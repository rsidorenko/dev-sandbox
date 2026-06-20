"""Tests for the vk-tunnel LTE entry setup helpers (setup_vk_tunnel).

Pure helpers only (no SSH/DB I/O): the WS-inbound streamSettings builder and the
wss-domain extractor. The streamSettings MUST be security:none — vk-tunnel
terminates TLS at the VK edge and forwards plain WS to the local inbound (clients
still use security=tls in their vless:// link to the VK domain; the mismatch is
intended).
"""

from __future__ import annotations

import json
import os
import sys

import pytest

# Import the operator script from backend/scripts (stdlib-only at import).
_SCRIPTS = os.path.join(os.path.dirname(__file__), "..", "scripts")
sys.path.insert(0, _SCRIPTS)
import setup_vk_tunnel as vkt  # noqa: E402


def test_ws_inbound_stream_settings_structure() -> None:
    stream = json.loads(vkt.ws_inbound_stream_settings())
    assert stream["network"] == "ws"
    assert stream["security"] == "none"  # vk-tunnel does TLS at the VK edge
    assert stream["wsSettings"]["path"] == "/vkt/"
    assert stream["wsSettings"]["acceptProxyProtocol"] is False
    assert stream["externalProxy"] == []


def test_ws_inbound_stream_settings_custom_path(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(vkt, "VKT_WS_PATH", "/secret-path/")
    stream = json.loads(vkt.ws_inbound_stream_settings())
    assert stream["wsSettings"]["path"] == "/secret-path/"


def test_ws_inbound_stream_settings_is_json_serializable_roundtrip() -> None:
    # 3x-ui stores streamSettings as a JSON string column; must round-trip cleanly.
    raw = vkt.ws_inbound_stream_settings()
    assert json.loads(raw) == json.loads(json.dumps(json.loads(raw)))


def test_extract_wss_domain() -> None:
    log = (
        "[INFO] vk-tunnel starting\n"
        "Tunnel is ready: https://abc123.vk-platform.ru, wss://abc123.vk-platform.ru\n"
    )
    assert vkt.extract_wss_domain(log) == "abc123.vk-platform.ru"


def test_extract_wss_domain_returns_last_match() -> None:
    # On a restart VK may log the old then the new domain; extractor takes the last.
    log = "wss://old.vk-platform.ru ready\n... restart ...\nwss://new.vk-platform.ru ready\n"
    assert vkt.extract_wss_domain(log) == "new.vk-platform.ru"


@pytest.mark.parametrize("text", ["", "no domains here", "https://foo.vk-platform.ru only"])
def test_extract_wss_domain_none_when_absent(text: str) -> None:
    assert vkt.extract_wss_domain(text) is None
