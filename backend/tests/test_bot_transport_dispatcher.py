"""Pure in-memory tests for slice-1 transport dispatcher (no Telegram SDK, no runtime)."""

from __future__ import annotations

import inspect
from datetime import UTC, datetime, timedelta

import pytest

from app.application.bootstrap import build_slice1_composition
from app.application.interfaces import SubscriptionSnapshot
from app.application.telegram_command_rate_limit import InMemoryTelegramCommandRateLimiter
from app.application.telegram_command_rate_limit_telemetry import (
    NoopTelegramCommandRateLimitTelemetry,
    TelegramCommandRateLimitDecisionEvent,
)
from app.bot_transport.dispatcher import Slice1Dispatcher, dispatch_slice1_transport
from app.bot_transport.message_catalog import render_telegram_outbound_plan
from app.bot_transport.normalized import TransportIncomingEnvelope
from app.bot_transport.outbound import (
    build_subscription_active_recovery_confirmation_plan,
    map_transport_safe_to_outbound_plan,
)
from app.bot_transport.presentation import (
    TransportAccessResendCode,
    TransportBootstrapCode,
    TransportErrorCode,
    TransportHelpCode,
    TransportNextActionHint,
    TransportResponseCategory,
    TransportSafeResponse,
    TransportStatusCode,
    TransportStorefrontCode,
    TransportSupportCode,
)
from app.persistence.in_memory import (
    InMemoryAuditAppender,
    InMemoryIdempotencyRepository,
    InMemorySubscriptionSnapshotReader,
    InMemoryUserIdentityRepository,
)
from app.shared.correlation import new_correlation_id
from app.shared.test_helpers import run_async as _run


def _uc02_status_outbound_texts(r: TransportSafeResponse) -> list[str]:
    """Primary status plan plus optional recovery confirmation (matches runtime facade layering)."""
    texts = [
        render_telegram_outbound_plan(map_transport_safe_to_outbound_plan(r)).message_text,
    ]
    if r.subscription_active_recovery_followup:
        texts.append(
            render_telegram_outbound_plan(
                build_subscription_active_recovery_confirmation_plan(r),
            ).message_text,
        )
    return texts


def _env(
    *,
    cid: str,
    uid: int = 100,
    update_id: int | None = 1,
    text: str = "/start",
) -> TransportIncomingEnvelope:
    return TransportIncomingEnvelope(
        telegram_user_id=uid,
        correlation_id=cid,
        telegram_update_id=update_id,
        normalized_command_text=text,
    )


def _uc02_transport_public_surface(r: TransportSafeResponse) -> str:
    """Concatenate only transport-facing fields (for leak assertions)."""
    hint = r.next_action_hint or ""
    return f"{r.category.value!s}{r.code!s}{r.correlation_id!s}{hint}"


def _assert_uc02_transport_has_no_sensitive_leaks(
    r: TransportSafeResponse,
    *,
    forbidden_substrings: tuple[str, ...],
) -> None:
    blob = _uc02_transport_public_surface(r).lower()
    for s in forbidden_substrings:
        assert s.lower() not in blob, f"unexpected substring in transport surface: {s!r}"
    assert "postgresql://" not in blob
    assert "postgres://" not in blob


def test_dispatch_start_bootstrap_success() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, text="/start"), c)
        assert r.category is TransportResponseCategory.SUCCESS
        assert r.code == TransportBootstrapCode.IDENTITY_READY.value
        assert r.correlation_id == cid

    _run(main())


def test_dispatch_duplicate_start_idempotent_same_success_no_extra_audit() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        e = _env(cid=cid, uid=42, update_id=5, text="/start")
        r1 = await dispatch_slice1_transport(e, c)
        r2 = await dispatch_slice1_transport(e, c)
        assert r1.category is r2.category is TransportResponseCategory.SUCCESS
        assert r1.code == r2.code == TransportBootstrapCode.IDENTITY_READY.value
        events = await c.audit.recorded_events()
        assert len(events) == 1

    _run(main())


