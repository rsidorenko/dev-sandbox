"""Операторский bootstrap и ограниченный цикл измерений VPN без ключей покупателей."""

import argparse
import asyncio
import inspect
import json
import logging
import os
import re
import secrets
import shlex
from datetime import UTC, datetime

import asyncpg

from app.application.connection_check import _resolve_host_ips
from app.application.subscription_grant import grant_subscription_days
from app.issuance.xui_vless_provider import (
    XuiVlessProvider,
    _build_vless_link,
    _email_from_internal,
    _vless_uuid_with_route,
)
from app.observability import vpn_quality_probe as probe

LOCATION = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")


def validate_location(location):
    if not LOCATION.fullmatch(location):
        raise ValueError("invalid_location")


async def bootstrap(pool, *, location, entry_server_id, apply=False):
    """Один бесплатный grant; повтор/crash не создаёт второго аккаунта и не продлевает срок."""
    validate_location(location)
    async with pool.acquire() as conn:
        async with conn.transaction():
            if apply:
                await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", "vpn-quality:" + location)
            existing = await conn.fetchrow("SELECT * FROM vpn_quality_accounts WHERE location=$1", location)
            if existing:
                if existing["entry_server_id"] != entry_server_id:
                    raise ValueError("location_entry_conflict")
                return dict(existing)
            entry = await conn.fetchval(
                "SELECT id FROM vpn_servers WHERE id=$1 AND is_active AND provider_kind='bare_xray'", entry_server_id
            )
            if entry is None:
                raise ValueError("invalid_entry")
            if not apply:
                return None
            await conn.execute(
                "CREATE SEQUENCE IF NOT EXISTS web_user_id_seq START WITH -1 INCREMENT BY -1 MINVALUE -999999999"
            )
            telegram_id = await conn.fetchval("SELECT nextval('web_user_id_seq')")
            identity = "web_" + secrets.token_hex(12)
            await conn.execute(
                "INSERT INTO user_identities(telegram_user_id,internal_user_id,is_internal) VALUES($1,$2,TRUE)",
                telegram_id,
                identity,
            )
            await conn.execute(
                "INSERT INTO vpn_quality_accounts(location,internal_user_id,entry_server_id) VALUES($1,$2,$3)",
                location,
                identity,
                entry_server_id,
            )
            await conn.execute(
                "INSERT INTO analytics_internal_audit(internal_user_id,actor,reason,old_value,new_value) "
                "VALUES($1,$2,'Служебный аккаунт измерителя VPN',FALSE,TRUE)",
                identity,
                "vpn-quality:" + location,
            )
            # Все SQL штатного grant выполняются на том же connection; сети внутри транзакции нет.
            await grant_subscription_days(
                pool=conn, vless_provider=None, internal_user_id=identity, days=365, actor="vpn-quality:" + location
            )
            return {
                "location": location,
                "internal_user_id": identity,
                "entry_server_id": entry_server_id,
                "enabled": False,
            }


async def account_state(pool, location):
    return await pool.fetchrow(
        """SELECT q.*,i.is_internal,i.telegram_user_id,i.vless_rotation_pending,s.state_label,
        s.active_until_utc,s.keys_deleted_at,s.keys_deactivated_at FROM vpn_quality_accounts q
        JOIN user_identities i USING(internal_user_id)
        LEFT JOIN subscription_snapshots s USING(internal_user_id) WHERE q.location=$1""",
        location,
    )


def ineligible_reason(account, *, require_enabled=True):
    if account is None:
        return "unconfigured"
    if require_enabled and not account["enabled"]:
        return "disabled"
    if not account["is_internal"] or account["telegram_user_id"] >= 0:
        return "invalid_identity"
    if account["vless_rotation_pending"]:
        return "rotation_pending"
    until = account["active_until_utc"]
    if account["state_label"] != "active" or until is None or until <= datetime.now(UTC):
        return "expired"
    if account["keys_deleted_at"] or account["keys_deactivated_at"]:
        return "inactive_keys"
    return ""


