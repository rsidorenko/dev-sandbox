"""Регрессии production-конфигурации публичного сайта и канала security review."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_VERIFICATION_VARIABLE = "NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION"


def test_google_verification_reaches_next_production_build() -> None:
    dockerfile = (_ROOT / "frontend" / "Dockerfile").read_text()
    assert f"ARG {_VERIFICATION_VARIABLE}" in dockerfile
    assert f"ENV {_VERIFICATION_VARIABLE}=${_VERIFICATION_VARIABLE}" in dockerfile

    for compose_name in ("docker-compose.yml", "docker-compose.prod.yml"):
        compose = (_ROOT / compose_name).read_text()
        assert f"{_VERIFICATION_VARIABLE}: ${{{_VERIFICATION_VARIABLE}:-}}" in compose


def test_public_host_is_canonical_and_has_security_contact() -> None:
    nginx = (_ROOT / "nginx" / "conf.d" / "default.conf").read_text()
    assert "server_name www.bravada-connect.ru;" in nginx
    assert "return 301 https://bravada-connect.ru$request_uri;" in nginx
    assert "server_name bravada-connect.ru;" in nginx

    security_txt = (_ROOT / "frontend" / "public" / ".well-known" / "security.txt").read_text()
    assert "Contact: mailto:bravadavpn_sup@mail.ru" in security_txt
    assert "Canonical: https://bravada-connect.ru/.well-known/security.txt" in security_txt
    expires_line = next(line for line in security_txt.splitlines() if line.startswith("Expires: "))
    expires_at = datetime.fromisoformat(expires_line.removeprefix("Expires: ").replace("Z", "+00:00"))
    assert expires_at > datetime.now(UTC)


def test_frontend_framework_is_on_patched_supported_line() -> None:
    package = json.loads((_ROOT / "frontend" / "package.json").read_text())
    major, minor, patch = (int(part) for part in package["dependencies"]["next"].split("."))
    assert (major, minor, patch) >= (15, 5, 24)
    assert package["overrides"]["nanoid"] >= "3.3.18"
    assert package["devDependencies"]["postcss"] >= "8.5.23"
    assert package["overrides"]["postcss"] == "$postcss"