def test_dispatch_status_bootstrapped_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        uid = 77
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=2, text="/start"), c)
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert r.correlation_id == cid

    _run(main())


def test_dispatch_status_unknown_user_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, uid=999, text="/status"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_status_needs_review_rejected_as_unknown() -> None:
    async def main() -> None:
        snaps = InMemorySubscriptionSnapshotReader()
        c = build_slice1_composition(
            identity=InMemoryUserIdentityRepository(),
            idempotency=InMemoryIdempotencyRepository(),
            snapshots=snaps,
            audit=InMemoryAuditAppender(),
        )
        cid = new_correlation_id()
        uid = 42
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=3, text="/start"), c)
        await snaps.upsert_for_tests(
            f"u{uid}",
            SubscriptionSnapshot(internal_user_id=f"u{uid}", state_label="needs_review"),
        )
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_status_known_user_missing_snapshot_rejected_as_unknown() -> None:
    async def main() -> None:
        snaps = InMemorySubscriptionSnapshotReader()
        c = build_slice1_composition(
            identity=InMemoryUserIdentityRepository(),
            idempotency=InMemoryIdempotencyRepository(),
            snapshots=snaps,
            audit=InMemoryAuditAppender(),
        )
        cid = new_correlation_id()
        uid = 55
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=4, text="/start"), c)
        await snaps.upsert_for_tests(f"u{uid}", None)
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_help_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, text="/help"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert r.correlation_id == cid

    _run(main())


def test_dispatch_support_rejected_as_unknown(monkeypatch) -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r_menu = await dispatch_slice1_transport(_env(cid=cid, text="/support"), c)
        r_contact = await dispatch_slice1_transport(_env(cid=cid, text="/support_contact"), c)
        assert r_menu.category is TransportResponseCategory.ERROR
        assert r_menu.code == TransportErrorCode.INVALID_INPUT.value
        assert r_contact.category is TransportResponseCategory.ERROR
        assert r_contact.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_storefront_commands_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        for cmd in ("/plans", "/buy", "/checkout", "/success", "/renew", "/support", "/support_contact"):
            r = await dispatch_slice1_transport(_env(cid=cid, text=cmd), c)
            assert r.category is TransportResponseCategory.ERROR, f"{cmd} should be rejected"
            assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_success_rejected_as_unknown() -> None:
    async def main() -> None:
        snaps = InMemorySubscriptionSnapshotReader()
        c = build_slice1_composition(
            identity=InMemoryUserIdentityRepository(),
            idempotency=InMemoryIdempotencyRepository(),
            snapshots=snaps,
            audit=InMemoryAuditAppender(),
        )
        cid = new_correlation_id()
        uid = 707
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/success"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_my_subscription_rejected_as_unknown() -> None:
    async def main() -> None:
        snaps = InMemorySubscriptionSnapshotReader()
        c = build_slice1_composition(
            identity=InMemoryUserIdentityRepository(),
            idempotency=InMemoryIdempotencyRepository(),
            snapshots=snaps,
            audit=InMemoryAuditAppender(),
        )
        cid = new_correlation_id()
        uid = 808
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        await snaps.upsert_for_tests(
            f"u{uid}",
            SubscriptionSnapshot(
                internal_user_id=f"u{uid}",
                state_label="active",
                active_until_utc=datetime.now(UTC) - timedelta(days=1),
            ),
        )
        expired = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/my_subscription"), c)
        assert expired.category is TransportResponseCategory.ERROR
        assert expired.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_active_subscription_status_commands_rejected_as_unknown() -> None:
    async def main(cmd: str) -> None:
        snaps = InMemorySubscriptionSnapshotReader()
        c = build_slice1_composition(
            identity=InMemoryUserIdentityRepository(),
            idempotency=InMemoryIdempotencyRepository(),
            snapshots=snaps,
            audit=InMemoryAuditAppender(),
        )
        cid = new_correlation_id()
        uid = 909
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        await snaps.upsert_for_tests(
            f"u{uid}",
            SubscriptionSnapshot(
                internal_user_id=f"u{uid}",
                state_label="active",
                active_until_utc=datetime.now(UTC) + timedelta(days=30),
            ),
        )
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=2, text=cmd), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    for command in ("/my_subscription", "/status"):
        _run(main(command))


