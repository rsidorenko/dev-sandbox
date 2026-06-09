"""VLESS provider port and stub implementation.

The real provider will connect to a VLESS panel (Marzban / 3x-ui).
This stub returns fake configs for development and testing.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol


class VlessProviderOutcome(StrEnum):
    SUCCESS = "success"
    UNAVAILABLE = "unavailable"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class VlessServerConfig:
    server_label: str
    country_code: str
    country_flag: str
    vless_link: str


@dataclass(frozen=True, slots=True)
class VlessUserConfig:
    user_uuid: str
    subscription_url: str
    servers: tuple[VlessServerConfig, ...]


@dataclass(frozen=True, slots=True)
class VlessProviderResult:
    outcome: VlessProviderOutcome
    config: VlessUserConfig | None = None


# Known country flags for VLESS servers
_SERVER_REGISTRY: tuple[dict[str, str], ...] = (
    {"label": "Нидерланды-1", "country": "NL", "flag": "\U0001f1f3\U0001f1f1"},
    {"label": "Германия-1", "country": "DE", "flag": "\U0001f1e9\U0001f1ea"},
    {"label": "Финляндия-1", "country": "FI", "flag": "\U0001f1eb\U0001f1ee"},
    {"label": "США-1", "country": "US", "flag": "\U0001f1fa\U0001f1f8"},
    {"label": "Япония-1", "country": "JP", "flag": "\U0001f1ef\U0001f1f5"},
)


class VlessProviderPort(Protocol):
    async def create_user(self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365) -> VlessProviderResult: ...
    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult: ...
    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult: ...
    async def activate_user(self, *, internal_user_id: str, device_count: int = 0, expiry_days: int = 365) -> VlessProviderResult: ...
    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult: ...


def _fake_vless_link(server: dict[str, str], user_uuid: str) -> str:
    return (
        f"vless://{user_uuid}@fake.{server['country'].lower()}.example.com:443"
        f"?type=ws&security=tls&sni=fake.{server['country'].lower()}.example.com"
        f"#VPN+{server['label']}"
    )


def build_subscription_url(servers: tuple[VlessServerConfig, ...]) -> str:
    links = [s.vless_link for s in servers]
    encoded = base64.b64encode("\n".join(links).encode("utf-8")).decode("utf-8")
    return f"data:text/plain;base64,{encoded}"


def format_key_list(servers: tuple[VlessServerConfig, ...]) -> str:
    lines: list[str] = []
    for s in servers:
        lines.append(f"{s.country_flag} {s.server_label}")
        lines.append(f"`{s.vless_link}`")
        lines.append("")
    return "\n".join(lines).strip()


class StubVlessProvider:
    """Fake VLESS provider for development. Returns deterministic configs."""

    def __init__(self) -> None:
        self._created_users: set[str] = set()

    async def create_user(self, *, internal_user_id: str) -> VlessProviderResult:
        self._created_users.add(internal_user_id)
        return self._build_config(internal_user_id)

    async def get_user_config(self, *, internal_user_id: str) -> VlessProviderResult:
        if internal_user_id not in self._created_users:
            return VlessProviderResult(outcome=VlessProviderOutcome.NOT_FOUND)
        return self._build_config(internal_user_id)

    async def revoke_user(self, *, internal_user_id: str) -> VlessProviderResult:
        self._created_users.discard(internal_user_id)
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)

    async def activate_user(self, *, internal_user_id: str) -> VlessProviderResult:
        self._created_users.add(internal_user_id)
        return self._build_config(internal_user_id)

    async def delete_user(self, *, internal_user_id: str) -> VlessProviderResult:
        self._created_users.discard(internal_user_id)
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS)

    def _build_config(self, internal_user_id: str) -> VlessProviderResult:
        import hashlib

        user_uuid = hashlib.sha256(internal_user_id.encode()).hexdigest()[:8] + "-stub-0000-0000-000000000000"
        servers = tuple(
            VlessServerConfig(
                server_label=s["label"],
                country_code=s["country"],
                country_flag=s["flag"],
                vless_link=_fake_vless_link(s, user_uuid),
            )
            for s in _SERVER_REGISTRY
        )
        sub_url = build_subscription_url(servers)
        config = VlessUserConfig(
            user_uuid=user_uuid,
            subscription_url=sub_url,
            servers=servers,
        )
        return VlessProviderResult(outcome=VlessProviderOutcome.SUCCESS, config=config)
