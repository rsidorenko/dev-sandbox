"""Static guard: production-shaped entrypoints must not introduce public billing HTTP ingress.

Aligned with docs/architecture/32-public-billing-ingress-decisions-adr.md §J and §N, and
docs/architecture/31-public-billing-ingress-security.md: public billing webhook HTTP remains
blocked until checklist and numeric gates are resolved; this test does **not** start any
listener or import ASGI servers.

Operator billing CLIs (:mod:`app.application.billing_ingestion_main`,
:mod:`app.application.billing_subscription_apply_main`) are expected to stay file/stdio-only
without HTTP stack signals. ADM-01 internal HTTP (:mod:`app.internal_admin.adm01_http_main`)
may use HTTP server primitives but must not introduce *public billing* ingress phrasing.

Sources are read from disk under ``backend/src/app/`` to avoid importing modules that pull in
uvicorn or runtime loops.
"""

from __future__ import annotations

from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_APP_SRC = _BACKEND_ROOT / "src" / "app"

# Substrings indicating *public* / webhook-style billing HTTP (case-insensitive scan).
_PUBLIC_BILLING_HTTP_SIGNALS: tuple[str, ...] = (
    "billing_webhook",
    "public_billing",
    "billing ingress",
    "billing_ingress",
    "/billing",
    "/webhook/billing",
    "billing_webhook_path",
)

# HTTP route / listener stack primitives (case-insensitive).
_HTTP_LISTENER_OR_ROUTE_SIGNALS: tuple[str, ...] = (
    "starlette(",
    "route(",
    "mount(",
    "uvicorn",
    ".add_route",
    ".route(",
    "asgi",
)

_RUNTIME_AND_HTTP_ENTRY_SOURCES: dict[str, Path] = {
    "app.runtime.runner": _APP_SRC / "runtime" / "runner.py",
    "app.runtime.__main__": _APP_SRC / "runtime" / "__main__.py",
    "app.runtime.telegram_httpx_live_main": _APP_SRC / "runtime" / "telegram_httpx_live_main.py",
    "app.internal_admin.adm01_http_main": _APP_SRC / "internal_admin" / "adm01_http_main.py",
    "app.internal_admin.__main__": _APP_SRC / "internal_admin" / "__main__.py",
}

_BILLING_OPERATOR_CLI_SOURCES: dict[str, Path] = {
    "app.application.billing_ingestion_main": _APP_SRC / "application" / "billing_ingestion_main.py",
    "app.application.billing_subscription_apply_main": _APP_SRC / "application" / "billing_subscription_apply_main.py",
}


def _read_lower(path: Path) -> str:
    assert path.is_file(), f"missing source file: {path}"
    return path.read_text(encoding="utf-8").lower()


def _contains_any(haystack_lower: str, needles: tuple[str, ...]) -> bool:
    return any(n in haystack_lower for n in needles)


def _assert_paths_exist(mapping: dict[str, Path]) -> None:
    for name, path in mapping.items():
        assert path.is_file(), f"{name}: expected {path}"


def test_production_runtime_http_entry_modules_have_no_public_billing_http_ingress_combo() -> None:
    """Fail if public-billing-ingress wording co-occurs with HTTP listener/route primitives."""
    _assert_paths_exist(_RUNTIME_AND_HTTP_ENTRY_SOURCES)
    for module_name, path in _RUNTIME_AND_HTTP_ENTRY_SOURCES.items():
        lower = _read_lower(path)
        pub = _contains_any(lower, _PUBLIC_BILLING_HTTP_SIGNALS)
        http = _contains_any(lower, _HTTP_LISTENER_OR_ROUTE_SIGNALS)
        assert not (pub and http), (
            f"{module_name}: public billing HTTP ingress signals must not combine with "
            f"HTTP listener/route primitives (public={pub!r}, http_stack={http!r})"
        )


def test_billing_operator_mains_remain_cli_only_no_http_listener_signals() -> None:
    """Operator billing mains may mention billing but must not embed HTTP ASGI/Starlette/uvicorn."""
    _assert_paths_exist(_BILLING_OPERATOR_CLI_SOURCES)
    for module_name, path in _BILLING_OPERATOR_CLI_SOURCES.items():
        lower = _read_lower(path)
        assert not _contains_any(lower, _HTTP_LISTENER_OR_ROUTE_SIGNALS), (
            f"{module_name}: billing operator CLI must not contain HTTP listener/route stack signals"
        )


def test_adm01_internal_http_source_has_no_public_billing_ingress_signals() -> None:
    """ADM-01 may run uvicorn; it must not be framed as public billing webhook ingress."""
    path = _APP_SRC / "internal_admin" / "adm01_http_main.py"
    lower = _read_lower(path)
    assert not _contains_any(lower, _PUBLIC_BILLING_HTTP_SIGNALS), (
        "adm01_http_main must not contain public billing webhook / ingress phrasing"
    )