async def provision(pool, provider, location):
    account = await account_state(pool, location)
    if ineligible_reason(account, require_enabled=False):
        raise ValueError("ineligible_account")
    identity = account["internal_user_id"]
    await provider.sync_user_access(internal_user_id=identity)
    clients = await provider._get_bare_xray_clients()
    verified = 0
    for client in clients:
        transport = client.server_config.transport_type
        expected = (
            await provider._uuid_for_transport(identity, transport),
            "xtls-rprx-vision" if transport == "tcp" else "",
        )
        accounts = await client.list_user_accounts()
        if accounts.get(_email_from_internal(identity, transport_type=transport)) != expected:
            raise ValueError("provisioning_incomplete")
        verified += 1
    if not verified or verified != len(provider._bare_xray_configs):
        raise ValueError("provisioning_incomplete")
    return verified


async def profiles(pool, provider, identity):
    clients = await provider._get_bare_xray_clients()
    rows = await pool.fetch("SELECT id,connection_check_host FROM vpn_servers WHERE is_active")
    hosts = {r["id"]: r["connection_check_host"] for r in rows}
    resolved = {}
    for host in set(hosts.values()) - {None, ""}:
        resolved[host] = await _resolve_host_ips(host)
    complete_map = bool(hosts) and all(host and resolved.get(host) for host in hosts.values())
    network_ips = sorted({ip for ips in resolved.values() for ip in ips}) if complete_map else []
    items = []
    for config in provider._bare_xray_configs:
        identity_uuid = await provider._uuid_for_transport(identity, config.transport_type)
        variants = [(0, config.label, identity_uuid)]
        if config.auto_selection_route and config.auto_selection_label and config.auto_selection_country_flag:
            variants.append(
                (
                    config.auto_selection_route,
                    config.auto_selection_label,
                    _vless_uuid_with_route(identity_uuid, config.auto_selection_route),
                )
            )
        for route, label, user_uuid in variants:
            try:
                outbound = probe.outbound_from_link(_build_vless_link(config, user_uuid))
            except (ValueError, TypeError, KeyError):
                outbound = None
            expected = network_ips if route else sorted(resolved.get(hosts.get(config.server_id), ()))
            items.append(
                {
                    "server_id": config.server_id,
                    "route": route,
                    "label": label[:200],
                    "outbound": outbound,
                    "expected_ips": expected,
                }
            )
    if not 1 <= len(items) <= 32:
        raise ValueError("profile_count_invalid")
    return clients, items


def remote_probe(client, items):
    """Свежий SSH-клиент цикла закрывается; удалённый watchdog не зависит от соединения."""
    ssh = client._connect()
    channel = None
    try:
        stdin, stdout, _ = ssh.exec_command("python3 -c " + shlex.quote(inspect.getsource(probe)), timeout=200)
        channel = stdout.channel
        stdin.write(json.dumps(items))
        stdin.flush()
        stdin.channel.shutdown_write()
        raw = stdout.read(65537)
        if len(raw) > 65536:
            raise ValueError("invalid_measurement")
        return [json.loads(line) for line in raw.decode().splitlines()], stdout.channel.recv_exit_status()
    finally:
        if channel is not None:
            channel.close()
        ssh.close()


def sanitize_sample(row):
    """Повторная проверка границы SSH перед сохранением; исключения/тела не сохраняются."""
    if row.get("result") not in probe.RESULTS:
        raise ValueError("invalid_measurement")
    result = {"result": row["result"]}
    for key in ("http_code", "tls_ms", "ttfb_ms", "total_ms", "response_bytes"):
        value = row.get(key)
        maximum = 599 if key == "http_code" else 4096 if key == "response_bytes" else 15000
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int | float) or not 0 <= value <= maximum
        ):
            raise ValueError("invalid_measurement")
        result[key] = value
    return result