def test_dispatch_inactive_subscription_status_rejected_as_unknown() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        uid = 910
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=2, text="/status"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_help_then_start_only_bootstrap_audit() -> None:
    """Help is rejected; first audit event appears only on /start."""

    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        h = await dispatch_slice1_transport(_env(cid=cid, uid=11, text="/help"), c)
        assert h.category is TransportResponseCategory.ERROR
        assert h.code == TransportErrorCode.INVALID_INPUT.value
        s = await dispatch_slice1_transport(
            _env(cid=cid, uid=11, text="/start", update_id=1),
            c,
        )
        assert s.code == TransportBootstrapCode.IDENTITY_READY.value
        assert len(await c.audit.recorded_events()) == 1

    _run(main())


def test_dispatch_resend_access_rejected_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ACCESS_RESEND_ENABLE", raising=False)

    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, uid=22, update_id=9, text="/resend_access"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_get_access_alias_rejected_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ACCESS_RESEND_ENABLE", raising=False)

    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, uid=22, update_id=10, text="/get_access"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_unknown_command_no_handler_invocation() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, text="/nope"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert len(await c.audit.recorded_events()) == 0

    _run(main())


def test_dispatch_invalid_telegram_user_id_rejected() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(_env(cid=cid, uid=0, text="/start", update_id=1), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_missing_bootstrap_update_id_rejected() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        r = await dispatch_slice1_transport(
            TransportIncomingEnvelope(
                telegram_user_id=10,
                correlation_id=cid,
                telegram_update_id=None,
                normalized_command_text="/start",
            ),
            c,
        )
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value
        assert len(await c.audit.recorded_events()) == 0

    _run(main())


def test_correlation_id_preserved_on_success_and_reject() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        ok = await dispatch_slice1_transport(_env(cid=cid, text="/start"), c)
        assert ok.correlation_id == cid
        bad = await dispatch_slice1_transport(_env(cid=cid, text="/unknown"), c)
        assert bad.correlation_id == cid

    _run(main())


def test_slice1_dispatcher_class_delegates() -> None:
    async def main() -> None:
        c = build_slice1_composition()
        cid = new_correlation_id()
        d = Slice1Dispatcher(c)
        r = await d.dispatch(_env(cid=cid, text="/start", update_id=1))
        assert r.correlation_id == cid

    _run(main())


def test_dispatcher_module_excludes_billing_issuance_admin_concepts() -> None:
    import app.bot_transport.dispatcher as d

    src = inspect.getsource(d)
    lower = src.lower()
    assert "billing" not in lower
    assert "issuance" not in lower
    assert "admin" not in lower


def test_dispatch_status_rejected_consistently() -> None:
    async def main() -> None:
        limiter = InMemoryTelegramCommandRateLimiter(
            status_limit=2,
            status_window_seconds=60.0,
            access_resend_limit=99,
            access_resend_window_seconds=60.0,
            now_seconds=lambda: 0.0,
        )
        c = build_slice1_composition(
            command_rate_limiter=limiter,
            command_rate_limit_telemetry=NoopTelegramCommandRateLimitTelemetry(),
        )
        cid = new_correlation_id()
        uid = 501
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        for _ in range(3):
            r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
            assert r.category is TransportResponseCategory.ERROR
            assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_my_subscription_alias_rejected_like_status() -> None:
    async def main() -> None:
        limiter = InMemoryTelegramCommandRateLimiter(
            status_limit=1,
            status_window_seconds=60.0,
            access_resend_limit=99,
            access_resend_window_seconds=60.0,
            now_seconds=lambda: 0.0,
        )
        c = build_slice1_composition(
            command_rate_limiter=limiter,
            command_rate_limit_telemetry=NoopTelegramCommandRateLimitTelemetry(),
        )
        cid = new_correlation_id()
        uid = 506
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        s = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        ms = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/my_subscription"), c)
        assert s.category is TransportResponseCategory.ERROR
        assert s.code == TransportErrorCode.INVALID_INPUT.value
        assert ms.category is TransportResponseCategory.ERROR
        assert ms.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_get_access_and_resend_both_rejected_as_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TELEGRAM_ACCESS_RESEND_ENABLE", raising=False)

    async def main() -> None:
        limiter = InMemoryTelegramCommandRateLimiter(
            status_limit=99,
            status_window_seconds=60.0,
            access_resend_limit=1,
            access_resend_window_seconds=60.0,
            now_seconds=lambda: 0.0,
        )
        c = build_slice1_composition(
            command_rate_limiter=limiter,
            command_rate_limit_telemetry=NoopTelegramCommandRateLimitTelemetry(),
        )
        cid = new_correlation_id()
        uid = 502
        g = await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/get_access"), c)
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=2, text="/resend_access"), c)
        assert g.category is TransportResponseCategory.ERROR
        assert g.code == TransportErrorCode.INVALID_INPUT.value
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_telemetry_failure_does_not_block_status_rejection() -> None:
    class _BoomTelemetry(NoopTelegramCommandRateLimitTelemetry):
        async def emit_decision(self, event: TelegramCommandRateLimitDecisionEvent) -> None:
            _ = event
            raise RuntimeError("telemetry boom")

    async def main() -> None:
        c = build_slice1_composition(
            command_rate_limit_telemetry=_BoomTelemetry(),
        )
        cid = new_correlation_id()
        uid = 503
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        r = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        assert r.category is TransportResponseCategory.ERROR
        assert r.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_status_rejected_even_when_telemetry_fails() -> None:
    class _BoomTelemetry(NoopTelegramCommandRateLimitTelemetry):
        async def emit_decision(self, event: TelegramCommandRateLimitDecisionEvent) -> None:
            _ = event
            raise RuntimeError("telemetry boom")

    async def main() -> None:
        limiter = InMemoryTelegramCommandRateLimiter(
            status_limit=1,
            status_window_seconds=60.0,
            access_resend_limit=99,
            access_resend_window_seconds=60.0,
            now_seconds=lambda: 0.0,
        )
        c = build_slice1_composition(
            command_rate_limiter=limiter,
            command_rate_limit_telemetry=_BoomTelemetry(),
        )
        cid = new_correlation_id()
        uid = 504
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        r2 = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        assert r2.category is TransportResponseCategory.ERROR
        assert r2.code == TransportErrorCode.INVALID_INPUT.value

    _run(main())


