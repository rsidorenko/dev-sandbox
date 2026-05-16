"""Tests for account merge logic (web-only → Telegram identity)."""

from __future__ import annotations

import hashlib

import pytest

from app.persistence.account_merge import _web_internal_id, _telegram_internal_id


class TestInternalIdDerivation:
    """Pure function tests for ID derivation — no DB needed."""

    def test_web_id_uses_sha256_prefix(self) -> None:
        email = "user@example.com"
        expected = f"web_{hashlib.sha256(email.encode()).hexdigest()[:12]}"
        assert _web_internal_id(email) == expected

    def test_web_id_deterministic(self) -> None:
        assert _web_internal_id("a@b.com") == _web_internal_id("a@b.com")

    def test_web_id_different_for_different_emails(self) -> None:
        assert _web_internal_id("a@b.com") != _web_internal_id("c@d.com")

    def test_telegram_id_format(self) -> None:
        assert _telegram_internal_id(123456) == "u123456"

    def test_telegram_id_different_from_web(self) -> None:
        email = "user@example.com"
        assert _telegram_internal_id(123456) != _web_internal_id(email)


# Integration tests for merge_web_account_if_needed require a real PostgreSQL pool.
# These are covered by the existing Postgres integration test suite
# (test_postgres_*.py) which runs in CI with Docker.
#
# The unit tests above validate the key derivation logic that determines
# which rows to rekey during a merge.
