"""Real VLESS provider backed by 3x-ui panels.

Implements :class:`VlessProviderPort` — creates/reads/disables/deletes VLESS users
across all active VPN servers registered in the ``vpn_servers`` table.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import secrets
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
from app.issuance.xui_client import XuiApiClient, XuiClientResult, XuiOutcome, XuiServerConfig
from app.security.field_encryption import decrypt_field

_SUBSCRIPTION_BASE_URL = (
    os.environ.get("SUBSCRIPTION_BASE_URL", "").strip()
    or os.environ.get("NEXT_PUBLIC_SITE_URL", "").strip()
    or "https://bravada-connect.ru"
).rstrip("/")

_CACHE_TTL_SECONDS = 600  # 10 minutes
_SUBSCRIPTION_TOKEN_TTL_DAYS = int(os.environ.get("SUBSCRIPTION_TOKEN_TTL_DAYS", "90"))

_LOGGER = logging.getLogger(__name__)

_DEFAULT_EXPIRY_DAYS = 365
_TRIAL_DEVICE_LIMIT = int(os.environ.get("TRIAL_DEVICE_LIMIT", "5"))


def _generate_subscription_token() -> str:
    return secrets.token_urlsafe(16)


def _web_sub_url(token: str) -> str:
    return f"{_SUBSCRIPTION_BASE_URL}/sub/{token}"


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
    prefix = {"xhttp": "x-", "cdn": "cdn-"}.get(transport_type, "")
    return f"{prefix}user-{internal_user_id[:16]}"


def _expiry_timestamp(days: int = _DEFAULT_EXPIRY_DAYS) -> int:
    """Unix timestamp in milliseconds for expiry."""
    future = datetime.now(UTC) + timedelta(days=days)
    return int(future.timestamp() * 1000)


def _build_vless_link(
    server: XuiServerConfig,
    user_uuid: str,
) -> str:
    """Build a vless:// URI for a specific server."""
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

    if server.transport_type == "cdn":
        path = server.ws_path.strip("/")
        cdn_host = server.tls_sni or host
        return (
            f"vless://{user_uuid}@{cdn_host}:{port}"
            f"?type=ws&security=tls&path=%2F{path}"
            f"&host={cdn_host}&sni={cdn_host}"
            f"#{label}"
        )

    # Default: TCP + Reality (no flow — avoids xtls-rprx-vision DPI fingerprint)
    return (
        f"vless://{user_uuid}@{host}:{port}"
        f"?type=tcp&security=reality"
        f"&pbk={server.reality_pbk}&fp={fp}&sni={server.reality_sni}"
        f"&sid={server.reality_sid}&spx=%2F"
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
                  COALESCE(reality_fp, 'chrome') AS reality_fp
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
        self._plaintext_warning_logged = False
        self._user_locks: dict[str, asyncio.Lock] = {}
        self._config_cache: dict[str, tuple[float, VlessProviderResult]] = {}

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
        self._clients = [XuiApiClient(c) for c in configs]
        self._clients_ts = time.monotonic()
        return self._clients

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
        await self._close_clients()

    async def create_user(self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365) -> VlessProviderResult:
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._create_user_unlocked(internal_user_id=internal_user_id, device_count=device_count, expiry_days=expiry_days)

    async def _create_user_unlocked(self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        expiry = _expiry_timestamp(days=expiry_days)
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        if not clients:
            _LOGGER.warning("no active vpn servers configured")
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        async def _add_or_update(client: XuiApiClient) -> tuple[XuiClientResult, str]:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            existing_uuid = await client.resolve_client_uuid(email=email)
            if existing_uuid is not None:
                await client.delete_client(user_uuid=existing_uuid)
                result = await client.add_client(
                    user_uuid=user_uuid,
                    email=email,
                    expiry_ts=expiry,
                    enable=True,
                    limit_ip=limit_ip,
                )
                return result, user_uuid
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

        # Use the first panel's UUID as the canonical one; sync DB if needed
        canonical_uuid = successes[0][1]
        if canonical_uuid != user_uuid:
            _LOGGER.info(
                "syncing vless_uuid on create user=%s db=%s panel=%s",
                internal_user_id, user_uuid, canonical_uuid,
            )
            await self._pool.execute(
                "UPDATE user_identities SET vless_uuid = $1 WHERE internal_user_id = $2",
                canonical_uuid,
                internal_user_id,
            )

        servers = tuple(
            VlessServerConfig(
                server_label=c.server_config.label,
                country_code=c.server_config.country_code,
                country_flag=c.server_config.country_flag,
                vless_link=_build_vless_link(c.server_config, uuid),
            )
            for c, uuid in successes
        )
        token = await _ensure_subscription_token(self._pool, internal_user_id)
        config = VlessUserConfig(
            user_uuid=canonical_uuid,
            subscription_url=_web_sub_url(token),
            servers=servers,
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    def _invalidate_config_cache(self, internal_user_id: str) -> None:
        self._config_cache.pop(internal_user_id, None)

    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult:
        now = time.monotonic()
        cached = self._config_cache.get(internal_user_id)
        if cached is not None and now - cached[0] < _CACHE_TTL_SECONDS:
            return cached[1]
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        async def _resolve(client: XuiApiClient) -> tuple[XuiApiClient, str | None]:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            uuid = await client.resolve_client_uuid(email=email)
            return client, uuid

        results = await asyncio.gather(*[_resolve(c) for c in clients], return_exceptions=True)

        servers: list[VlessServerConfig] = []
        panel_uuid: str | None = None
        for item in results:
            if isinstance(item, Exception):
                _LOGGER.debug("resolve_client_uuid exception: %s", item)
                continue
            client, uuid = item
            if uuid is not None:
                if panel_uuid is None:
                    panel_uuid = uuid
                servers.append(
                    VlessServerConfig(
                        server_label=client.server_config.label,
                        country_code=client.server_config.country_code,
                        country_flag=client.server_config.country_flag,
                        vless_link=_build_vless_link(client.server_config, uuid),
                    )
                )

        if not servers:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        # Sync DB UUID with the actual panel UUID if they diverged
        effective_uuid = panel_uuid or user_uuid
        if panel_uuid and panel_uuid != user_uuid:
            _LOGGER.info(
                "syncing vless_uuid user=%s db=%s panel=%s",
                internal_user_id, user_uuid, panel_uuid,
            )
            await self._pool.execute(
                "UPDATE user_identities SET vless_uuid = $1 WHERE internal_user_id = $2",
                panel_uuid,
                internal_user_id,
            )

        token = await _ensure_subscription_token(self._pool, internal_user_id)
        config = VlessUserConfig(
            user_uuid=effective_uuid,
            subscription_url=_web_sub_url(token),
            servers=tuple(servers),
        )
        result = VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)
        if len(self._config_cache) > 10000:
            self._config_cache.clear()
        self._config_cache[internal_user_id] = (time.monotonic(), result)
        return result

    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Disable (not delete) VLESS user on all servers."""
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._revoke_user_unlocked(internal_user_id=internal_user_id)

    async def _revoke_user_unlocked(self, *, internal_user_id: str) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _disable(client: XuiApiClient) -> XuiClientResult:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            return await client.disable_client(user_uuid=user_uuid, email=email, expiry_ts=expiry)

        raw = await _run_sequential_per_panel(clients, _disable)

        any_disabled = any(
            not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS
            for _, r in raw
        )

        if any_disabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def activate_user(self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365) -> VlessProviderResult:
        """Re-enable previously disabled VLESS user on all servers."""
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._activate_user_unlocked(internal_user_id=internal_user_id, device_count=device_count, expiry_days=expiry_days)

    async def _activate_user_unlocked(self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        expiry = _expiry_timestamp(days=expiry_days)
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        # Delete+re-add instead of enable_client to avoid 3x-ui client_traffics desync.
        async def _reactivate(client: XuiApiClient) -> XuiClientResult:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            await client.delete_client(user_uuid=user_uuid)
            return await client.add_client(
                user_uuid=user_uuid,
                email=email,
                expiry_ts=expiry,
                enable=True,
                limit_ip=limit_ip,
            )

        raw = await _run_sequential_per_panel(clients, _reactivate)

        any_enabled = any(
            not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS
            for _, r in raw
        )

        if any_enabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Permanently delete VLESS user from all servers."""
        self._invalidate_config_cache(internal_user_id)
        async with self._user_lock(internal_user_id):
            return await self._delete_user_unlocked(internal_user_id=internal_user_id)

    async def _delete_user_unlocked(self, *, internal_user_id: str) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _delete(client: XuiApiClient) -> XuiClientResult:
            return await client.delete_client(user_uuid=user_uuid)

        raw = await _run_sequential_per_panel(clients, _delete)

        any_deleted = any(
            not isinstance(r, Exception) and r.outcome in (XuiOutcome.SUCCESS, XuiOutcome.NOT_FOUND)
            for _, r in raw
        )

        if any_deleted:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def reconcile_all_active_users(self) -> tuple[int, int, int]:
        """Ensure all active users have VLESS keys on every active server.

        Only adds clients that are **missing** on a specific server — existing
        clients are never touched (no delete, no re-add, no traffic reset).
        Runs as a fire-and-forget background task on startup.

        Returns ``(added, failed, total_users)`` counts.
        """
        self._clients_ts = 0.0  # Force server list refresh
        users = await self._pool.fetch(
            "SELECT i.internal_user_id, i.vless_uuid FROM user_identities i "
            "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
            "WHERE s.state_label = 'active' AND i.vless_uuid IS NOT NULL"
        )
        if not users:
            _LOGGER.info("reconcile_start: no active users")
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
            user_uuid = u["vless_uuid"]
            user_added = False
            user_failed = False

            for client in clients:
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

                # Client missing on this server — add it
                expiry = _expiry_timestamp(days=_DEFAULT_EXPIRY_DAYS)
                try:
                    result = await client.add_client(
                        user_uuid=user_uuid,
                        email=email,
                        expiry_ts=expiry,
                        enable=True,
                        limit_ip=_TRIAL_DEVICE_LIMIT,
                    )
                    if result.outcome == XuiOutcome.SUCCESS:
                        user_added = True
                        _LOGGER.info(
                            "reconcile_added user=%s server=%s",
                            uid[:8], client.server_id,
                        )
                    else:
                        _LOGGER.warning(
                            "reconcile_add_failed user=%s server=%s outcome=%s",
                            uid[:8], client.server_id, result.outcome,
                        )
                        user_failed = True
                except Exception:
                    _LOGGER.warning(
                        "reconcile_add_exception user=%s server=%s",
                        uid[:8], client.server_id, exc_info=True,
                    )
                    user_failed = True

            if user_added:
                added += 1
            if user_failed:
                failed += 1

        _LOGGER.info("reconcile_done added=%d failed=%d total=%d", added, failed, len(users))
        return added, failed, len(users)
