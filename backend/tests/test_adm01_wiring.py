"""Composition wiring for ADM-01: explicit ports + :class:`Adm01PostgresIssuanceReadAdapter` (no default runtime)."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx

from app.admin_support.adm01_internal_http import ADM01_INTERNAL_LOOKUP_PATH
from app.admin_support.adm01_postgres_issuance_read_adapter import Adm01PostgresIssuanceReadAdapter
from app.admin_support.adm01_postgres_subscription_read_adapter import Adm01PostgresSubscriptionReadAdapter
from app.admin_support.adm01_subscription_entitlement_read_adapter import (
    Adm01SubscriptionEntitlementReadAdapter,
)
from app.admin_support.adm01_subscription_policy_read_adapter import (
    Adm01SubscriptionPolicyReadAdapter,
)
from app.admin_support.adm01_wiring import (
    build_adm01_entitlement_read_from_postgres_snapshots,
    build_adm01_internal_lookup_http_app,
    build_adm01_issuance_read_from_postgres_issuance_state,
    build_adm01_policy_read_from_postgres_snapshots,
    build_adm01_subscription_read_from_postgres_snapshots,
)
from app.admin_support.contracts import (
    AdminPolicyFlag,
    EntitlementSummary,
    EntitlementSummaryCategory,
    InternalUserTarget,
)
from app.application.interfaces import SubscriptionSnapshot
from app.internal_admin.adm01_bundle import (
    Adm01InternalLookupDependencies,
    Adm01InternalLookupWithPostgresIssuanceStateDependencies,
    build_adm01_internal_lookup_starlette_app,
    build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state,
)
from app.persistence.issuance_state_record import IssuanceStatePersistence, IssuanceStateRow
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run

_REF_MUST_NOT_LEAK = "issuance-ref:unit-wiring:cursor-leaktest-SECRET-SUFFIX-xyz"[:64]
_IDEM_MUST_NOT_LEAK = "idem-wiring-super-secret-key-12345"
_TS = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


def _row_issued() -> IssuanceStateRow:
    return IssuanceStateRow(
        internal_user_id="u-777",
        issue_idempotency_key=_IDEM_MUST_NOT_LEAK,
        state=IssuanceStatePersistence.ISSUED,
        provider_issuance_ref=_REF_MUST_NOT_LEAK,
        created_at=_TS,
        updated_at=_TS,
        revoked_at=None,
    )


def _row_revoked() -> IssuanceStateRow:
    r = _row_issued()
    return IssuanceStateRow(
        internal_user_id=r.internal_user_id,
        issue_idempotency_key=r.issue_idempotency_key,
        state=IssuanceStatePersistence.REVOKED,
        provider_issuance_ref=r.provider_issuance_ref,
        created_at=r.created_at,
        updated_at=_TS,
        revoked_at=_TS,
    )


class _IdentityEcho:
    async def resolve_internal_user_id(self, target, *, correlation_id: str) -> str | None:
        if isinstance(target, InternalUserTarget):
            return target.internal_user_id
        return None


class _Subscription:
    def __init__(self, label: str) -> None:
        self._label = label

    async def get_subscription_snapshot(self, internal_user_id: str) -> SubscriptionSnapshot | None:
        return SubscriptionSnapshot(internal_user_id=internal_user_id, state_label=self._label)


class _Entitlement:
    def __init__(self, category: EntitlementSummaryCategory) -> None:
        self._category = category

    async def get_entitlement_summary(self, internal_user_id: str) -> EntitlementSummary:
        return EntitlementSummary(category=self._category)


class _Policy:
    def __init__(self, flag: AdminPolicyFlag) -> None:
        self._flag = flag

    async def get_policy_flag(self, internal_user_id: str) -> AdminPolicyFlag:
        return self._flag


class _IssuanceRepoFake:
    def __init__(self, current: IssuanceStateRow | None) -> None:
        self._current = current
        self.get_current_calls = 0

    async def get_current_for_user(self, internal_user_id: str) -> IssuanceStateRow | None:
        self.get_current_calls += 1
        del internal_user_id
        return self._current


async def _post_json(app, payload: dict) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://wiring.test") as client:
        return await client.post(ADM01_INTERNAL_LOOKUP_PATH, json=payload)


def _assert_json_has_no_secrets(text: str) -> None:
    assert _REF_MUST_NOT_LEAK not in text
    assert _IDEM_MUST_NOT_LEAK not in text
    assert "provider_issuance_ref" not in text
    assert "issue_idempotency_key" not in text


def test_postgres_issuance_helper_wraps_repository() -> None:
    spec = MagicMock(spec=PostgresIssuanceStateRepository)
    p = build_adm01_issuance_read_from_postgres_issuance_state(spec)
    assert isinstance(p, Adm01PostgresIssuanceReadAdapter)


def test_postgres_subscription_helper_wraps_reader() -> None:
    spec = MagicMock(spec=PostgresSubscriptionSnapshotReader)
    p = build_adm01_subscription_read_from_postgres_snapshots(spec)
    assert isinstance(p, Adm01PostgresSubscriptionReadAdapter)


def test_postgres_entitlement_helper_wraps_reader() -> None:
    spec = MagicMock(spec=PostgresSubscriptionSnapshotReader)
    p = build_adm01_entitlement_read_from_postgres_snapshots(spec)
    assert isinstance(p, Adm01SubscriptionEntitlementReadAdapter)


def test_postgres_policy_helper_wraps_reader() -> None:
    spec = MagicMock(spec=PostgresSubscriptionSnapshotReader)
    p = build_adm01_policy_read_from_postgres_snapshots(spec)
    assert isinstance(p, Adm01SubscriptionPolicyReadAdapter)


def test_wiring_issued_ok_via_asgi() -> None:
    cid = new_correlation_id()
    repo = _IssuanceRepoFake(_row_issued())
    issuance = Adm01PostgresIssuanceReadAdapter(repo)
    app = build_adm01_internal_lookup_http_app(
        identity=_IdentityEcho(),
        subscription=_Subscription("active"),
        entitlement=_Entitlement(EntitlementSummaryCategory.ACTIVE),
        issuance=issuance,
        policy=_Policy(AdminPolicyFlag.DEFAULT),
        redaction=None,
        adm01_allowlisted_internal_admin_principal_ids=["wiring-ok"],
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "wiring-ok",
                "internal_user_id": "u-777",
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["outcome"] == "success"
        s = body["summary"]
        assert s["access_readiness_bucket"] == "active_access_ready"
        assert s["telegram_identity_known"] is True
        assert repo.get_current_calls == 1
        out = r.text
        _assert_json_has_no_secrets(out)
        assert s["subscription_bucket"] == "active"
        assert s["recommended_next_action"] == "ask_user_to_use_get_access"

    _run(main())


def test_wiring_revoked_maps_to_none() -> None:
    cid = new_correlation_id()
    repo = _IssuanceRepoFake(_row_revoked())
    app = build_adm01_internal_lookup_http_app(
        identity=_IdentityEcho(),
        subscription=_Subscription("active"),
        entitlement=_Entitlement(EntitlementSummaryCategory.NONE),
        issuance=Adm01PostgresIssuanceReadAdapter(repo),
        policy=_Policy(AdminPolicyFlag.DEFAULT),
        adm01_allowlisted_internal_admin_principal_ids=["a"],
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "a",
                "internal_user_id": "u-777",
            },
        )
        assert r.status_code == 200
        assert r.json()["summary"]["access_readiness_bucket"] == "active_access_not_ready"
        _assert_json_has_no_secrets(r.text)
        assert repo.get_current_calls == 1

    _run(main())


def test_deny_does_not_touch_issuance_repo() -> None:
    cid = new_correlation_id()
    repo = _IssuanceRepoFake(_row_issued())
    app = build_adm01_internal_lookup_http_app(
        identity=_IdentityEcho(),
        subscription=_Subscription("a"),
        entitlement=_Entitlement(EntitlementSummaryCategory.ACTIVE),
        issuance=Adm01PostgresIssuanceReadAdapter(repo),
        policy=_Policy(AdminPolicyFlag.DEFAULT),
        adm01_allowlisted_internal_admin_principal_ids=["only-this"],
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "intruder",
                "internal_user_id": "u-777",
            },
        )
        assert r.status_code == 200
        assert r.json()["outcome"] == "denied"
        assert r.json()["summary"] is None
        assert repo.get_current_calls == 0
        _assert_json_has_no_secrets(r.text)

    _run(main())


def test_bundle_delegates_to_wiring() -> None:
    cid = new_correlation_id()
    repo = _IssuanceRepoFake(_row_issued())
    app = build_adm01_internal_lookup_starlette_app(
        Adm01InternalLookupDependencies(
            identity=_IdentityEcho(),
            subscription=_Subscription("active"),
            entitlement=_Entitlement(EntitlementSummaryCategory.ACTIVE),
            issuance=Adm01PostgresIssuanceReadAdapter(repo),
            policy=_Policy(AdminPolicyFlag.DEFAULT),
            redaction=None,
            adm01_allowlisted_internal_admin_principal_ids=["b1"],
        ),
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "b1",
                "internal_user_id": "u-777",
            },
        )
        assert r.json()["summary"]["access_readiness_bucket"] == "active_access_ready"
        _assert_json_has_no_secrets(r.text)

    _run(main())


def test_bundle_with_postgres_repo_uses_wiring_helper() -> None:
    cid = new_correlation_id()
    repo = MagicMock(spec=PostgresIssuanceStateRepository)
    repo.get_current_for_user = AsyncMock(return_value=_row_issued())
    app = build_adm01_internal_lookup_starlette_app_with_postgres_issuance_state(
        Adm01InternalLookupWithPostgresIssuanceStateDependencies(
            identity=_IdentityEcho(),
            subscription=_Subscription("active"),
            entitlement=_Entitlement(EntitlementSummaryCategory.ACTIVE),
            postgres_issuance_state=cast("PostgresIssuanceStateRepository", repo),
            policy=_Policy(AdminPolicyFlag.DEFAULT),
            redaction=None,
            adm01_allowlisted_internal_admin_principal_ids=["pg1"],
        ),
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "pg1",
                "internal_user_id": "u-777",
            },
        )
        j = r.json()
        assert j["summary"]["access_readiness_bucket"] == "active_access_ready"
        _assert_json_has_no_secrets(r.text)
        assert repo.get_current_for_user.await_count == 1

    _run(main())


def test_outbound_json_keys_limited() -> None:
    """Entire body must not gain secret-bearing keys; outcome path unchanged."""
    cid = new_correlation_id()
    app = build_adm01_internal_lookup_http_app(
        identity=_IdentityEcho(),
        subscription=_Subscription("a"),
        entitlement=_Entitlement(EntitlementSummaryCategory.ACTIVE),
        issuance=Adm01PostgresIssuanceReadAdapter(_IssuanceRepoFake(_row_issued())),
        policy=_Policy(AdminPolicyFlag.DEFAULT),
        adm01_allowlisted_internal_admin_principal_ids=["k"],
    )

    async def main() -> None:
        r = await _post_json(
            app,
            {
                "correlation_id": cid,
                "internal_admin_principal_id": "k",
                "internal_user_id": "u-777",
            },
        )
        d = r.json()
        top = set(d.keys())
        assert top <= {"outcome", "correlation_id", "summary"}
        s = d["summary"]
        assert s is not None
        assert set(s.keys()) <= {
            "telegram_identity_known",
            "subscription_bucket",
            "access_readiness_bucket",
            "recommended_next_action",
            "redaction",
        }
        _assert_json_has_no_secrets(json.dumps(d))

    _run(main())
