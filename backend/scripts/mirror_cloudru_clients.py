#!/usr/bin/env python3
"""Operator: mirror active subscribers' VLESS UUIDs onto the Cloud.ru bare-xray entry.

Cloud.ru (provider_kind='bare_xray', 85.208.87.187) has no 3x-ui panel, so the bot's
panel CRUD never reaches it. This script is the desired-state reconciliation for that
server: it reads the active subscribers from the DB, computes their deterministic tcp
UUIDs (the same UUIDs the /sub/ link emits for surface='happ'), and rewrites Cloud.ru's
xray inbound `clients` to EXACTLY that set. Effect:

  * a Happ user's /sub/ link works (their UUID is present on Cloud.ru);
  * a lapsed user's UUID is removed on the next run -> enforcement on the Happ leg.

Idempotent. xray is restarted ONLY when the client set actually changes (diff-skip ->
no connection drop on quiet cycles). The script owns the Cloud.ru inbound client list
wholesale (any manual/test UUID not in the active set is removed); re-add such UUIDs as
real subscriber rows if they must persist.

Runs in the production container with DATABASE_URL + CLOUDRU_SSH_HOST/_USER/_KEY env.
Wired into reprovision_active.py so it shares the ~10-30 min self-heal cadence.

The pure helpers (desired_client_uuids / build_desired_clients / rewrite_clients) are
unit-tested without SSH; paramiko is imported lazily inside _ssh_apply so the module
imports cleanly in the test environment.
"""
from __future__ import annotations

import asyncio
import copy
import json
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from rebuild_panel_clients import _vless_uuid_for_transport  # noqa: E402

CLOUDRU_CONFIG_DEFAULT = "/usr/local/etc/xray/config.json"


# ---------------------------------------------------------------------------
# Pure helpers (unit-tested)
# ---------------------------------------------------------------------------


def desired_client_uuids(internal_user_ids: list[str]) -> list[str]:
    """Deterministic tcp VLESS UUID per active user, deduped + sorted (stable output
    so the diff-skip is meaningful across runs)."""
    return sorted({_vless_uuid_for_transport(uid, "tcp") for uid in internal_user_ids})


def build_desired_clients(uuids: list[str]) -> list[dict]:
    """Each active user becomes a minimal VLESS client on Cloud.ru: id + vision flow.
    The Happ leg doesn't track traffic or per-device limits here (the foreign fleet
    enforces limitIp on the Karing leg)."""
    return [{"id": u, "flow": "xtls-rprx-vision"} for u in uuids]


def rewrite_clients(config: dict, desired: list[dict]) -> tuple[dict, bool]:
    """Return (new_config, changed). Sets inbounds[0].settings.clients = desired while
    preserving every other field (Reality keys, routing, outbounds, …). changed is True
    only when the client list actually differs (drives the restart-skip)."""
    new_config = copy.deepcopy(config)
    inbounds = new_config.get("inbounds") or []
    if not inbounds:
        return new_config, False  # nothing to write to — leave untouched
    settings = inbounds[0].get("settings")
    if isinstance(settings, str):
        settings = json.loads(settings) if settings else {}
    if not isinstance(settings, dict):
        settings = {}
    if settings.get("clients", []) == desired:
        return new_config, False
    settings["clients"] = desired
    inbounds[0]["settings"] = settings
    return new_config, True


# ---------------------------------------------------------------------------
# SSH I/O layer (paramiko imported lazily)
# ---------------------------------------------------------------------------


def _ssh_apply(host: str, user: str, key: str, config_path: str, desired: list[dict]) -> dict:
    """Synchronous paramiko: read Cloud.ru config, rewrite clients, write + restart if
    changed. Returns a small summary dict."""
    import paramiko  # lazy import — keeps the module importable without paramiko (tests)

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        hostname=host, username=user, key_filename=key, look_for_keys=False,
        allow_agent=False, timeout=20,
    )
    try:
        # Read current config (root-readable 644).
        _, stdout, _ = client.exec_command(f"cat {config_path}", timeout=20)
        raw = stdout.read().decode("utf-8")
        if not raw.strip():
            return {"changed": False, "error": "current config empty"}
        config = json.loads(raw)

        new_config, changed = rewrite_clients(config, desired)
        if not changed:
            return {"changed": False, "clients": len(desired)}

        # Write new config to a romtan-writable temp, then sudo install + restart.
        tmp = "/tmp/cloudru.config.new.json"
        sftp = client.open_sftp()
        try:
            with sftp.file(tmp, "w") as f:
                f.write(json.dumps(new_config, indent=2, ensure_ascii=False))
        finally:
            sftp.close()
        cmd = (
            f"sudo cp {tmp} {config_path} && "
            f"sudo chown root:root {config_path} && "
            f"sudo chmod 644 {config_path} && "
            f"sudo systemctl restart xray && rm -f {tmp}"
        )
        _, stdout, stderr = client.exec_command(cmd, timeout=30)
        rc = stdout.channel.recv_exit_status()
        err = stderr.read().decode("utf-8").strip()
        if rc != 0:
            return {"changed": True, "clients": len(desired), "error": f"install rc={rc}: {err}"}
        return {"changed": True, "clients": len(desired)}
    finally:
        client.close()


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------


async def run(pool: asyncpg.Pool) -> dict:
    rows = await pool.fetch(
        """SELECT i.internal_user_id
           FROM user_identities i
           JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
           WHERE s.state_label = 'active' AND s.keys_deleted_at IS NULL"""
    )
    internal_user_ids = [r["internal_user_id"] for r in rows]
    desired = build_desired_clients(desired_client_uuids(internal_user_ids))

    host = os.environ.get("CLOUDRU_SSH_HOST", "")
    user = os.environ.get("CLOUDRU_SSH_USER", "romtan")
    key = os.environ.get("CLOUDRU_SSH_KEY", "")
    config_path = os.environ.get("CLOUDRU_CONFIG", CLOUDRU_CONFIG_DEFAULT)

    if not host or not key:
        print(f"mirror_cloudru: skip (CLOUDRU_SSH_HOST/CLOUDRU_SSH_KEY not set); "
              f"would set {len(desired)} clients")
        return {"skipped": True, "clients": len(desired)}

    summary = await asyncio.to_thread(_ssh_apply, host, user, key, config_path, desired)
    print(f"mirror_cloudru: users={len(internal_user_ids)} clients={len(desired)} -> {summary}")
    return summary


if __name__ == "__main__":
    async def _main() -> None:
        if not os.environ.get("DATABASE_URL"):
            print("ERROR: DATABASE_URL not set", file=sys.stderr)
            sys.exit(1)
        pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        try:
            await run(pool)
        finally:
            await pool.close()

    asyncio.run(_main())
