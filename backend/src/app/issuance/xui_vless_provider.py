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

_LOGGER = logging.getLogger(__name__)

_DEFAULT_EXPIRY_DAYS = 365
_TRIAL_DEVICE_LIMIT = 5


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
    expires_at = datetime.now(UTC) + timedelta(days=90)
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
    """Get stored VLESS UUID or generate a new random one and persist it."""
    row = await pool.fetchrow(
        "SELECT vless_uuid FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    if row and row["vless_uuid"]:
        return row["vless_uuid"]
    new_uuid = str(uuid.uuid4())
    await pool.execute(
        "UPDATE user_identities SET vless_uuid = $1 WHERE internal_user_id = $2",
        new_uuid,
        internal_user_id,
    )
    return new_uuid


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

    if server.transport_type == "xhttp":
        path = server.ws_path.strip("/")
        return (
            f"vless://{user_uuid}@{host}:{port}"
            f"?type=xhttp&security=reality&path=%2F{path}"
            f"&pbk={server.reality_pbk}&fp=chrome&sni={server.reality_sni}"
            f"&sid={server.reality_sid}&spx=%2F"
            f"#{label}"
        )

    if server.transport_type == "ws":
        path = server.ws_path.strip("/")
        ws_host = server.tls_sni or host
        return (
            f"vless://{user_uuid}@{ws_host}:{port}"
            f"?type=ws&security=tls&path=%2F{path}"
            f"&host={ws_host}&fp=chrome&sni={ws_host}"
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

    # Default: TCP + Reality
    return (
        f"vless://{user_uuid}@{host}:{port}"
        f"?type=tcp&security=reality"
        f"&pbk={server.reality_pbk}&fp=chrome&sni={server.reality_sni}"
        f"&sid={server.reality_sid}&spx=%2F&flow=xtls-rprx-vision"
        f"#{label}"
    )


def _resolve_panel_password(row: asyncpg.Record) -> str:
    """Resolve panel password: prefer encrypted column, fallback to plaintext."""
    encrypted = row.get("encrypted_password", "")
    if encrypted:
        return decrypt_field(encrypted)
    return row["panel_password"]


async def _load_server_configs(pool: asyncpg.Pool) -> tuple[XuiServerConfig, ...]:
    rows = await pool.fetch(
        """SELECT id, label, country_code, country_flag, server_host, server_port,
                  ws_path, tls_sni, panel_url, panel_username, panel_password,
                  COALESCE(encrypted_password, '') AS encrypted_password,
                  inbound_id, reality_pbk, reality_sid, reality_sni,
                  COALESCE(transport_type, 'tcp') AS transport_type,
                  COALESCE(api_token, '') AS api_token
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
            transport_type=r["transport_type"],
            api_token=r["api_token"],
        )
        for r in rows
    )


class XuiVlessProvider(VlessProviderPort):
    """Real VLESS provider: manages users across all active 3x-ui panels.

    Caches XuiApiClient instances with a TTL to avoid recreating HTTP clients
    on every operation. All panel requests run concurrently via asyncio.gather.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._clients: list[XuiApiClient] | None = None
        self._clients_ts: float = 0.0

    async def _get_clients(self) -> list[XuiApiClient]:
        if self._clients is not None and time.monotonic() - self._clients_ts < _CACHE_TTL_SECONDS:
            return self._clients
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

    async def aclose(self) -> None:
        """Close all cached HTTP clients. Call on shutdown."""
        await self._close_clients()

    async def _restart_xray_on_all(self) -> None:
        """Restart xray on all panel clients to pick up config changes immediately."""
        clients = self._clients
        if not clients:
            return
        results = await asyncio.gather(
            *[c.restart_xray() for c in clients], return_exceptions=True,
        )
        for client, ok in zip(clients, results):
            if isinstance(ok, Exception) or not ok:
                _LOGGER.debug("xray restart failed server=%s", client.server_id)

    async def create_user(self, *, internal_user_id: str, device_count: int = 0) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        expiry = _expiry_timestamp()
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        if not clients:
            _LOGGER.warning("no active vpn servers configured")
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        async def _add_or_update(client: XuiApiClient) -> tuple[XuiApiClient, XuiClientResult, str]:
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
                return client, result, user_uuid
            result = await client.add_client(
                user_uuid=user_uuid,
                email=email,
                expiry_ts=expiry,
                enable=True,
                limit_ip=limit_ip,
            )
            return client, result, user_uuid

        results = await asyncio.gather(*[_add_or_update(c) for c in clients], return_exceptions=True)

        successes: list[tuple[XuiApiClient, str]] = []
        for item in results:
            if isinstance(item, Exception):
                _LOGGER.warning("xui add_client exception: %s", item)
                continue
            client, result, effective_uuid = item
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
        await self._restart_xray_on_all()
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult:
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
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Disable (not delete) VLESS user on all servers."""
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _disable(client: XuiApiClient) -> XuiClientResult:
            email = _email_from_internal(internal_user_id, transport_type=client.server_config.transport_type)
            return await client.disable_client(user_uuid=user_uuid, email=email, expiry_ts=expiry)

        results = await asyncio.gather(*[_disable(c) for c in clients], return_exceptions=True)

        any_disabled = any(
            not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS
            for r in results
        )

        if any_disabled:
            await self._restart_xray_on_all()
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def activate_user(self, *, internal_user_id: str, device_count: int = 0) -> VlessProviderResult:
        """Re-enable previously disabled VLESS user on all servers."""
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        expiry = _expiry_timestamp()
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

        results = await asyncio.gather(*[_reactivate(c) for c in clients], return_exceptions=True)

        any_enabled = any(
            not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS
            for r in results
        )

        if any_enabled:
            await self._restart_xray_on_all()
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Permanently delete VLESS user from all servers."""
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _delete(client: XuiApiClient) -> XuiClientResult:
            return await client.delete_client(user_uuid=user_uuid)

        results = await asyncio.gather(*[_delete(c) for c in clients], return_exceptions=True)

        any_deleted = any(
            not isinstance(r, Exception) and r.outcome in (XuiOutcome.SUCCESS, XuiOutcome.NOT_FOUND)
            for r in results
        )

        if any_deleted:
            await self._restart_xray_on_all()
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)
