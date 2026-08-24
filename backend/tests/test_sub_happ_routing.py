"""Tests for the /sub/ Happ routing-profile delivery: the
`happ://routing/onadd/<base64>` link builder and its attachment to the base64
response for Happ imports (flag SUB_HAPP_ROUTING_ENABLE)."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime, timedelta

from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from app.issuance.vless_provider import (
    StubVlessProvider,
    VlessProviderOutcome,
    VlessProviderResult,
    VlessServerConfig,
    VlessUserConfig,
)
from app.web_api.subscription import handle_subscription
from app.web_api.subscription_happ_routing import build_routing_header_value

_PREFIX = "happ://routing/onadd/"
_HAPP_UA = "Happ/1.9.5 (Android 15)"


# ---------------------------------------------------------------------------
# build_routing_header_value — the onadd link
# ---------------------------------------------------------------------------


def test_routing_link_prefix_and_payload() -> None:
    value = build_routing_header_value()
    assert value.startswith(_PREFIX)
    profile = json.loads(base64.b64decode(value[len(_PREFIX) :]))
    assert profile["Name"] == "Bravada"
    assert profile["GlobalProxy"] == "true"
    direct = profile["DirectSites"]
    # Озон: витрина/API + медиа-CDN
    for site in ("domain:ozon.ru", "domain:ozone.ru", "domain:ozonusercontent.com"):
        assert site in direct
    # MAX + его IP-echo проверки — единым direct-путём (иначе детект сплита)
    for site in (
        "domain:max.ru",
        "domain:oneme.ru",
        "domain:okcdn.ru",
        "domain:apptracer.ru",
        "domain:api.ipify.org",
        "domain:checkip.amazonaws.com",
        "domain:ifconfig.me",
    ):
        assert site in direct
    # VK: сайт/приложение + зарубежные CDN-домены, перечисленные явно
    for site in (
        "domain:vk.com",
        "domain:vk.ru",
        "domain:userapi.com",
        "domain:vkuserapi.com",
        "domain:vkuservideo.net",
        "domain:vkvideo.com",
        "domain:mycdn.me",
    ):
        assert site in direct
    # Яндекс.Музыка: API-семейство + медиа-CDN
    for site in ("domain:yandex.ru", "domain:yandex.com", "domain:yandex.net", "domain:yastatic.net"):
        assert site in direct
    # Т-Банк: ядро (bug-bounty-скоуп) + CDN/антифрод/телеметрия + экосистема
    for site in (
        "domain:tinkoff.ru",
        "domain:tinkoff.com",
        "domain:tbank.ru",
        "domain:tcsbank.ru",
        "domain:tinkoffinsurance.ru",
        "domain:cdn-tinkoff.ru",
        "domain:t-static.ru",
        "domain:tmiaf.ru",
        "domain:data-tscbank.ru",
        "domain:t-calls.ru",
        "domain:tinkoffmobile.com",
        "domain:dolyame.ru",
        "domain:t-bank-app.ru",
    ):
        assert site in direct
    # без дублей — каждое правило встречается ровно один раз
    assert len(direct) == len(set(direct))


def test_routing_link_deterministic() -> None:
    """Same value across calls — same-name profiles are overwritten on update,
    so a stable payload must not create churn for unchanged settings."""
    assert build_routing_header_value() == build_routing_header_value()


# ---------------------------------------------------------------------------
# handle_subscription — header wiring
# ---------------------------------------------------------------------------


class _Row(dict):
    def get(self, key, default=None):
        return super().get(key, default)


class _Pool:
    def __init__(self, row):
        self._row = row

    async def fetchrow(self, *_args, **_kwargs):
        return self._row


class _ProviderWithToken(StubVlessProvider):
    async def get_user_config(self, *, internal_user_id: str):
        return VlessProviderResult(
            outcome=VlessProviderOutcome.SUCCESS,
            config=VlessUserConfig(
                user_uuid="test-uuid",
                subscription_url="https://example.com/sub/test",
                servers=(VlessServerConfig("Литва", "LT", "🇱🇹", "vless://uuid@1.2.3.4:443#lt"),),
            ),
        )


def _client() -> TestClient:
    pool = _Pool(
        _Row(
            internal_user_id="u1",
            state_label="active",
            plan_id=None,
            trial_expires_at=datetime.now(UTC) + timedelta(days=1),
            active_until_utc=None,
            keys_deleted_at=None,
        )
    )
    app = Starlette(routes=[Route("/sub/{token}", handle_subscription, methods=["GET"])])
    app.state.pool = pool
    app.state.vless_provider = _ProviderWithToken()
    return TestClient(app)


def _body_links(resp) -> list[str]:
    import base64 as _b64

    return _b64.b64decode(resp.text).decode("utf-8").splitlines()


def test_header_present_for_happ_when_flag_on(monkeypatch) -> None:
    monkeypatch.setattr("app.web_api.subscription._HAPP_ROUTING_ENABLE", True)
    resp = _client().get("/sub/tok", headers={"User-Agent": _HAPP_UA})
    assert resp.status_code == 200
    assert resp.headers["routing"].startswith(_PREFIX)
    # body stays the plain link list — the server picker in Happ is untouched
    assert _body_links(resp) == ["vless://uuid@1.2.3.4:443#lt"]


def test_header_absent_for_other_clients_when_flag_on(monkeypatch) -> None:
    """Karing/v2rayTune imports must not get Happ-specific headers."""
    monkeypatch.setattr("app.web_api.subscription._HAPP_ROUTING_ENABLE", True)
    resp = _client().get("/sub/tok", headers={"User-Agent": "Karing/1.0"})
    assert resp.status_code == 200
    assert "routing" not in resp.headers


def test_header_absent_when_flag_off(monkeypatch) -> None:
    monkeypatch.setattr("app.web_api.subscription._HAPP_ROUTING_ENABLE", False)
    resp = _client().get("/sub/tok", headers={"User-Agent": _HAPP_UA})
    assert resp.status_code == 200
    assert "routing" not in resp.headers


def test_header_present_for_client_param_override(monkeypatch) -> None:
    """?client=happ forces the Happ surface even with a generic UA."""
    monkeypatch.setattr("app.web_api.subscription._HAPP_ROUTING_ENABLE", True)
    resp = _client().get("/sub/tok?client=happ", headers={"User-Agent": "curl/8"})
    assert resp.status_code == 200
    assert resp.headers["routing"].startswith(_PREFIX)
