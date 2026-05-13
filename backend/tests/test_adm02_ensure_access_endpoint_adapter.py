"""ADM-02 ensure-access thin transport adapter tests (fakes only)."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict

from app.admin_support.adm02_ensure_access_endpoint import (
    Adm02EnsureAccessInboundRequest,
    execute_adm02_ensure_access_endpoint,
)
from app.admin_support.contracts import (
    Adm01SupportAccessReadinessBucket,
    Adm01SupportNextAction,
    Adm01SupportSubscriptionBucket,
    Adm02EnsureAccessInput,
    Adm02EnsureAccessOutcome,
    Adm02EnsureAccessRemediationResult,
    Adm02EnsureAccessResult,
    Adm02EnsureAccessSummary,
    AdminActorRef,
    InternalAdminPrincipalExtractionInput,
    InternalAdminPrincipalExtractionOutcome,
    InternalAdminPrincipalExtractionResult,
    TelegramUserTarget,
)
from app.shared.correlation import new_correlation_id


def _run(coro):
    return asyncio.run(coro)


class _Handler:
    def __init__(self, result: Adm02EnsureAccessResult) -> None:
        self._result = result
        self.last_inp: Adm02EnsureAccessInput | None = None

    async def handle(self, inp: Adm02EnsureAccessInput) -> Adm02EnsureAccessResult:
        self.last_inp = inp
        return self._result


class _Extractor:
    async def extract_trusted_internal_admin_principal(self, inp: InternalAdminPrincipalExtractionInput):
        return InternalAdminPrincipalExtractionResult(
            outcome=InternalAdminPrincipalExtractionOutcome.SUCCESS,
            principal=AdminActorRef(internal_admin_principal_id="adm-ok"),
        )


def _result(cid: str) -> Adm02EnsureAccessResult:
    return Adm02EnsureAccessResult(
        outcome=Adm02EnsureAccessOutcome.SUCCESS,
        correlation_id=cid,
        summary=Adm02EnsureAccessSummary(
            telegram_identity_known=True,
            subscription_bucket=Adm01SupportSubscriptionBucket.ACTIVE,
            access_readiness_bucket=Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
            remediation_result=Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
            recommended_next_action=Adm01SupportNextAction.ASK_USER_TO_USE_GET_ACCESS,
        ),
    )


def test_normalizes_target_and_returns_safe_summary() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _Handler(_result(cid))
        r = await execute_adm02_ensure_access_endpoint(
            h,
            _Extractor(),
            Adm02EnsureAccessInboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-x",
                telegram_user_id=42,
            ),
        )
        assert h.last_inp is not None
        assert h.last_inp.target == TelegramUserTarget(telegram_user_id=42)
        assert r.outcome == "success"
        assert r.summary is not None
        assert r.summary.telegram_identity_known is True
        assert r.summary.access_readiness_bucket == "active_access_ready"
        assert r.summary.remediation_result == "issued_access"

    _run(main())


def test_invalid_input_fail_closed() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _Handler(_result(cid))
        r = await execute_adm02_ensure_access_endpoint(
            h,
            _Extractor(),
            Adm02EnsureAccessInboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-x",
                internal_user_id="u-1",
                telegram_user_id=1,
            ),
        )
        assert r.outcome == "invalid_input"
        assert r.summary is None

    _run(main())


def test_non_success_has_no_summary_and_no_leaks() -> None:
    cid = new_correlation_id()

    async def main() -> None:
        h = _Handler(
            Adm02EnsureAccessResult(
                outcome=Adm02EnsureAccessOutcome.DENIED,
                correlation_id=cid,
                summary=_result(cid).summary,
            )
        )
        r = await execute_adm02_ensure_access_endpoint(
            h,
            _Extractor(),
            Adm02EnsureAccessInboundRequest(
                correlation_id=cid,
                internal_admin_principal_id="adm-x",
                internal_user_id="u-1",
            ),
        )
        assert r.outcome == "denied"
        assert r.summary is None
        blob = json.dumps(asdict(r), sort_keys=True).lower()
        for forbidden in (
            "database_url",
            "postgres://",
            "postgresql://",
            "bearer ",
            "private key",
            "provider_issuance_ref",
            "issue_idempotency_key",
            "schema_version",
            "customer_ref",
            "provider_ref",
            "checkout_attempt_id",
            "internal_user_id",
        ):
            assert forbidden not in blob

    _run(main())
