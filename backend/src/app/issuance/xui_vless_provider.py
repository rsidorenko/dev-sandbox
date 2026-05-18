"""Real VLESS provider backed by 3x-ui panels.

Implements :class:`VlessProviderPort` — creates/reads/disables/deletes VLESS users
across all active VPN servers registered in the ``vpn_servers`` table.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg

from app.issuance.vless_provider import (
    VlessProviderOutcome,
    VlessProviderPort,
    VlessProviderResult,
    VlessServerConfig,
    VlessUserConfig,
    build_subscription_url,
)
from app.issuance.xui_client import XuiApiClient, XuiOutcome, XuiServerConfig

_LOGGER = logging.getLogger(__name__)

_DEFAULT_EXPIRY_DAYS = 365


def _user_uuid_from_internal(internal_user_id: str) -> str:
    """Deterministic UUID v4 derived from internal user ID (stable across calls)."""
    digest = hashlib.sha256(internal_user_id.encode()).hexdigest()
    return str(uuid.UUID(digest[:32]))


def _email_from_internal(internal_user_id: str) -> str:
    return f"user-{internal_user_id[:16]}"


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
    path = server.ws_path.lstrip("/")
    sni = server.tls_sni or host
    label = f"VPN+{server.label}"
    return (
        f"vless://{user_uuid}@{host}:{port}"
        f"?type=ws&security=tls&sni={sni}&path=%2F{path}#{label}"
    )


async def _load_server_configs(pool: asyncpg.Pool) -> tuple[XuiServerConfig, ...]:
    rows = await pool.fetch(
        """SELECT id, label, country_code, country_flag, server_host, server_port,
                  ws_path, tls_sni, panel_url, panel_username, panel_password, inbound_id
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
            panel_password=r["panel_password"],
            inbound_id=r["inbound_id"],
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

    async def create_user(self, *, internal_user_id: str) -> VlessProviderResult:
        user_uuid = _user_uuid_from_internal(internal_user_id)
        email = _email_from_internal(internal_user_id)
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        if not clients:
            _LOGGER.warning("no active vpn servers configured")
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        successes: list[tuple[XuiApiClient, XuiServerConfig]] = []
        for client in clients:
            result = await client.add_client(
                user_uuid=user_uuid,
                email=email,
                expiry_ts=expiry,
                enable=True,
            )
            if result.outcome == XuiOutcome.SUCCESS:
                successes.append((client, client.server_config))
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
                server_label=sc.label,
                country_code=sc.country_code,
                country_flag=sc.country_flag,
                vless_link=_build_vless_link(sc, user_uuid),
            )
            for _, sc in successes
        )
        config = VlessUserConfig(
            user_uuid=user_uuid,
            subscription_url=build_subscription_url(servers),
            servers=servers,
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult:
        user_uuid = _user_uuid_from_internal(internal_user_id)
        clients = await self._get_clients()
        if not clients:
            return VlessProviderResult(outcome=VlessProviderOutcome.UNAVAILABLE)

        servers: list[VlessServerConfig] = []
        for client in clients:
            # Check if client exists on this server by trying to get it
            result = await client.get_client(email=_email_from_internal(internal_user_id))
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

        config = VlessUserConfig(
            user_uuid=user_uuid,
            subscription_url=build_subscription_url(tuple(servers)),
            servers=tuple(servers),
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)

    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Disable (not delete) VLESS user on all servers."""
        user_uuid = _user_uuid_from_internal(internal_user_id)
        email = _email_from_internal(internal_user_id)
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        any_disabled = False
        for client in clients:
            result = await client.disable_client(
                user_uuid=user_uuid, email=email, expiry_ts=expiry
            )
            if result.outcome == XuiOutcome.SUCCESS:
                any_disabled = True

        if any_disabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def activate_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Re-enable previously disabled VLESS user on all servers."""
        user_uuid = _user_uuid_from_internal(internal_user_id)
        email = _email_from_internal(internal_user_id)
        expiry = _expiry_timestamp()

        clients = await self._get_clients()
        any_enabled = False
        for client in clients:
            result = await client.enable_client(
                user_uuid=user_uuid, email=email, expiry_ts=expiry
            )
            if result.outcome == XuiOutcome.SUCCESS:
                any_enabled = True

        if any_enabled:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)

    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult:
        """Permanently delete VLESS user from all servers."""
        user_uuid = _user_uuid_from_internal(internal_user_id)

        clients = await self._get_clients()
        any_deleted = False
        for client in clients:
            result = await client.delete_client(user_uuid=user_uuid)
            if result.outcome in (XuiOutcome.SUCCESS, XuiOutcome.NOT_FOUND):
                any_deleted = True

        if any_deleted:
            return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)
        return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)
