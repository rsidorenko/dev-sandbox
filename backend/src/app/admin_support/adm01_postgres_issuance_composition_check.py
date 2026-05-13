"""In-process ADM-01 + Postgres issuance composition check (no listen socket, no env value logging)."""

from __future__ import annotations

import os
import uuid
from collections.abc import Sequence

import asyncpg
import httpx

from app.admin_support.adm01_internal_http import ADM01_INTERNAL_LOOKUP_PATH
from app.admin_support.contracts import (
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    InternalUserTarget,
)
from app.application.interfaces import SubscriptionSnapshot
from app.internal_admin.adm01_bundle import (
    Adm01InternalLookupWithPostgresIssuanceStateDependencies,
    build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state,
)
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.persistence.postgres_migrations import apply_postgres_migrations, default_migrations_directory
from app.shared.correlation import new_correlation_id

_ENV_ENABLE = "ADM01_POSTGRES_ISSUANCE_COMPOSITION_CHECK_ENABLE"
_ENV_DATABASE_URL = "DATABASE_URL"
_ENV_PRINCIPAL = "ADM01_POSTGRES_ISSUANCE_COMPOSITION_CHECK_PRINCIPAL"
_DEFAULT_ALLOW_PRINCIPAL = "adm01-pg-comp-chk-allow"
_ID_PREFIX = "adm01_comp_chk_"
_FORBIDDEN_SUBSTRINGS = (
    "provider_issuance_ref",
    "issue_idempotency_key",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
)


def adm01_postgres_issuance_composition_check_enabled() -> bool:
    raw = os.environ.get(_ENV_ENABLE, "").strip().lower()
    return raw in ("1", "true", "yes")


def _dsn_from_env() -> str:
    dsn = os.environ.get(_ENV_DATABASE_URL, "").strip()
    if not dsn:
        msg = "missing database configuration"
        raise RuntimeError(msg)
    return dsn


def _allowlist_principal() -> str:
    raw = os.environ.get(_ENV_PRINCIPAL, "").strip()
    return raw or _DEFAULT_ALLOW_PRINCIPAL


def assert_adm01_composition_http_text_safe(
    text: str,
    *,
    synthetic_secret_markers: Sequence[str] = (),
) -> None:
    """Raise :class:`RuntimeError` if ``text`` may leak issuance secrets or disallowed patterns."""
    lower = text.lower()
    for frag in _FORBIDDEN_SUBSTRINGS:
        if frag.lower() in lower:
            msg = "response text failed leak guard"
            raise RuntimeError(msg)
    for marker in synthetic_secret_markers:
        if marker and marker in text:
            msg = "response text failed leak guard"
            raise RuntimeError(msg)


class _IdentityEchoInternalUserId:
    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        if isinstance(target, InternalUserTarget):
            return target.internal_user_id
        return None


class _SubscriptionReadFixed:
    def __init__(self, state_label: str) -> None:
        self._state_label = state_label

    async def get_subscription_snapshot(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label=self._state_label)


class _EntitlementReadFixed:
    def __init__(self, category: EntitlementSummaryCategory) -> None:
        self._category = category

    async def get_entitlement_summary(self, internal_user_id: str) -> EntitlementSummary:
        return EntitlementSummary(category=self._category)


class _PolicyReadFixed:
    def __init__(self, flag: AdminPolicyFlag) -> None:
        self._flag = flag

    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        return self._flag


async def _post_json(app: object, path: str, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)  # type: ignore[arg-type]
    async with httpx.AsyncClient(transport=transport, base_url="http://adm01_composition.test") as client:
        return await client.post(path, json=payload)


async def run_adm01_postgres_issuance_composition_check() -> None:
    """
    Exercise :class:`Adm01PostgresIssuanceReadAdapter` through the composed ADM-01 Starlette app
    against PostgreSQL. Raises :class:`RuntimeError` on expected check failure.
    """
    if not adm01_postgres_issuance_composition_check_enabled():
        msg = "opt-in not enabled"
        raise RuntimeError(msg)

    dsn = _dsn_from_env()

    principal_ok = _allowlist_principal()
    principal_deny = f"{principal_ok}-deny-not-allowed"
    user_id = f"{_ID_PREFIX}u_{uuid.uuid4().hex[:20]}"
    ikey = f"{_ID_PREFIX}ik_{uuid.uuid4().hex[:20]}"
    opaque_ref = f"opaque_adm01chk_{uuid.uuid4().hex[:24]}"[:64]

    pool: asyncpg.Pool | None = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=2)
        await apply_postgres_migrations(pool, migrations_directory=default_migrations_directory())
        repo = PostgresIssuanceStateRepository(pool)
        _ = await repo.issue_or_get(
            internal_user_id=user_id,
            issue_idempotency_key=ikey,
            provider_issuance_ref=opaque_ref,
        )

        app = build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state(
            Adm01InternalLookupWithPostgresIssuanceStateDependencies(
                identity=_IdentityEchoInternalUserId(),
                subscription=_SubscriptionReadFixed("active"),
                entitlement=_EntitlementReadFixed(EntitlementSummaryCategory.ACTIVE),
                postgres_issuance_state=repo,
                policy=_PolicyReadFixed(AdminPolicyFlag.DEFAULT),
                redaction=None,
                adm01_allowlisted_internal_admin_principal_ids=[principal_ok],
            ),
        )

        cid_ok = new_correlation_id()
        r_ok = await _post_json(
            app,
            ADM01_INTERNAL_LOOKUP_PATH,
            {
                "correlation_id": cid_ok,
                "internal_admin_principal_id": principal_ok,
                "internal_user_id": user_id,
            },
        )
        if r_ok.status_code != 200:
            msg = "allow scenario status"
            raise RuntimeError(msg)
        body_ok = r_ok.json()
        if body_ok.get("outcome") != "success":
            msg = "allow scenario outcome"
            raise RuntimeError(msg)
        summary = body_ok.get("summary")
        if not isinstance(summary, dict):
            msg = "allow scenario summary"
            raise RuntimeError(msg)
        if summary.get("issuance_state") != "ok":
            msg = "allow scenario issuance projection"
            raise RuntimeError(msg)
        assert_adm01_composition_http_text_safe(
            r_ok.text,
            synthetic_secret_markers=(opaque_ref, ikey),
        )

        cid_deny = new_correlation_id()
        r_deny = await _post_json(
            app,
            ADM01_INTERNAL_LOOKUP_PATH,
            {
                "correlation_id": cid_deny,
                "internal_admin_principal_id": principal_deny,
                "internal_user_id": user_id,
            },
        )
        if r_deny.status_code != 200:
            msg = "deny scenario status"
            raise RuntimeError(msg)
        body_deny = r_deny.json()
        if body_deny.get("outcome") != "denied":
            msg = "deny scenario outcome"
            raise RuntimeError(msg)
        if body_deny.get("summary") is not None:
            msg = "deny scenario summary leak"
            raise RuntimeError(msg)
        assert_adm01_composition_http_text_safe(
            r_deny.text,
            synthetic_secret_markers=(opaque_ref, ikey),
        )
    finally:
        if pool is not None:
            try:
                async with pool.acquire() as conn:
                    await conn.execute(
                        "DELETE FROM issuance_state WHERE internal_user_id = $1::text AND issue_idempotency_key = $2::text",
                        user_id,
                        ikey,
                    )
            except (asyncpg.PostgresError, OSError):
                pass
            await pool.close()


__all__ = [
    "adm01_postgres_issuance_composition_check_enabled",
    "assert_adm01_composition_http_text_safe",
    "run_adm01_postgres_issuance_composition_check",
]
