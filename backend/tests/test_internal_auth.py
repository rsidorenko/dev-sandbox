"""Tests for internal admin shared-secret authentication.

Covers the fail-closed-in-production behavior: a missing ``ADM_INTERNAL_SECRET``
rejects admin endpoints (ADM-01 lookup, ADM-02 access mutation) with 401 in any
production ``APP_ENV``, while remaining optional in local/test ``APP_ENV`` (and
when ``APP_ENV`` is unset, which the codebase treats as ``"development"``).
"""

from __future__ import annotations

from app.admin_support.internal_auth import verify_internal_admin_secret


class _FakeRequest:
    """Minimal stand-in for starlette.Request — only ``headers`` is read."""

    def __init__(self, headers: dict[str, str] | None = None) -> None:
        self.headers = headers or {}


_GOOD_SECRET = "a-very-strong-admin-secret-12345"


def test_production_env_without_secret_is_rejected(monkeypatch) -> None:
    monkeypatch.delenv("ADM_INTERNAL_SECRET", raising=False)
    resp = verify_internal_admin_secret(_FakeRequest(), app_env="production")
    assert resp is not None
    assert resp.status_code == 401


def test_unset_app_env_defaults_to_local_so_allowed_without_secret(monkeypatch) -> None:
    # Unset APP_ENV ⇒ "development" (local) ⇒ secret optional (codebase convention).
    monkeypatch.delenv("ADM_INTERNAL_SECRET", raising=False)
    monkeypatch.delenv("APP_ENV", raising=False)
    assert verify_internal_admin_secret(_FakeRequest()) is None


def test_local_env_without_secret_is_allowed(monkeypatch) -> None:
    monkeypatch.delenv("ADM_INTERNAL_SECRET", raising=False)
    assert verify_internal_admin_secret(_FakeRequest(), app_env="test") is None
    assert verify_internal_admin_secret(_FakeRequest(), app_env="development") is None


def test_production_env_with_correct_secret_is_allowed(monkeypatch) -> None:
    monkeypatch.setenv("ADM_INTERNAL_SECRET", _GOOD_SECRET)
    req = _FakeRequest({"X-Admin-Secret": _GOOD_SECRET})
    assert verify_internal_admin_secret(req, app_env="production") is None


def test_production_env_with_wrong_secret_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ADM_INTERNAL_SECRET", _GOOD_SECRET)
    resp = verify_internal_admin_secret(_FakeRequest({"X-Admin-Secret": "wrong"}), app_env="production")
    assert resp is not None
    assert resp.status_code == 401


def test_production_env_with_missing_header_is_rejected(monkeypatch) -> None:
    monkeypatch.setenv("ADM_INTERNAL_SECRET", _GOOD_SECRET)
    resp = verify_internal_admin_secret(_FakeRequest(), app_env="production")
    assert resp is not None
    assert resp.status_code == 401


def test_local_env_with_configured_secret_still_validates_header(monkeypatch) -> None:
    # When a secret IS configured (even locally), the header must still match.
    monkeypatch.setenv("ADM_INTERNAL_SECRET", _GOOD_SECRET)
    resp = verify_internal_admin_secret(_FakeRequest({"X-Admin-Secret": "wrong"}), app_env="development")
    assert resp is not None
    assert resp.status_code == 401
