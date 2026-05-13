"""Tests for issuance.vless_provider: stub provider, subscription URL, key list."""

import asyncio
import base64

from app.issuance.vless_provider import (
    StubVlessProvider,
    VlessProviderOutcome,
    build_subscription_url,
    format_key_list,
)


def _run(coro):
    return asyncio.run(coro)


def test_stub_create_user():
    p = StubVlessProvider()
    result = _run(p.create_user(internal_user_id="user1"))
    assert result.outcome == VlessProviderOutcome.SUCCESS
    assert result.config is not None
    assert len(result.config.servers) > 0
    assert result.config.subscription_url.startswith("data:text/plain;base64,")


def test_stub_get_config_before_create():
    p = StubVlessProvider()
    result = _run(p.get_user_config(internal_user_id="unknown"))
    assert result.outcome == VlessProviderOutcome.NOT_FOUND


def test_stub_get_config_after_create():
    p = StubVlessProvider()
    _run(p.create_user(internal_user_id="user1"))
    result = _run(p.get_user_config(internal_user_id="user1"))
    assert result.outcome == VlessProviderOutcome.SUCCESS


def test_stub_revoke():
    p = StubVlessProvider()
    _run(p.create_user(internal_user_id="user1"))
    result = _run(p.revoke_user(internal_user_id="user1"))
    assert result.outcome == VlessProviderOutcome.SUCCESS
    result = _run(p.get_user_config(internal_user_id="user1"))
    assert result.outcome == VlessProviderOutcome.NOT_FOUND


def test_build_subscription_url_valid_base64():
    from app.issuance.vless_provider import VlessServerConfig

    servers = (
        VlessServerConfig("NL-1", "NL", "\U0001f1f3\U0001f1f1", "vless://abc@nl:443#test"),
        VlessServerConfig("DE-1", "DE", "\U0001f1e9\U0001f1ea", "vless://def@de:443#test2"),
    )
    url = build_subscription_url(servers)
    assert url.startswith("data:text/plain;base64,")
    encoded = url[len("data:text/plain;base64,") :]
    decoded = base64.b64decode(encoded).decode("utf-8")
    assert "vless://abc@nl:443#test" in decoded
    assert "vless://def@de:443#test2" in decoded


def test_format_key_list():
    from app.issuance.vless_provider import VlessServerConfig

    servers = (VlessServerConfig("NL-1", "NL", "\U0001f1f3\U0001f1f1", "vless://abc@nl:443#test"),)
    text = format_key_list(servers)
    assert "NL-1" in text
    assert "vless://abc@nl:443#test" in text