def test_dispatch_status_no_rate_limit_events_since_command_rejected() -> None:
    class _Spy(NoopTelegramCommandRateLimitTelemetry):
        def __init__(self) -> None:
            self.events: list[TelegramCommandRateLimitDecisionEvent] = []

        async def emit_decision(self, event: TelegramCommandRateLimitDecisionEvent) -> None:
            self.events.append(event)

    async def main() -> None:
        spy = _Spy()
        limiter = InMemoryTelegramCommandRateLimiter(
            status_limit=1,
            status_window_seconds=60.0,
            access_resend_limit=99,
            access_resend_window_seconds=60.0,
            now_seconds=lambda: 0.0,
        )
        c = build_slice1_composition(command_rate_limiter=limiter, command_rate_limit_telemetry=spy)
        cid = new_correlation_id()
        uid = 505
        await dispatch_slice1_transport(_env(cid=cid, uid=uid, update_id=1, text="/start"), c)
        r1 = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        r2 = await dispatch_slice1_transport(_env(cid=cid, uid=uid, text="/status"), c)
        # Both /status calls are rejected before rate limiter is consulted
        assert r1.category is TransportResponseCategory.ERROR
        assert r2.category is TransportResponseCategory.ERROR
        assert len(spy.events) == 0

    _run(main())
