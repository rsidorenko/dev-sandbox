"""Tests for standalone ADM-01 internal HTTP entry (no real listener, no Postgres)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from starlette.applications import Starlette

from app.admin_support.adm01_internal_http import ADM01_INTERNAL_LOOKUP_PATH
from app.admin_support.adm01_postgres_subscription_read_adapter import (
    Adm01PostgresSubscriptionReadAdapter,
)
from app.admin_support.adm01_subscription_entitlement_read_adapter import (
    Adm01SubscriptionEntitlementReadAdapter,
)
from app.admin_support.adm01_subscription_policy_read_adapter import (
    Adm01SubscriptionPolicyReadAdapter,
)
from app.admin_support.adm02_ensure_access_audit_logging import (
    FanoutAdm02EnsureAccessAuditSink,
    StructuredLoggingAdm02EnsureAccessAuditSink,
)
from app.admin_support.adm02_ensure_access_audit_postgres import (
    PostgresAdm02EnsureAccessAuditSink,
)
from app.admin_support.adm02_internal_http import ADM02_INTERNAL_AUDIT_EVENTS_PATH, ADM02_INTERNAL_ENSURE_ACCESS_PATH
from app.internal_admin import adm01_http_main as main_mod
from app.security.config import RuntimeConfig
from app.shared.correlation import new_correlation_id


class _FakePool:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


def _runtime_with_dsn() -> RuntimeConfig:
    return RuntimeConfig(
        bot_token="x" * 12,
        database_url="postgresql://localhost/testdb",
        app_env="development",
        debug_safe=False,
    )


@pytest.mark.asyncio
async def test_disabled_by_default_returns_zero_no_side_effects(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ADM01_INTERNAL_HTTP_ENABLE", raising=False)
    monkeypatch.delenv("ADM01_INTERNAL_HTTP_ALLOWLIST", raising=False)

    def boom_runtime() -> RuntimeConfig:
        raise AssertionError("load_runtime_config must not be called when ADM-01 HTTP disabled")

    monkeypatch.setattr(main_mod, "load_runtime_config", boom_runtime)

    r = await main_mod.async_run_adm01_internal_http_from_env(load_runtime=boom_runtime)
    assert r == 0
    out, err = capsys.readouterr()
    assert out.strip() == main_mod._STDOUT_DISABLED
    assert err == ""


@pytest.mark.asyncio
async def test_config_error_invalid_boolean_no_secret_echo(monkeypatch, capsys) -> None:
    secret = "SECRET_VALUE_XYZ_992"
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", secret)

    r = await main_mod.async_run_adm01_internal_http_from_env()
    assert r == 1
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == main_mod._STDERR_CONFIG
    assert secret not in err
    assert secret not in out


@pytest.mark.asyncio
async def test_enabled_happy_path_order_and_pool_closed(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", " adm-principal-a , adm-principal-b ")
    monkeypatch.setenv("BOT_TOKEN", "y" * 15)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")

    calls: list[str] = []
    fake = _FakePool()
    asgi_app = MagicMock(name="asgi_app")

    async def track_migrations(rt: RuntimeConfig) -> None:
        calls.append("migrations")

    async def track_pool(dsn: str) -> object:
        calls.append(f"pool:{dsn}")
        return fake

    uv_calls: list[tuple[str, int]] = []

    async def track_uvicorn(app: object, *, host: str, port: int) -> None:
        uv_calls.append((host, port))
        assert app is asgi_app
        calls.append("uvicorn")

    def track_build(deps: object) -> MagicMock:
        calls.append("build_app")
        assert isinstance(
            deps.subscription,
            Adm01PostgresSubscriptionReadAdapter,
        )
        assert isinstance(deps.entitlement, Adm01SubscriptionEntitlementReadAdapter)
        assert isinstance(deps.policy, Adm01SubscriptionPolicyReadAdapter)
        return asgi_app

    r = await main_mod.async_run_adm01_internal_http_from_env(
        apply_migrations=track_migrations,
        create_pool=track_pool,
        build_app=track_build,
        run_uvicorn=track_uvicorn,
    )
    assert r == 0
    assert calls == ["migrations", "pool:postgresql://localhost/testdb", "build_app", "uvicorn"]
    assert uv_calls == [("127.0.0.1", 18081)]
    assert fake.closed is True
    out, err = capsys.readouterr()
    assert out == "" and err == ""


@pytest.mark.asyncio
async def test_startup_failure_after_pool_closes_pool(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "p1")
    monkeypatch.setenv("BOT_TOKEN", "z" * 14)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")

    fake = _FakePool()

    async def ok_migrations(rt: RuntimeConfig) -> None:
        return None

    async def open_pool(dsn: str) -> object:
        return fake

    async def boom_uvicorn(app: object, *, host: str, port: int) -> None:
        raise RuntimeError("simulated uvicorn failure")

    r = await main_mod.async_run_adm01_internal_http_from_env(
        load_runtime=lambda: _runtime_with_dsn(),
        apply_migrations=ok_migrations,
        create_pool=open_pool,
        run_uvicorn=boom_uvicorn,
    )
    assert r == 1
    assert fake.closed is True
    out, err = capsys.readouterr()
    assert out == ""
    assert err.strip() == main_mod._STDERR_FAILED
    assert "simulated" not in err
    assert "Traceback" not in err


@pytest.mark.asyncio
async def test_bind_all_interfaces_without_override_config_error_no_uvicorn(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_BIND_HOST", "0.0.0.0")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "p1")

    async def boom_uvicorn(app: object, *, host: str, port: int) -> None:
        raise AssertionError("uvicorn must not run when bind config is rejected")

    r = await main_mod.async_run_adm01_internal_http_from_env(run_uvicorn=boom_uvicorn)
    assert r == 1
    _out, err = capsys.readouterr()
    assert err.strip() == main_mod._STDERR_CONFIG


@pytest.mark.asyncio
async def test_missing_allowlist_when_enabled_config_error(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.delenv("ADM01_INTERNAL_HTTP_ALLOWLIST", raising=False)
    monkeypatch.setenv("BOT_TOKEN", "a" * 20)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")

    async def no_migrations(rt: RuntimeConfig) -> None:
        raise AssertionError("migrations must not run without allowlist")

    async def boom_uvicorn(app: object, *, host: str, port: int) -> None:
        raise AssertionError("uvicorn must not run without allowlist")

    r = await main_mod.async_run_adm01_internal_http_from_env(
        apply_migrations=no_migrations,
        run_uvicorn=boom_uvicorn,
    )
    assert r == 1
    _, err = capsys.readouterr()
    assert err.strip() == main_mod._STDERR_CONFIG


@pytest.mark.asyncio
async def test_output_leak_guard_disabled_path(capsys) -> None:
    await main_mod.async_run_adm01_internal_http_from_env()
    out, err = capsys.readouterr()
    combined = out + err
    for frag in (
        "DATABASE_URL",
        "postgres://",
        "postgresql://",
        "Bearer ",
        "provider_issuance_ref",
        "issue_idempotency_key",
    ):
        assert frag not in combined


@pytest.mark.asyncio
async def test_output_leak_guard_config_error_path(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "not-a-bool")

    await main_mod.async_run_adm01_internal_http_from_env()
    combined = capsys.readouterr().out + capsys.readouterr().err
    for frag in (
        "DATABASE_URL",
        "postgres://",
        "postgresql://",
        "Bearer ",
        "provider_issuance_ref",
        "issue_idempotency_key",
    ):
        assert frag not in combined


def test_main_disabled_integration(monkeypatch, capsys) -> None:
    monkeypatch.delenv("ADM01_INTERNAL_HTTP_ENABLE", raising=False)
    assert main_mod.main() == 0
    out, err = capsys.readouterr()
    assert out.strip() == main_mod._STDOUT_DISABLED
    assert err == ""


@pytest.mark.asyncio
async def test_apply_migrations_failure_returns_failed(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "p1")
    monkeypatch.setenv("BOT_TOKEN", "b" * 18)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/x")

    async def boom_migrations(rt: RuntimeConfig) -> None:
        raise RuntimeError("migration failure")

    r = await main_mod.async_run_adm01_internal_http_from_env(apply_migrations=boom_migrations)
    assert r == 1
    assert capsys.readouterr().err.strip() == main_mod._STDERR_FAILED


@pytest.mark.asyncio
async def test_build_app_failure_closes_pool(monkeypatch, capsys) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "p1")
    fake = _FakePool()

    async def ok_migrations(rt: RuntimeConfig) -> None:
        return None

    async def open_pool(dsn: str) -> object:
        return fake

    def boom_build(deps: object) -> MagicMock:
        raise RuntimeError("build failure")

    async def boom_uvicorn(app: object, *, host: str, port: int) -> None:
        raise AssertionError("uvicorn must not run if build fails")

    r = await main_mod.async_run_adm01_internal_http_from_env(
        load_runtime=lambda: _runtime_with_dsn(),
        apply_migrations=ok_migrations,
        create_pool=open_pool,
        build_app=boom_build,
        run_uvicorn=boom_uvicorn,
    )
    assert r == 1
    assert fake.closed is True
    assert capsys.readouterr().err.strip() == main_mod._STDERR_FAILED


@pytest.mark.asyncio
async def test_adm02_ensure_access_route_not_wired_without_env_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "adm-x")
    monkeypatch.setenv("BOT_TOKEN", "x" * 16)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")
    monkeypatch.delenv("ADM02_ENSURE_ACCESS_ENABLE", raising=False)

    class _FakePool:
        async def close(self) -> None:
            return None

    captured_app: Starlette | None = None

    async def _ok_migrations(rt: RuntimeConfig) -> None:
        return None

    async def _open_pool(dsn: str) -> object:
        return _FakePool()

    async def _capture_run(app: object, *, host: str, port: int) -> None:
        del host, port
        nonlocal captured_app
        assert isinstance(app, Starlette)
        captured_app = app

    rc = await main_mod.async_run_adm01_internal_http_from_env(
        load_runtime=lambda: _runtime_with_dsn(),
        apply_migrations=_ok_migrations,
        create_pool=_open_pool,
        run_uvicorn=_capture_run,
    )
    assert rc == 0
    assert captured_app is not None
    paths = {route.path for route in captured_app.routes}
    assert ADM01_INTERNAL_LOOKUP_PATH in paths
    assert ADM02_INTERNAL_AUDIT_EVENTS_PATH in paths
    assert ADM02_INTERNAL_ENSURE_ACCESS_PATH not in paths


@pytest.mark.asyncio
async def test_adm02_ensure_access_route_wired_with_env_opt_in_and_unauthorized_denied(monkeypatch) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "adm-allow")
    monkeypatch.setenv("ADM02_ENSURE_ACCESS_ENABLE", "yes")
    monkeypatch.setenv("BOT_TOKEN", "x" * 16)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")

    class _FakePool:
        async def close(self) -> None:
            return None

    captured_app: Starlette | None = None

    async def _ok_migrations(rt: RuntimeConfig) -> None:
        return None

    async def _open_pool(dsn: str) -> object:
        return _FakePool()

    async def _capture_run(app: object, *, host: str, port: int) -> None:
        del host, port
        nonlocal captured_app
        assert isinstance(app, Starlette)
        captured_app = app

    rc = await main_mod.async_run_adm01_internal_http_from_env(
        load_runtime=lambda: _runtime_with_dsn(),
        apply_migrations=_ok_migrations,
        create_pool=_open_pool,
        run_uvicorn=_capture_run,
    )
    assert rc == 0
    assert captured_app is not None
    paths = {route.path for route in captured_app.routes}
    assert ADM01_INTERNAL_LOOKUP_PATH in paths
    assert ADM02_INTERNAL_AUDIT_EVENTS_PATH in paths
    assert ADM02_INTERNAL_ENSURE_ACCESS_PATH in paths

    ensure_route = next(route for route in captured_app.routes if route.path == ADM02_INTERNAL_ENSURE_ACCESS_PATH)
    cid = new_correlation_id()
    dummy_receive = {
        "type": "http.request",
        "body": (
            f'{{"correlation_id":"{cid}","internal_admin_principal_id":"intruder","telegram_user_id":42}}'
        ).encode(),
        "more_body": False,
    }
    events: list[dict] = []

    async def _receive() -> dict:
        return dummy_receive

    async def _send(message: dict) -> None:
        events.append(message)

    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "POST",
        "path": ADM02_INTERNAL_ENSURE_ACCESS_PATH,
        "raw_path": ADM02_INTERNAL_ENSURE_ACCESS_PATH.encode("utf-8"),
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("test", 1234),
        "server": ("test", 80),
        "scheme": "http",
    }
    await ensure_route.app(scope, _receive, _send)
    body_bytes = b"".join(evt.get("body", b"") for evt in events if evt["type"] == "http.response.body")
    text = body_bytes.decode("utf-8")
    assert '"outcome":"denied"' in text.replace(" ", "")
    lowered = text.lower()
    for forbidden in (
        "database_url",
        "postgres://",
        "postgresql://",
        "bearer ",
        "private key",
        "begin ",
        "token=",
        "vpn://",
        "provider_issuance_ref",
        "issue_idempotency_key",
        "schema_version",
        "customer_ref",
        "provider_ref",
        "checkout_attempt_id",
        "internal_user_id",
    ):
        assert forbidden not in lowered


@pytest.mark.asyncio
async def test_adm02_opt_in_wires_durable_postgres_with_structured_fallback(monkeypatch) -> None:
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ENABLE", "1")
    monkeypatch.setenv("ADM01_INTERNAL_HTTP_ALLOWLIST", "adm-allow")
    monkeypatch.setenv("ADM02_ENSURE_ACCESS_ENABLE", "1")
    monkeypatch.setenv("BOT_TOKEN", "x" * 16)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("DATABASE_URL", "postgresql://localhost/testdb")

    class _FakePool:
        async def close(self) -> None:
            return None

    async def _ok_migrations(rt: RuntimeConfig) -> None:
        return None

    async def _open_pool(dsn: str) -> object:
        return _FakePool()

    async def _capture_run(app: object, *, host: str, port: int) -> None:
        del app, host, port
        return

    captured: dict[str, object] = {}

    def fake_build_adm02_ensure_access_handler(**kwargs):
        captured.update(kwargs)

        class _Handler:
            async def handle(self, inp):
                _ = inp
                raise AssertionError("should not be called")

        return _Handler()

    monkeypatch.setattr(main_mod, "build_adm02_ensure_access_handler", fake_build_adm02_ensure_access_handler)

    rc = await main_mod.async_run_adm01_internal_http_from_env(
        load_runtime=lambda: _runtime_with_dsn(),
        apply_migrations=_ok_migrations,
        create_pool=_open_pool,
        run_uvicorn=_capture_run,
    )
    assert rc == 0
    assert "audit" in captured
    assert isinstance(captured["audit"], FanoutAdm02EnsureAccessAuditSink)
    fanout = captured["audit"]
    sinks = fanout._sinks
    assert len(sinks) == 2
    assert isinstance(sinks[0], PostgresAdm02EnsureAccessAuditSink)
    assert isinstance(sinks[1], StructuredLoggingAdm02EnsureAccessAuditSink)