async def collect(pool, provider, location):
    validate_location(location)
    async with pool.acquire() as lock:
        if not await lock.fetchval("SELECT pg_try_advisory_lock(hashtext($1))", "vpn-quality:" + location):
            return "already_running"
        run_id = None
        try:
            account = await account_state(pool, location)
            if account is None:
                return "unconfigured"
            reason = ineligible_reason(account)
            run_id = await pool.fetchval(
                "INSERT INTO vpn_quality_runs(location,status) VALUES($1,'running') RETURNING id", location
            )
            if reason:
                await pool.execute(
                    "UPDATE vpn_quality_runs SET status='skipped',reason=$2,finished_at=NOW() WHERE id=$1",
                    run_id,
                    reason,
                )
                return reason
            clients, items = await profiles(pool, provider, account["internal_user_id"])
            entry = next(c for c in clients if c.server_id == account["entry_server_id"])
            rows, rc = await asyncio.to_thread(remote_probe, entry, items)
            expected = {(i["server_id"], i["route"]): i for i in items}
            seen = set()
            for row in rows:
                if row.get("measurement_error"):
                    continue
                key = (row.get("server_id"), row.get("route"))
                if key not in expected or key in seen:
                    raise ValueError("invalid_measurement")
                seen.add(key)
                sample = sanitize_sample(row)
                await pool.execute(
                    """INSERT INTO vpn_quality_samples
                    (run_id,server_id,route,label,result,http_code,tls_ms,ttfb_ms,total_ms,response_bytes)
                    VALUES($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)""",
                    run_id,
                    *key,
                    expected[key]["label"],
                    *(sample[k] for k in ["result", "http_code", "tls_ms", "ttfb_ms", "total_ms", "response_bytes"]),
                )
            status = "complete" if rc == 0 and seen == set(expected) else "measurement_error"
            await pool.execute("UPDATE vpn_quality_runs SET status=$2,finished_at=NOW() WHERE id=$1", run_id, status)
            return status
        except Exception:
            if run_id is not None:
                await pool.execute(
                    "UPDATE vpn_quality_runs SET status='measurement_error',reason='collector_failed',finished_at=NOW() WHERE id=$1",
                    run_id,
                )
            return "measurement_error"
        finally:
            await lock.execute("SELECT pg_advisory_unlock(hashtext($1))", "vpn-quality:" + location)


async def run(args):
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3, command_timeout=15)
    provider = XuiVlessProvider(pool)
    try:
        if args.bootstrap:
            account = await bootstrap(
                pool, location=args.location, entry_server_id=args.entry_server_id, apply=args.apply
            )
            if not args.apply:
                return {"status": "exists" if account else "would_create"}
            count = await provision(pool, provider, args.location)
            if args.enable:
                await pool.execute("UPDATE vpn_quality_accounts SET enabled=TRUE WHERE location=$1", args.location)
            return {"status": "provisioned", "inbounds_verified": count}
        return {"status": await collect(pool, provider, args.location)}
    finally:
        for client in provider._bare_xray_clients or ():
            if client._ssh is not None:
                client._ssh.close()
        await pool.close()


def main():
    parser = argparse.ArgumentParser(description="Ограниченная проверка VPN и служебный аккаунт")
    parser.add_argument("--location", required=True)
    parser.add_argument("--bootstrap", action="store_true")
    parser.add_argument("--entry-server-id", type=int)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--enable", action="store_true")
    args = parser.parse_args()
    if (
        (args.bootstrap and args.entry_server_id is None)
        or ((args.apply or args.enable) and not args.bootstrap)
        or (args.enable and not args.apply)
    ):
        parser.error("bootstrap требует entry-server-id; enable требует bootstrap --apply")
    logging.disable(logging.CRITICAL)
    try:
        result = asyncio.run(run(args))
    except Exception:
        result = {"status": "operator_error"}
    print(json.dumps(result))
    return 1 if result["status"] in {"operator_error", "measurement_error"} else 0


if __name__ == "__main__":
    raise SystemExit(main())
