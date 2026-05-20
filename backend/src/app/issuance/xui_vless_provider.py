"""Real VLESS provider backed by 3x-ui panels.

Implements :class:`VlessProviderPort` — creates/reads/disables/deletes VLESS users
across all active VPN servers registered in the ``vpn_servers`` table.
"""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
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

_LOGGER = logging.getLogger(__name__)

_DEFAULT_EXPIRY_DAYS = 365
_TRIAL_DEVICE_LIMIT = 5


def _generate_subscription_token() -> str:
    return secrets.token_urlsafe(16)


def _web_sub_url(token: str) -> str:
    return f"{_SUBSCRIPTION_BASE_URL}/sub/{token}"


async def _ensure_subscription_token(pool: asyncpg.Pool, internal_user_id: str) -> str:
    row = await pool.fetchrow(
        "SELECT subscription_token FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    if row and row["subscription_token"]:
        return row["subscription_token"]
    token = _generate_subscription_token()
    await pool.execute(
        "UPDATE user_identities SET subscription_token = $1 WHERE internal_user_id = $2",
        token,
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


def _email_from_internal(internal_user_id: str) -> str:
    return f"user-{internal_user_id[:16]}"


def _expiry_timestamp(days: int = _DEFAULT_EXPIRY_DAYS) -> int:
    """Unix timestamp in milliseconds for expiry."""
    future = datetime.now(UTC) + timedelta(days=days)
    return int(future.timestamp() * 1000)


def _build_vless_link(
    server: XuiServerConfig,
    user_uuid: str,
    *,
    flow: str = "xtls-rprx-vision",
) -> str:
    """Build a vless:// URI for a specific server with Reality TLS."""
    host = server.server_host
    port = server.server_port
    label = f"{server.country_flag} {server.label}"
    return (
        f"vless://{user_uuid}@{host}:{port}"
        f"?type=tcp&security=reality"
        f"&pbk={server.reality_pbk}&fp=chrome&sni={server.reality_sni}"
        f"&sid={server.reality_sid}&spx=%2F&flow={flow}"
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
                  inbound_id, reality_pbk, reality_sid, reality_sni
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
        )
        for r in rows
    )


class XuiVlessProvider(VlessProviderPort):
    """Real VLESS provider: manages users across all active 3x-ui panels."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def _get_clients(self) -> list[XuiApiClient]:
        configs = await _load_server_configs(self._pool)
        return [XuiApiClient(c) for c in configs]

    async def create_user(self, *, internal_user_id: str, device_count: int = 0) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        email = _email_from_internal(internal_user_id)
        expiry = _expiry_timestamp()
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        if not clients:
            _LOGGER.warning("no active vpn servers configured")
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        async def _add(client: XuiApiClient) -> tuple[XuiApiClient, XuiClientResult]:
            result = await client.add_client(
                user_uuid=user_uuid,
                email=email,
                expiry_ts=expiry,
                enable=True,
                limit_ip=limit_ip,
            )
            return client, result

        results = await asyncio.gather(*[_add(c) for c in clients], return_exceptions=True)

        successes: list[XuiApiClient] = []
        for item in results:
            if isinstance(item, Exception):
                _LOGGER.warning("xui add_client exception: %s", item)
                continue
            client, result = item
            if result.outcome == XuiOutcome.SUCCESS:
                successes.append(client)
            else:
                _LOGGER.warning(
                    "xui add_client failed server=%s outcome=%s",
                    client.server_id,
                    result.outcome,
                )

        if not successes:
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        servers = tuple(
            VlessServerConfig(
                server_label=c.server_config.label,
                country_code=c.server_config.country_code,
                country_flag=c.server_config.country_flag,
                vless_link=_build_vless_link(c.server_config, user_uuid),
            )
            for c in successes
        )
        token = await _ensure_subscription_token(self._pool, internal_user_id)
        config = VlessUserConfig(
            user_uuid=user_uuid,
            subscription_url=_web_sub_url(token),
            servers=servers,
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult:
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        async def _check(client: XuiApiClient) -> tuple[XuiApiClient, XuiClientResult]:
            result = await client.get_client(email=_email_from_internal(internal_user_id))
            return client, result

        results = await asyncio.gather(*[_check(c) for c in clients], return_exceptions=True)

        servers: list[VlessServerConfig] = []
        for item in results:
            if isinstance(item, Exception):
                continue
            client, result = item
            if result.outcome == XuiOutcome.SUCCESS:
                servers.append(
                    VlessServerConfig(
                        server_label=client.server_config.label,
                        country_code=client.server_config.country_code,
                        country_flag=client.server_config.country_flag,
                        vless_link=_build_vless_link(client.server_config, user_uuid),
                    )
                )

        if not servers:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        token = await _ensure_subscription_token(self._pool, internal_user_id)
        config = VlessUserConfig(
            user_uuid=user_uuid,
            subscription_url=_web_sub_url(token),
            servers=tuple(servers),
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Disable (not delete) VLESS user on all servers."""
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        email = _email_from_internal(internal_user_id)
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _disable(client: XuiApiClient) -> XuiClientResult:
            return await client.disable_client(user_uuid=user_uuid, email=email, expiry_ts=expiry)

        results = await asyncio.gather(*[_disable(c) for c in clients], return_exceptions=True)

        any_disabled = any(
            not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS
            for r in results
        )

        if any_disabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def activate_user(self, *, internal_user_id: str, device_count: int = 0) -> VlessProviderResult:
        """Re-enable previously disabled VLESS user on all servers."""
        user_uuid = await _get_or_create_vless_uuid(self._pool, internal_user_id)
        email = _email_from_internal(internal_user_id)
        expiry = _expiry_timestamp()
        limit_ip = device_count if device_count > 0 else _TRIAL_DEVICE_LIMIT

        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

        async def _enable(client: XuiApiClient) -> XuiClientResult:
            return await client.enable_client(
                user_uuid=user_uuid, email=email, expiry_ts=expiry, limit_ip=limit_ip
            )

        results = await asyncio.gather(*[_enable(c) for c in clients], return_exceptions=True)

        any_enabled = any(
            not isinstance(r, Exception) and r.outcome == XuiOutcome.SUCCESS
            for r in results
        )

        if any_enabled:
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
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)
