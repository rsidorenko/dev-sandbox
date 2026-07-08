"""Provider-side tests for the Yandex-Cloud-CDN whitelist-bypass bare_xray row (id=34).

Covers the link_template path (h1cloud-style: full vless:// with {UUID} + `extra`
padding, emitted verbatim) and the per-user gRPC lifecycle dispatch on the Origin
inbound `from-yandex-cdn` (xhttp → empty flow, email `x-user-`).
"""

from __future__ import annotations

from unittest.mock import AsyncMock

from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import XuiVlessProvider, _vless_uuid_for_transport

# Mirrors backend/scripts/add_cdn_whitelist_row.py — the full vless:// the bot emits
# verbatim with {UUID} substituted per-user.
_LINK_TEMPLATE = (
    "vless://{UUID}@cdn.strimingset.ru:443"
    "?type=xhttp&security=tls&sni=cdn.strimingset.ru&host=cdn.strimingset.ru"
    "&path=%2Fapi-test&fp=firefox&alpn=h2%2Chttp%2F1.1&mode=packet-up"
    "&extra=%7B%22mode%22%3A%22packet-up%22%2C%22scMaxEachPostBytes%22%3A1000000"
    "%2C%22scMinPostsIntervalMs%22%3A30%2C%22scMaxBufferedPosts%22%3A30"
    "%2C%22xPaddingObfsMode%22%3Atrue%2C%22xPaddingKey%22%3A%22dc%22"
    "%2C%22xPaddingHeader%22%3A%22X-Cache%22%2C%22xPaddingMethod%22%3A%22tokenish%22"
    "%2C%22xPaddingPlacement%22%3A%22queryInHeader%22%2C%22uplinkHTTPMethod%22%3A%22OPTIONS%22%7D"
    "#%F0%9F%9B%A1%EF%B8%8F%20%D0%9E%D0%B1%D1%85%D0%BE%D0%B4%20CDN"
)


def _cdn_config() -> XuiServerConfig:
    return XuiServerConfig(
        server_id=34,
        label="Обход CDN",
        country_code="",
        country_flag="\U0001f310",
        server_host="158.160.149.191",  # Origin — SSH target (cloudru_key.pem/romtan)
        server_port=443,  # CDN-facing port; link_template overrides host/port
        ws_path="api-test",
        tls_sni=None,
        panel_url="",
        panel_username="",
        panel_password="",
        inbound_id=0,
        reality_pbk="",  # unused — link_template carries everything
        reality_sid="",
        reality_sni="",
        reality_fp="firefox",
        transport_type="xhttp",
        provider_kind="bare_xray",
        xray_inbound_tag="from-yandex-cdn",
        client_surface="happ",
        link_template=_LINK_TEMPLATE,
    )


def _provider_with_cdn() -> XuiVlessProvider:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._bare_xray_configs = (_cdn_config(),)
    return p


# --- link building ---------------------------------------------------------


async def test_cdn_whitelist_link_emitted_with_extra_and_per_user_uuid() -> None:
    p = _provider_with_cdn()
    uid = "uABC123456789012"

    servers = await p._bare_xray_servers(uid)

    assert len(servers) == 1
    s = servers[0]
    assert s.surface == "happ"
    link = s.vless_link

    # Per-user xhttp UUID substituted into the {UUID} placeholder.
    uuid = _vless_uuid_for_transport(uid, "xhttp")
    assert "{UUID}" not in link  # placeholder fully replaced
    assert link.startswith(f"vless://{uuid}@cdn.strimingset.ru:443?")

    # CDN-fronted xhttp + TLS (NOT reality) + the padding extra + packet-up path.
    assert "type=xhttp" in link
    assert "security=tls" in link
    assert "sni=cdn.strimingset.ru" in link
    assert "host=cdn.strimingset.ru" in link
    assert "path=%2Fapi-test" in link
    assert "fp=firefox" in link
    assert "extra=" in link
    # xPadding obfuscation markers survive URL-encoding inside extra.
    assert "xPaddingObfsMode" in link
    assert "uplinkHTTPMethod" in link

    # Distinct UUID per transport (xhttp vs tcp) — guards the per-transport dispatch.
    assert uuid != _vless_uuid_for_transport(uid, "tcp")


async def test_cdn_whitelist_empty_when_no_bare_xray() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    p._bare_xray_configs = ()
    assert await p._bare_xray_servers("u1") == []


# --- lifecycle dispatch ----------------------------------------------------


async def test_cdn_whitelist_lifecycle_add_dispatches_to_origin_inbound() -> None:
    """add → gRPC AlterInbound AddUser on the Origin `from-yandex-cdn` inbound:
    xhttp transport → empty flow, email `x-user-<uid>`, per-transport xhttp UUID."""
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    mock = AsyncMock()
    mock.server_config = _cdn_config()
    mock.server_id = 34
    mock.add_client = AsyncMock(return_value=True)
    mock.remove_client = AsyncMock(return_value=True)
    p._bare_xray_clients = (mock,)  # bypass env-driven _get_bare_xray_clients
    uid = "u999"

    await p._bare_xray_lifecycle(uid, "add")

    mock.add_client.assert_awaited_once_with(
        user_uuid=_vless_uuid_for_transport(uid, "xhttp"),
        email=f"x-user-{uid[:16]}",
    )
    mock.remove_client.assert_not_awaited()


async def test_cdn_whitelist_lifecycle_remove_dispatches() -> None:
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    mock = AsyncMock()
    mock.server_config = _cdn_config()
    mock.server_id = 34
    mock.add_client = AsyncMock(return_value=True)
    mock.remove_client = AsyncMock(return_value=True)
    p._bare_xray_clients = (mock,)
    uid = "u999"

    await p._bare_xray_lifecycle(uid, "remove")

    mock.remove_client.assert_awaited_once_with(email=f"x-user-{uid[:16]}")
    mock.add_client.assert_not_awaited()


async def test_cdn_whitelist_lifecycle_swallows_failure() -> None:
    """A failing Origin gRPC op must not crash the bot (reconcile re-applies)."""
    p = XuiVlessProvider(pool=None)  # type: ignore[arg-type]
    bad = AsyncMock()
    bad.server_config = _cdn_config()
    bad.server_id = 34
    bad.add_client = AsyncMock(side_effect=RuntimeError("grpcurl down"))
    p._bare_xray_clients = (bad,)

    await p._bare_xray_lifecycle("u1", "add")  # must not raise
