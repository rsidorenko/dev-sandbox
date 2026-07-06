"""Real VLESS provider backed by 3x-ui panels.

Implements :class:`VlessProviderPort` — creates/reads/disables/deletes VLESS users
across all active VPN servers registered in the ``vpn_servers`` table.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import secrets
import sqlite3
import string
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from app.issuance.vless_provider import (
    VlessProviderOutcome,
    VlessProviderPort,
    VlessProviderResult,
    VlessServerConfig,
    VlessUserConfig,
)
from app.issuance.xray_api_client import DEFAULT_API_PORT, XrayApiClient
from app.issuance.xui_client import (
    REALITY_TCP_FLOW,
    XuiApiClient,
    XuiClientResult,
    XuiOutcome,
    XuiServerConfig,
)
from app.security.field_encryption import decrypt_field
from app.shared.site_url import get_site_base_url

_CACHE_TTL_SECONDS = 600  # 10 minutes — panel-client (server list) cache
# User-config cache (get_user_config / /sub/). The probe fans out one panel call per
# active server (~14), so a cold miss costs ~2s. Serve recent entries instantly and
# refresh in the background so repeat /sub/ imports stop paying that cost every time.
# Probe logic and result semantics are unchanged — only the cache timing around it.
_USER_CONFIG_FRESH_SECONDS = 120  # served without triggering a refresh
_USER_CONFIG_STALE_SECONDS = 3600  # served (background-refreshed) up to this age; block beyond
_CONFIG_CACHE_MAX_ENTRIES = 10000
_SUBSCRIPTION_TOKEN_TTL_DAYS = int(os.environ.get("SUBSCRIPTION_TOKEN_TTL_DAYS", "90"))

_LOGGER = logging.getLogger(__name__)

_DEFAULT_EXPIRY_DAYS = 365
_TRIAL_DEVICE_LIMIT = int(os.environ.get("TRIAL_DEVICE_LIMIT", "5"))


def _generate_subscription_token() -> str:
    return secrets.token_urlsafe(16)


def _web_sub_url(token: str) -> str:
    return f"{get_site_base_url()}/sub/{token}"


async def _ensure_subscription_token(pool: asyncpg.Pool, internal_user_id: str) -> str:
    row = await pool.fetchrow(
        "SELECT subscription_token, subscription_token_expires_at FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    if row and row["subscription_token"]:
        expires = row.get("subscription_token_expires_at")
        if expires is None or expires > datetime.now(UTC):
            return row["subscription_token"]
    # Token missing or expired — generate fresh one
    token = _generate_subscription_token()
    expires_at = datetime.now(UTC) + timedelta(days=_SUBSCRIPTION_TOKEN_TTL_DAYS)
    await pool.execute(
        "UPDATE user_identities SET subscription_token = $1, subscription_token_expires_at = $2 WHERE internal_user_id = $3",
        token,
        expires_at,
        internal_user_id,
    )
    return token


def _user_uuid_from_internal(internal_user_id: str) -> str:
    """Deterministic UUID v5 derived from internal user ID (stable, not guessable)."""
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, f"vpn.bravada.internal.{internal_user_id}"))


def _vless_uuid_for_transport(internal_user_id: str, transport_type: str) -> str:
    """Deterministic VLESS uuid per (user, transport).

    3x-ui v3 keys its ``clients`` table by uuid (one row per uuid). When the SAME
    uuid is provisioned on several inbounds of one panel (1.0 tcp / 2.0 cdn / 3.0
    xhttp), v3 regenerates ``client_inbounds`` mapping that uuid to only ~one
    inbound -> xray serves the user on fewer inbounds than JSON promises ("8 vs 10
    keys"). Giving each transport its own uuid makes every inbound's client a unique
    uuid -> unique clients row -> correct per-inbound mapping. Stable (uuid5), no
    DB storage needed.
    """
    return str(
        uuid.uuid5(
            uuid.NAMESPACE_DNS,
            f"vpn.bravada.internal.{internal_user_id}.{transport_type}",
        )
    )


async def _get_or_create_vless_uuid(pool: asyncpg.Pool, internal_user_id: str) -> str:
    """Atomically get or create VLESS UUID. First writer wins via COALESCE."""
    new_uuid = str(uuid.uuid4())
    row = await pool.fetchrow(
        "UPDATE user_identities SET vless_uuid = COALESCE(vless_uuid, $2) "
        "WHERE internal_user_id = $1 RETURNING vless_uuid",
        internal_user_id,
        new_uuid,
    )
    return row["vless_uuid"]


def _email_from_internal(internal_user_id: str, *, transport_type: str = "tcp") -> str:
    prefix = {"xhttp": "x-"}.get(transport_type, "")
    return f"{prefix}user-{internal_user_id[:16]}"


def _h1_client_token(n: int = 16) -> str:
    """Random lowercase-alnum token (matches the h1-fork panel's auth/password/subId format)."""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))


def build_h1_client(*, user_uuid: str, email: str, enable: bool = True) -> dict:
    """Client object for the h1cloud 3x-ui h1-fork. The fork requires its custom schema
    (auth/password/subId/security, numeric tgId, NO flow) — without it the fork's xray config
    regen doesn't activate the client, and the link gets 'server closed connection'.

    ``enable`` mirrors the subscription state — expired/in-grace users must be disabled on
    h1cloud too (the fork uses expiryTime=0, so without an explicit enable=False an expired
    user's h1cloud key stays live = infinite subscription leak)."""
    return {
        "id": user_uuid,
        "email": email,
        "enable": enable,
        "expiryTime": 0,
        "limitIp": 0,
        "totalGB": 0,
        "tgId": 0,
        "subId": _h1_client_token(),
        "auth": _h1_client_token(),
        "password": _h1_client_token(),
        "security": "auto",
        "comment": "",
        "reset": 0,
    }


def _expiry_timestamp(days: int = _DEFAULT_EXPIRY_DAYS) -> int:
    """Unix timestamp in milliseconds for expiry."""
    future = datetime.now(UTC) + timedelta(days=days)
    return int(future.timestamp() * 1000)


def _expiry_ms_for_user(active_until: datetime | None, now: datetime | None = None) -> int:
    """Expiry timestamp (ms) reflecting a subscription's real state.

    Active -> active_until (>=1 day floor). **Expired** (active_until in the past) -> the past
    active_until itself, so the panel shows 'expired' rather than a misleading +365d. No date ->
    minimal default. Used by reconcile so expired/disabled clients display their real expiry."""
    now = now or datetime.now(UTC)
    if active_until and active_until > now:
        return _expiry_timestamp(days=max(1, (active_until - now).days))
    if active_until:  # expired — past active_until (panel shows expired, NOT +365)
        return int(active_until.timestamp() * 1000)
    return _expiry_timestamp(days=1)


def _sync_h1_tables(conn: sqlite3.Connection, desired: dict[str, dict], inbound_id: int = 1) -> bool:
    """Mirror desired bot clients into the h1-fork clients/client_inbounds/client_traffics tables
    + inbounds.settings.clients JSON. The h1-fork (3x-ui v3 on Pterodactyl) reads VLESS clients
    from these tables (NOT from settings.clients JSON); the bot's /update writes only the JSON, so a
    direct table-sync is the only way bot clients get served.

    Scoped to clients whose email starts with ``x-user-`` (bot clients) — manual/test clients are
    NEVER touched (a sync bug cannot break a manually-added working key). Idempotent. Returns True
    if any row changed (caller uploads + restarts xray only then).

    ``desired`` maps email -> {"uuid", "enable": bool, "expiry": ms-since-epoch}.
    """
    cur = conn.cursor()
    now_ms = int(time.time() * 1000)

    def tok(n: int = 16) -> str:
        return "".join(secrets.choice(string.ascii_lowercase + string.digits) for _ in range(n))

    cur.execute("SELECT id, email, uuid, enable, expiry_time FROM clients WHERE email LIKE 'x-user-%'")
    current = {
        r[1]: {"id": r[0], "uuid": r[2], "enable": int(r[3] or 0), "expiry": int(r[4] or 0)} for r in cur.fetchall()
    }

    changed = False
    for email, spec in desired.items():
        en = 1 if spec["enable"] else 0
        if email not in current:
            cur.execute(
                """INSERT INTO clients (email, sub_id, uuid, password, auth, flow, security, reverse,
                   limit_ip, total_gb, expiry_time, enable, tg_id, group_name, comment, reset,
                   created_at, updated_at, wg_private_key, wg_public_key, wg_allowed_ips,
                   wg_pre_shared_key, wg_keep_alive)
                   VALUES (?,?,?,?,?, '', 'auto','', 0,0, ?,?, 0,'','',0, ?,?, '','','','', 0)""",
                (email, tok(), spec["uuid"], tok(), tok(), spec["expiry"], en, now_ms, now_ms),
            )
            cid = cur.execute("SELECT last_insert_rowid()").fetchone()[0]
            cur.execute(
                "INSERT OR IGNORE INTO client_inbounds (client_id, inbound_id, flow_override, created_at) VALUES (?,?,?,?)",
                (cid, inbound_id, "", now_ms),
            )
            cur.execute(
                """INSERT INTO client_traffics (inbound_id, enable, email, up, down, expiry_time, total, reset, last_online)
                   VALUES (?,?,?,0,0,?,0,0,0)""",
                (inbound_id, en, email, spec["expiry"]),
            )
            changed = True
        else:
            c = current[email]
            if c["enable"] != en or c["expiry"] != spec["expiry"]:
                cur.execute(
                    "UPDATE clients SET enable=?, expiry_time=?, updated_at=? WHERE id=?",
                    (en, spec["expiry"], now_ms, c["id"]),
                )
                cur.execute(
                    "UPDATE client_traffics SET enable=?, expiry_time=? WHERE email=? AND inbound_id=?",
                    (en, spec["expiry"], email, inbound_id),
                )
                changed = True
    for email, c in current.items():
        if email not in desired:
            cur.execute("DELETE FROM client_inbounds WHERE client_id=?", (c["id"],))
            cur.execute("DELETE FROM client_traffics WHERE email=? AND inbound_id=?", (email, inbound_id))
            cur.execute("DELETE FROM clients WHERE id=?", (c["id"],))
            changed = True

    if changed:
        row = cur.execute("SELECT settings FROM inbounds WHERE id=?", (inbound_id,)).fetchone()
        se = json.loads(row[0]) if row and row[0] else {}
        clients = se.setdefault("clients", [])
        by_email = {cl.get("email"): cl for cl in clients}
        for email, spec in desired.items():
            if email in by_email:
                by_email[email].update({"enable": spec["enable"], "expiryTime": spec["expiry"], "updated_at": now_ms})
            else:
                clients.append(
                    {
                        "id": spec["uuid"],
                        "email": email,
                        "enable": spec["enable"],
                        "expiryTime": spec["expiry"],
                        "limitIp": 0,
                        "totalGB": 0,
                        "tgId": 0,
                        "subId": tok(),
                        "auth": tok(),
                        "password": tok(),
                        "security": "auto",
                        "comment": "",
                        "reset": 0,
                        "created_at": now_ms,
                        "updated_at": now_ms,
                        "flow": "",
                    }
                )
        se["clients"] = [
            cl
            for cl in clients
            if not (str(cl.get("email", "")).startswith("x-user-") and cl.get("email") not in desired)
        ]
        cur.execute("UPDATE inbounds SET settings=? WHERE id=?", (json.dumps(se), inbound_id))
    conn.commit()
    return changed


def _build_vless_link(
    server: XuiServerConfig,
    user_uuid: str,
) -> str:
    """Build a vless:// URI for a specific server."""
    # Custom template (e.g. CDN-fronted xhttp with obfuscation the per-transport branches can't
    # assemble). The row stores a full vless:// with a literal {UUID} placeholder.
    if server.link_template:
        return server.link_template.replace("{UUID}", user_uuid)

    host = server.server_host
    port = server.server_port
    label = f"{server.country_flag} {server.label}"
    fp = server.reality_fp

    if server.transport_type == "xhttp":
        path = server.ws_path.strip("/")
        return (
            f"vless://{user_uuid}@{host}:{port}"
            f"?type=xhttp&security=reality&path=%2F{path}"
            f"&pbk={server.reality_pbk}&fp={fp}&sni={server.reality_sni}"
            f"&sid={server.reality_sid}&spx=%2F"
            f"#{label}"
        )

    if server.transport_type == "grpc":
        return (
            f"vless://{user_uuid}@{host}:{port}"
            f"?type=grpc&security=reality"
            f"&pbk={server.reality_pbk}&fp={fp}&sni={server.reality_sni}"
            f"&sid={server.reality_sid}&spx=%2F&flow=&serviceName=&authority="
            f"#{label}"
        )

    if server.transport_type == "ws":
        path = server.ws_path.strip("/")
        ws_host = server.tls_sni or host
        return (
            f"vless://{user_uuid}@{ws_host}:{port}"
            f"?type=ws&security=tls&path=%2F{path}"
            f"&host={ws_host}&fp={fp}&sni={ws_host}"
            f"#{label}"
        )

    # Default: TCP + Reality. 3x-ui serves reality+tcp clients with the
    # xtls-rprx-vision flow (the panel's reality+tcp default — the bot's empty-flow
    # upsert does NOT override it on v3 panels). The link MUST carry the matching
    # flow or the reality handshake silently hangs. A link/server flow mismatch is
    # exactly what took every 1.0 (tcp/Reality) key down across all panels on
    # 2026-06-22: the link emitted no flow while the server kept vision. (The
    # 2026-06-14 "revert vision — broke tcp/Reality" was the inverse mismatch: the
    # server was no-flow then. Vision is the only valid flow for tcp+Reality and
    # also resists active DPI probing — see REALITY_TCP_FLOW.)
    flow_seg = f"&flow={REALITY_TCP_FLOW}"
    return (
        f"vless://{user_uuid}@{host}:{port}"
        f"?type=tcp&security=reality"
        f"&pbk={server.reality_pbk}&fp={fp}&sni={server.reality_sni}"
        f"&sid={server.reality_sid}&spx=%2F{flow_seg}"
        f"#{label}"
    )


def _resolve_panel_password(row: asyncpg.Record) -> str:
    """Resolve panel password: encrypted column preferred, plaintext fallback with warning."""
    encrypted = row.get("encrypted_password", "")
    if encrypted:
        return decrypt_field(encrypted)
    plain = row.get("panel_password", "")
    if plain:
        _LOGGER.critical(
            "SECURITY: server id=%s has plaintext panel_password — "
            "run scripts/migrate_encrypt_passwords.py to encrypt and clear",
            row.get("id"),
        )
    return plain


async def _load_server_configs(pool: asyncpg.Pool) -> tuple[XuiServerConfig, ...]:
    rows = await pool.fetch(
        """SELECT id, label, country_code, country_flag, server_host, server_port,
                  ws_path, tls_sni, panel_url, panel_username, panel_password,
                  COALESCE(encrypted_password, '') AS encrypted_password,
                  inbound_id, reality_pbk, reality_sid, reality_sni,
                  COALESCE(transport_type, 'tcp') AS transport_type,
                  COALESCE(api_token, '') AS api_token,
                  COALESCE(reality_fp, 'chrome') AS reality_fp,
                  COALESCE(link_template, '') AS link_template,
                  COALESCE(provider_kind, 'xui') AS provider_kind,
                  COALESCE(xray_inbound_tag, '') AS xray_inbound_tag,
                  COALESCE(client_surface, 'default') AS client_surface,
                  COALESCE(panel_flavor, 'standard') AS panel_flavor,
                  COALESCE(ssh_user, '') AS ssh_user,
                  COALESCE(ssh_key_path, '') AS ssh_key_path
           FROM vpn_servers WHERE is_active = TRUE ORDER BY id"""
    )
    return tuple(
        XuiServerConfig(
            server_id=r["id"],
            label=r["label"],
            country_code=r["country_code"],
            country_flag=r["country_flag"],
            server_host=r["server_host"],
            server_port=r["server_port"],
            ws_path=r["ws_path"],
            tls_sni=r["tls_sni"],
            panel_url=r["panel_url"],
            panel_username=r["panel_username"],
            panel_password=_resolve_panel_password(r),
            inbound_id=r["inbound_id"],
            reality_pbk=r["reality_pbk"],
            reality_sid=r["reality_sid"],
            reality_sni=r["reality_sni"],
            reality_fp=r["reality_fp"],
            transport_type=r["transport_type"],
            api_token=r["api_token"],
            link_template=r["link_template"],
            provider_kind=r["provider_kind"],
            xray_inbound_tag=r["xray_inbound_tag"],
            client_surface=r["client_surface"],
            panel_flavor=r["panel_flavor"],
            ssh_user=r["ssh_user"],
            ssh_key_path=r["ssh_key_path"],
        )
        for r in rows
    )


async def _run_sequential_per_panel(
    clients: list[XuiApiClient],
    fn,
) -> list[tuple[XuiApiClient, object]]:
    """Run *fn(client)* for each client, sequential within the same panel.

    Different 3x-ui panels can run in parallel, but inbounds on the **same**
    panel must be sequential — concurrent ``addClient`` calls to one panel
    cause a read-modify-write race in 3x-ui's internal store, silently
    dropping clients.
    """
    from collections import OrderedDict

    by_panel: OrderedDict[str, list[XuiApiClient]] = OrderedDict()
    for c in clients:
        key = c.server_config.panel_url
        by_panel.setdefault(key, []).append(c)

    results: list[tuple[XuiApiClient, object]] = []

    async def _run_panel(panel_clients: list[XuiApiClient]) -> None:
        for c in panel_clients:
            r = await fn(c)
            results.append((c, r))

    await asyncio.gather(*[_run_panel(pcs) for pcs in by_panel.values()])
    return results


class XuiVlessProvider(VlessProviderPort):
    """Real VLESS provider: manages users across all active 3x-ui panels.

    Caches XuiApiClient instances with a TTL to avoid recreating HTTP clients
    on every operation.  Write operations run sequential-per-panel (parallel
    across different panels) to avoid 3x-ui read-modify-write races.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._clients: list[XuiApiClient] | None = None
        self._clients_ts: float = 0.0
        # bare_xray server configs (RU-relay entries like Cloud.ru) populated alongside
        # _clients by _get_clients. No XuiApiClient — these have no panel.
        self._bare_xray_configs: tuple[XuiServerConfig, ...] = ()
        # Cached XrayApiClient instances (one per bare_xray inbound), built from env creds.
        self._bare_xray_clients: tuple[XrayApiClient, ...] | None = None
        self._plaintext_warning_logged = False
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._config_cache: dict[str, tuple[float, VlessProviderResult, int]] = {}
        # Epoch is bumped on every invalidation so an in-flight (background) refresh
        # can detect that a mutation invalidated the entry and refuse to clobber it.
        self._cache_epoch: dict[str, int] = {}
        self._refreshing: set[str] = set()
        self._refresh_tasks: set[asyncio.Task] = set()

    async def _check_plaintext_passwords(self) -> None:
        if self._plaintext_warning_logged:
            return
        row = await self._pool.fetchrow(
            "SELECT COUNT(*) AS cnt FROM vpn_servers WHERE is_active = TRUE AND panel_password != ''"
        )
        if row and row["cnt"] > 0:
            _LOGGER.critical(
                "SECURITY: %d active vpn_servers have non-empty panel_password — "
                "run scripts/migrate_encrypt_passwords.py to encrypt and clear plaintext",
                row["cnt"],
            )
        self._plaintext_warning_logged = True

    async def _get_clients(self) -> list[XuiApiClient]:
        if self._clients is not None and time.monotonic() - self._clients_ts < _CACHE_TTL_SECONDS:
            return self._clients
        await self._check_plaintext_passwords()
        # Close old clients before creating new ones
        if self._clients is not None:
            await self._close_clients()
        configs = await _load_server_configs(self._pool)
        # Only 'xui' servers get a panel client. 'bare_xray' servers (no panel) are kept
        # aside for link-building only — panel CRUD never touches them.
        self._clients = [XuiApiClient(c) for c in configs if c.provider_kind == "xui"]
        self._bare_xray_configs = tuple(c for c in configs if c.provider_kind == "bare_xray")
        # Invalidate the bare_xray gRPC client cache so new/changed rows take effect.
        if self._bare_xray_clients:
            for bx in self._bare_xray_clients:
                with contextlib.suppress(Exception):
                    await bx.aclose()
            self._bare_xray_clients = None
        self._clients_ts = time.monotonic()
        return self._clients

    async def _bare_xray_servers(self, internal_user_id: str) -> list[VlessServerConfig]:
        """surface='happ' links for each bare_xray (RU-relay) inbound — one per transport
        (tcp :443 + xhttp :8443 on Cloud.ru). Uses the per-transport deterministic UUID;
        the inbounds are kept in sync LIVE by _bare_xray_lifecycle (add on subscribe /
        remove on expire) + reconcile, so the link is functional for active subscribers.
        [] when no bare_xray servers are configured."""
        if not self._bare_xray_configs:
            return []
        return [
            VlessServerConfig(
                server_label=c.label,
                country_code=c.country_code,
                country_flag=c.country_flag,
                vless_link=_build_vless_link(c, _vless_uuid_for_transport(internal_user_id, c.transport_type)),
                surface=c.client_surface,
            )
            for c in self._bare_xray_configs
        ]

    async def _get_bare_xray_clients(self) -> tuple[XrayApiClient, ...]:
        """XrayApiClient per gRPC-managed bare_xray inbound. Per-row SSH creds (ssh_user /
        ssh_key_path from vpn_servers) override the global CLOUDRU_SSH_* env — so a future
        bare_xray host with a different key can coexist. Both current entries (Cloud.ru
        Frankfurt id=26/27 + cloud.ru Helsinki id=28/29) share the same key, so their rows
        leave ssh_user/ssh_key_path NULL and fall back to env. Cached.
        Returns () when there are no bare_xray rows, no tagged inbounds, or creds unset."""
        if self._bare_xray_clients is not None:
            return self._bare_xray_clients
        await self._get_clients()  # ensures _bare_xray_configs is populated
        env_ssh_user = os.environ.get("CLOUDRU_SSH_USER", "")
        env_ssh_key = os.environ.get("CLOUDRU_SSH_KEY", "")
        api_port = int(os.environ.get("CLOUDRU_XRAY_API_PORT", str(DEFAULT_API_PORT)))
        tagged = [c for c in self._bare_xray_configs if c.xray_inbound_tag]
        result: list[XrayApiClient] = []
        for c in tagged:
            ssh_user = c.ssh_user or env_ssh_user
            ssh_key = c.ssh_key_path or env_ssh_key
            if not (ssh_user and ssh_key):
                _LOGGER.warning(
                    "bare_xray server=%s has no SSH creds (neither row nor CLOUDRU_SSH_* env) — "
                    "live lifecycle disabled; reconcile will retry",
                    c.server_id,
                )
                continue
            result.append(XrayApiClient(c, ssh_user=ssh_user, ssh_key=ssh_key, api_port=api_port))
        self._bare_xray_clients = tuple(result)
        return self._bare_xray_clients

    async def _bare_xray_lifecycle(self, internal_user_id: str, action: str) -> None:
        """Add ('add') or remove ('remove') the user on every bare_xray inbound via xray gRPC
        (live, no restart). Non-fatal: a failure is logged and the periodic reconcile re-applies."""
        clients = await self._get_bare_xray_clients()
        for client in clients:
            transport = client.server_config.transport_type
            email = _email_from_internal(internal_user_id, transport_type=transport)
            try:
                if action == "add":
                    user_uuid = _vless_uuid_for_transport(internal_user_id, transport)
                    await client.add_client(user_uuid=user_uuid, email=email)
                else:
                    await client.remove_client(email=email)
            except Exception as exc:
                _LOGGER.warning(
                    "xray_api %s failed server=%s user=%s: %r",
                    action,
                    client.server_id,
                    internal_user_id[:8],
                    exc,
                )

    async def _close_clients(self) -> None:
        if self._clients is None:
            return
        for c in self._clients:
            with contextlib.suppress(Exception):
                await c.aclose()
        self._clients = None
        self._clients_ts = 0.0

    def _user_lock(self, internal_user_id: str) -> asyncio.Lock:
        lock = self._user_locks.get(internal_user_id)
        if lock is None:
            lock = asyncio.Lock()
            self._user_locks[internal_user_id] = lock
        if not lock.locked() and len(self._user_locks) > 1000:
            self._user_locks = {internal_user_id: lock}
        return lock

    async def aclose(self) -> None:
        """Close all cached HTTP clients. Call on shutdown."""
        for task in list(self._refresh_tasks):
            task.cancel()
        await self._close_clients()
        if self._bare_xray_clients:
            for bx in self._bare_xray_clients:
                with contextlib.suppress(Exception):
                    await bx.aclose()
            self._bare_xray_clients = None

    async def create_user(
        self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365
    ) -> VlessProviderResult:
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._create_user_unlocked(
                internal_user_id=internal_user_id, device_count=device_count, expiry_days=expiry_days
            )

    async def _create_user_unlocked(
        self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365
    ) -> VlessProviderResult:
        expiry = _expiry_timestamp(days=expiry_days)
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        clients = [c for c in clients if c.server_config.panel_flavor != "h1"]  # h1 deferred to reconcile
        if not clients:
            _LOGGER.warning("no active vpn servers configured")
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        async def _add_or_update(client: XuiApiClient) -> tuple[XuiClientResult, str]:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            # Distinct uuid per transport -> each inbound's client is a unique uuid
            # (fixes the 3x-ui v3 client_inbounds under-mapping on multi-inbound panels).
            user_uuid = _vless_uuid_for_transport(internal_user_id, client.server_config.transport_type)
            existing_uuid = await client.resolve_client_uuid(email=email)
            if existing_uuid is not None and existing_uuid != user_uuid:
                # Stale uuid on this email (e.g. pre-migration single uuid) -> remove so
                # add_client upserts the correct per-transport uuid cleanly.
                await client.delete_client(user_uuid=existing_uuid)
            result = await client.add_client(
                user_uuid=user_uuid,
                email=email,
                expiry_ts=expiry,
                enable=True,
                limit_ip=limit_ip,
            )
            return result, user_uuid

        raw = await _run_sequential_per_panel(clients, _add_or_update)

        successes: list[tuple[XuiApiClient, str]] = []
        for client, item in raw:
            if isinstance(item, Exception):
                _LOGGER.warning("xui add_client exception: %s", item)
                continue
            result, effective_uuid = item
            if result.outcome == XuiOutcome.SUCCESS:
                successes.append((client, effective_uuid))
            else:
                _LOGGER.warning(
                    "xui add_client failed server=%s outcome=%s",
                    client.server_id,
                    result.outcome,
                )

        if not successes:
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        servers = [
            VlessServerConfig(
                server_label=c.server_config.label,
                country_code=c.server_config.country_code,
                country_flag=c.server_config.country_flag,
                vless_link=_build_vless_link(c.server_config, uuid),
                surface=c.server_config.client_surface,
            )
            for c, uuid in successes
        ]
        # Append bare_xray RU-relay entries (e.g. Cloud.ru for Happ) — same set /sub/ serves.
        servers.extend(await self._bare_xray_servers(internal_user_id))
        # Live-provision the user on each bare_xray inbound (xray gRPC, no restart).
        await self._bare_xray_lifecycle(internal_user_id, "add")
        servers_tuple = tuple(servers)
        token = await _ensure_subscription_token(self._pool, internal_user_id)
        config = VlessUserConfig(
            user_uuid=_user_uuid_from_internal(internal_user_id),
            subscription_url=_web_sub_url(token),
            servers=servers_tuple,
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    def _invalidate_config_cache(self, internal_user_id: str) -> None:
        """Clear cached user config and bump its epoch so an in-flight background
        refresh can detect the invalidation and refuse to clobber the fresher state."""
        self._config_cache.pop(internal_user_id, None)
        self._cache_epoch[internal_user_id] = self._cache_epoch.get(internal_user_id, 0) + 1

    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult:
        """User config with serve-stale-while-revalidate caching.

        A fresh or recently-cached entry is returned instantly. An entry older than the
        fresh window is still returned instantly while a background panel probe refreshes
        it — so repeat /sub/ imports stop paying the ~2s (one-call-per-panel) probe on
        every cold hit. A missing or stale-beyond-limit entry falls back to a blocking
        probe. The probe itself and its result semantics are unchanged from before; only
        the cache timing around it differs.
        """
        now = time.monotonic()
        cached = self._config_cache.get(internal_user_id)
        if cached is not None:
            cached_ts, result, _epoch = cached
            age = now - cached_ts
            if age < _USER_CONFIG_STALE_SECONDS:
                if age >= _USER_CONFIG_FRESH_SECONDS:
                    self._schedule_refresh(internal_user_id)
                return result
        return await self._compute_user_config(internal_user_id)

    async def _probe_user_config(self, internal_user_id: str) -> VlessProviderResult | None:
        """Probe every active panel for the user's per-transport client uuid and build
        the link set. Returns the result, or ``None`` when there are no active servers
        (so the caller can decide the UNAVAILABLE vs NOT_FOUND semantics). Slow — one
        panel round-trip per active server, parallelised — so always call via the cache.
        """
        clients = await self._get_clients()
        if not clients:
            return None

        async def _resolve(client: XuiApiClient) -> tuple[XuiApiClient, str | None]:
            # Existence probe by email (per transport). The link uuid is whatever uuid
            # the panel actually holds for that email — works under both the legacy
            # single-uuid scheme and the per-transport scheme (and during migration).
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            uuid = await client.resolve_client_uuid(email=email)
            return client, uuid

        results = await asyncio.gather(*[_resolve(c) for c in clients], return_exceptions=True)

        servers: list[VlessServerConfig] = []
        for item in results:
            if isinstance(item, Exception):
                _LOGGER.debug("resolve_client_uuid exception: %s", item)
                continue
            client, uuid = item
            if uuid is not None:
                servers.append(
                    VlessServerConfig(
                        server_label=client.server_config.label,
                        country_code=client.server_config.country_code,
                        country_flag=client.server_config.country_flag,
                        vless_link=_build_vless_link(client.server_config, uuid),
                        surface=client.server_config.client_surface,
                    )
                )

        if not servers:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        # Append the bare_xray RU-relay entries (e.g. Cloud.ru for Happ) — they have no
        # panel to probe, so the link is built deterministically for any provisioned user.
        servers.extend(await self._bare_xray_servers(internal_user_id))

        token = await _ensure_subscription_token(self._pool, internal_user_id)
        config = VlessUserConfig(
            user_uuid=_user_uuid_from_internal(internal_user_id),
            subscription_url=_web_sub_url(token),
            servers=tuple(servers),
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def _compute_user_config(self, internal_user_id: str) -> VlessProviderResult:
        """Blocking probe + cache, serialized per user so concurrent cold reads don't
        each fan out ~14 panel calls. The epoch guard drops the result if a mutation
        (create/revoke/delete/activate) invalidated the entry mid-probe."""
        async with self._user_lock(internal_user_id):
            # Double-check after acquiring the lock — another read may have just filled it.
            cached = self._config_cache.get(internal_user_id)
            if cached is not None and time.monotonic() - cached[0] < _USER_CONFIG_FRESH_SECONDS:
                return cached[1]
            epoch = self._cache_epoch.get(internal_user_id, 0)
            probed = await self._probe_user_config(internal_user_id)
            result = probed if probed is not None else VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)
            self._store_config_cache(internal_user_id, epoch, result)
            return result

    def _schedule_refresh(self, internal_user_id: str) -> None:
        """Fire-and-forget background refresh; deduped so at most one runs per user."""
        if internal_user_id in self._refreshing:
            return
        epoch = self._cache_epoch.get(internal_user_id, 0)
        self._refreshing.add(internal_user_id)
        task = asyncio.create_task(self._refresh_user_config(internal_user_id, epoch))
        # Keep a strong reference until completion (create_task docs: task may otherwise
        # be garbage-collected mid-execution).
        self._refresh_tasks.add(task)
        task.add_done_callback(lambda done, uid=internal_user_id: self._on_refresh_done(uid, done))

    def _on_refresh_done(self, internal_user_id: str, task: asyncio.Task) -> None:
        self._refreshing.discard(internal_user_id)
        self._refresh_tasks.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            _LOGGER.warning("background user-config refresh failed user=%s: %r", internal_user_id, exc)

    async def _refresh_user_config(self, internal_user_id: str, epoch_at_schedule: int) -> None:
        """Background refresh. Deliberately does NOT take the user lock (must not block
        user mutations like create/revoke); the epoch guard in ``_store_config_cache``
        discards the result if a mutation invalidated the entry while this probe ran."""
        probed = await self._probe_user_config(internal_user_id)
        if probed is None:
            return  # no active servers — leave whatever is cached (or empty)
        self._store_config_cache(internal_user_id, epoch_at_schedule, probed)

    def _store_config_cache(self, internal_user_id: str, epoch_at_compute: int, result: VlessProviderResult) -> None:
        # Don't clobber a fresher state: a mutation bumps the epoch; if it changed since
        # we read it (before the probe), drop our result so the next read recomputes.
        if self._cache_epoch.get(internal_user_id, 0) != epoch_at_compute:
            return
        if len(self._config_cache) > _CONFIG_CACHE_MAX_ENTRIES:
            self._config_cache.clear()
        self._config_cache[internal_user_id] = (time.monotonic(), result, epoch_at_compute)

    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Disable (not delete) VLESS user on all servers."""
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._revoke_user_unlocked(internal_user_id=internal_user_id)

    async def _revoke_user_unlocked(self, *, internal_user_id: str) -> VlessProviderResult:
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        clients = [c for c in clients if c.server_config.panel_flavor != "h1"]  # h1 deferred to reconcile
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _disable(client: XuiApiClient) -> XuiClientResult:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            user_uuid = _vless_uuid_for_transport(internal_user_id, client.server_config.transport_type)
            return await client.disable_client(user_uuid=user_uuid, email=email, expiry_ts=expiry)

        raw = await _run_sequential_per_panel(clients, _disable)

        await self._bare_xray_lifecycle(internal_user_id, "remove")

        any_disabled = any(not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS for _, r in raw)

        if any_disabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def activate_user(
        self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365
    ) -> VlessProviderResult:
        """Re-enable previously disabled VLESS user on all servers."""
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._activate_user_unlocked(
                internal_user_id=internal_user_id, device_count=device_count, expiry_days=expiry_days
            )

    async def _activate_user_unlocked(
        self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365
    ) -> VlessProviderResult:
        expiry = _expiry_timestamp(days=expiry_days)
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        clients = [c for c in clients if c.server_config.panel_flavor != "h1"]  # h1 deferred to reconcile
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        # Delete+re-add instead of enable_client to avoid 3x-ui client_traffics desync.
        async def _reactivate(client: XuiApiClient) -> XuiClientResult:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            user_uuid = _vless_uuid_for_transport(internal_user_id, client.server_config.transport_type)
            await client.delete_client(user_uuid=user_uuid)
            return await client.add_client(
                user_uuid=user_uuid,
                email=email,
                expiry_ts=expiry,
                enable=True,
                limit_ip=limit_ip,
            )

        raw = await _run_sequential_per_panel(clients, _reactivate)

        await self._bare_xray_lifecycle(internal_user_id, "add")

        any_enabled = any(not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS for _, r in raw)

        if any_enabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Permanently delete VLESS user from all servers."""
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._delete_user_unlocked(internal_user_id=internal_user_id)

    async def _delete_user_unlocked(self, *, internal_user_id: str) -> VlessProviderResult:
        clients = await self._get_clients()
        clients = [c for c in clients if c.server_config.panel_flavor != "h1"]  # h1 deferred to reconcile
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _delete(client: XuiApiClient) -> XuiClientResult:
            # Per-transport uuid (matches what create_user/reconcile provision).
            user_uuid = _vless_uuid_for_transport(internal_user_id, client.server_config.transport_type)
            return await client.delete_client(user_uuid=user_uuid)

        raw = await _run_sequential_per_panel(clients, _delete)

        await self._bare_xray_lifecycle(internal_user_id, "remove")

        any_deleted = any(
            not isinstance(r, Exception) and r.outcome in (XuiOutcome.SUCCESS, XuiOutcome.NOT_FOUND) for _, r in raw
        )

        if any_deleted:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def _reconcile_h1_server(self, client: XuiApiClient, users: list[asyncpg.Record]) -> None:
        """Desired-state sync for an h1-fork panel (h1cloud) via direct SFTP db-edit.

        The h1-fork (3x-ui v3 on Pterodactyl) reads VLESS clients from the clients/client_inbounds/
        client_traffics TABLES, not from inbounds.settings.clients JSON. The panel /update (used by
        ``replace_inbound_clients``) writes only the JSON → bot clients never reach the tables → xray
        doesn't serve them, AND /update triggers a reload-loop (``use of closed network connection``).
        So this method NEVER /updates: it downloads x-ui.db over SFTP, mirrors desired bot clients
        into the tables (``_sync_h1_tables``, scoped to x-user- so manual clients are untouched),
        uploads atomically (rename), and restarts xray (config.json regenerates from the tables).
        Graceful: missing creds / SFTP failure → log + return (h1cloud state preserved, nothing breaks).

        Needs H1_SFTP_HOST/PORT/USER/PASS env (set in backend/.env on prod). Runs on the reconcile
        timer → auto-provisions new users, auto-disables expired, auto-removes deleted."""
        host = os.environ.get("H1_SFTP_HOST", "").strip()
        if not host:
            _LOGGER.info("reconcile_h1_sftp server=%s skipped (H1_SFTP_HOST not set)", client.server_id)
            return
        port = int(os.environ.get("H1_SFTP_PORT", "2022"))
        user = os.environ.get("H1_SFTP_USER", "")
        pw = os.environ.get("H1_SFTP_PASS", "")
        transport = client.server_config.transport_type
        inbound_id = client.server_config.inbound_id

        desired: dict[str, dict] = {}
        now = datetime.now(UTC)
        for u in users:
            uid = u["internal_user_id"]
            expiry = _expiry_ms_for_user(u.get("active_until_utc"), now)
            desired[_email_from_internal(uid, transport_type=transport)] = {
                "uuid": _vless_uuid_for_transport(uid, transport),
                "enable": u.get("state_label") == "active",
                "expiry": expiry,
            }

        import paramiko  # lazy — keeps module import light and test-isolated

        sftp = None
        ssh = None
        local_db = None
        local_bak = None
        try:
            ssh = paramiko.SSHClient()
            ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
            ssh.connect(
                hostname=host,
                port=port,
                username=user,
                password=pw,
                timeout=20,
                look_for_keys=False,
                allow_agent=False,
            )
            sftp = ssh.open_sftp()
            local_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False).name
            local_bak = local_db + ".bak"
            sftp.get("db/x-ui.db", local_db)
            import shutil

            shutil.copy(local_db, local_bak)  # pristine backup (for restore on failure)

            conn = sqlite3.connect(local_db)
            try:
                changed = _sync_h1_tables(conn, desired, inbound_id)
            finally:
                conn.close()

            if not changed:
                _LOGGER.info(
                    "reconcile_h1_sftp server=%s steady-state (no change, no restart)",
                    client.server_id,
                )
                return

            # atomic upload: .new -> rename over x-ui.db (previous kept as .old for one cycle)
            sftp.put(local_db, "db/x-ui.db.new")
            with contextlib.suppress(Exception):
                sftp.remove("db/x-ui.db.old")
            with contextlib.suppress(Exception):
                sftp.rename("db/x-ui.db", "db/x-ui.db.old")
            sftp.rename("db/x-ui.db.new", "db/x-ui.db")
            restarted = await client.restart_xray()
            if restarted:
                _LOGGER.info(
                    "reconcile_h1_sftp server=%s synced=%d restart=True",
                    client.server_id,
                    len(desired),
                )
            else:
                # restart failed — restore the pristine backup so h1cloud keeps serving its last state
                _LOGGER.warning("reconcile_h1_sftp server=%s restart_failed — restoring backup", client.server_id)
                with contextlib.suppress(Exception):
                    sftp.put(local_bak, "db/x-ui.db")
                    await client.restart_xray()
        except Exception:
            _LOGGER.warning("reconcile_h1_sftp server=%s failed (state preserved)", client.server_id, exc_info=True)
        finally:
            with contextlib.suppress(Exception):
                if sftp is not None:
                    sftp.close()
            with contextlib.suppress(Exception):
                if ssh is not None:
                    ssh.close()
            for path in (local_db, local_bak):
                if path:
                    with contextlib.suppress(Exception):
                        os.unlink(path)

    async def reconcile_all_users(self) -> tuple[int, int, int]:
        """Ensure every non-deleted user has a VLESS key on every active server.

        Covers active subscribers AND expired-but-in-grace users (keys not yet
        purged). Only adds clients that are **missing** on a specific server —
        existing clients are never touched (no delete, no re-add, no enable
        toggle, no traffic reset). Enable state of a freshly added client follows
        the subscription: active → enabled, expired → disabled. Enable state of
        existing clients is left to the deactivate/reactivate lifecycle flows.

        This closes the gap where a user who expired *before* a server was added
        (e.g. Russia, id=11) never gets a client there — so they're absent from
        that server until they renew. Runs as a fire-and-forget background task on
        startup and periodically via the server-sync scheduler.

        Returns ``(added, failed, total_users)`` counts.
        """
        self._clients_ts = 0.0  # Force server list refresh
        users = await self._pool.fetch(
            "SELECT i.internal_user_id, "
            "  s.state_label, s.device_count, s.active_until_utc "
            "FROM user_identities i "
            "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
            "WHERE s.state_label IN ('active', 'expired') "
            "  AND s.keys_deleted_at IS NULL"
        )
        if not users:
            _LOGGER.info("reconcile_start: no users")
            return 0, 0, 0

        clients = await self._get_clients()
        if not clients:
            _LOGGER.info("reconcile_start: no active servers")
            return 0, 0, len(users)

        _LOGGER.info("reconcile_start users=%d servers=%d", len(users), len(clients))

        added = 0
        failed = 0

        for u in users:
            uid = u["internal_user_id"]
            # Active subscribers get enabled keys; expired (in grace) get disabled keys.
            enable = u["state_label"] == "active"
            # Use real device_count from subscription; fall back to trial limit
            device_count = u.get("device_count") or 0
            limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT
            # Use real expiry from subscription; expired users get their past active_until
            # (panel shows 'expired', not a misleading +365d)
            expiry = _expiry_ms_for_user(u.get("active_until_utc"))

            user_added = False
            user_failed = False

            for client in clients:
                if client.server_config.panel_flavor == "h1":
                    continue  # h1-fork panels are handled by the batch reconcile below
                email = _email_from_internal(uid, transport_type=client.server_config.transport_type)
                # Check if client already exists on this panel — non-destructive probe
                try:
                    existing = await client.resolve_client_uuid(email=email)
                except Exception:
                    _LOGGER.debug("reconcile_probe_failed user=%s server=%s", uid[:8], client.server_id)
                    user_failed = True
                    continue

                if existing is not None:
                    continue  # Already exists — skip, don't touch

                # Client missing on this server — add it (enabled iff subscription active)
                try:
                    user_uuid = _vless_uuid_for_transport(uid, client.server_config.transport_type)
                    result = await client.add_client(
                        user_uuid=user_uuid,
                        email=email,
                        expiry_ts=expiry,
                        enable=enable,
                        limit_ip=limit_ip,
                    )
                    if result.outcome == XuiOutcome.SUCCESS:
                        user_added = True
                        _LOGGER.info(
                            "reconcile_added user=%s server=%s enable=%s",
                            uid[:8],
                            client.server_id,
                            enable,
                        )
                    else:
                        _LOGGER.warning(
                            "reconcile_add_failed user=%s server=%s outcome=%s",
                            uid[:8],
                            client.server_id,
                            result.outcome,
                        )
                        user_failed = True
                except Exception:
                    _LOGGER.warning(
                        "reconcile_add_exception user=%s server=%s",
                        uid[:8],
                        client.server_id,
                        exc_info=True,
                    )
                    user_failed = True

            if user_added:
                added += 1
            if user_failed:
                failed += 1

        # --- bare_xray reconcile (live xray gRPC): full desired-state per inbound.
        # Repopulates Cloud.ru after an xray restart (API users are in-memory only) and
        # removes orphans (users present on an inbound but no longer active/in-grace). ---
        bx_clients = await self._get_bare_xray_clients()
        bx_added = bx_removed = 0
        for client in bx_clients:
            transport = client.server_config.transport_type
            desired = {_email_from_internal(u["internal_user_id"], transport_type=transport) for u in users}
            try:
                present = set(await client.list_user_emails())
            except Exception:
                _LOGGER.warning("reconcile bx_list_failed server=%s", client.server_id)
                continue
            for u in users:
                email = _email_from_internal(u["internal_user_id"], transport_type=transport)
                if email in present:
                    continue
                uid = u["internal_user_id"]
                try:
                    if await client.add_client(user_uuid=_vless_uuid_for_transport(uid, transport), email=email):
                        bx_added += 1
                except Exception:
                    _LOGGER.warning("reconcile bx_add_failed user=%s server=%s", uid[:8], client.server_id)
            for orphan in present - desired:
                with contextlib.suppress(Exception):
                    if await client.remove_client(email=orphan):
                        bx_removed += 1
        if bx_clients:
            _LOGGER.info("reconcile_bare_xray added=%d removed=%d", bx_added, bx_removed)

        # --- h1-fork panels (h1cloud): batch desired-state in h1-schema + xray restart ---
        for client in clients:
            if client.server_config.panel_flavor == "h1":
                await self._reconcile_h1_server(client, users)

        _LOGGER.info("reconcile_done added=%d failed=%d total=%d", added, failed, len(users))
        return added, failed, len(users)
