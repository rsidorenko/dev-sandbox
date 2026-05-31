"""Tests for PostgreSQL MVP access fulfillment e2e smoke output and guards."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_postgres_mvp_access_fulfillment_e2e.py"
_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "BEGIN ",
    "token=",
    "vpn://",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
    "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS",
    "BILLING_NORMALIZED_INGEST_ENABLE",
    "BILLING_SUBSCRIPTION_APPLY_ENABLE",
    "ISSUANCE_OPERATOR_ENABLE",
    "TELEGRAM_ACCESS_RESEND_ENABLE",
    "ADM02_ENSURE_ACCESS_ENABLE",
    "external_event_id",
    "internal_fact_ref",
    "billing_provider_key",
    "checkout_attempt_id",
    "customer_ref",
    "provider_ref",
    "internal_user_id",
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "check_postgres_mvp_access_fulfillment_e2e",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_success_main_outputs_exact_ok_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    async def ok_run() -> None:
        return None

    monkeypatch.setattr(script, "run_postgres_mvp_access_fulfillment_e2e", ok_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "postgres_mvp_access_fulfillment_e2e: ok"
    assert out.err == ""


def test_runtime_error_maps_to_fail_fixed_line_without_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    async def fail_run() -> None:
        raise RuntimeError("postgresql://u:secret@localhost/dev")

    monkeypatch.setattr(script, "run_postgres_mvp_access_fulfillment_e2e", fail_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "postgres_mvp_access_fulfillment_e2e: fail"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


def test_unexpected_exception_maps_to_failed_without_traceback_or_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    async def boom_run() -> None:
        raise ValueError("DATABASE_URL=postgresql://bad")

    monkeypatch.setattr(script, "run_postgres_mvp_access_fulfillment_e2e", boom_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "postgres_mvp_access_fulfillment_e2e: failed"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


@pytest.mark.parametrize(
    "missing_env",
    [
        "SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS",
        "BILLING_NORMALIZED_INGEST_ENABLE",
        "BILLING_SUBSCRIPTION_APPLY_ENABLE",
        "ISSUANCE_OPERATOR_ENABLE",
        "TELEGRAM_ACCESS_RESEND_ENABLE",
        "ADM02_ENSURE_ACCESS_ENABLE",
    ],
)
@pytest.mark.asyncio
async def test_missing_opt_in_fails_before_database_url_and_pool_open(
    monkeypatch: pytest.MonkeyPatch,
    missing_env: str,
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.setenv("ISSUANCE_OPERATOR_ENABLE", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_RESEND_ENABLE", "1")
    monkeypatch.setenv("ADM02_ENSURE_ACCESS_ENABLE", "1")
    monkeypatch.delenv(missing_env, raising=False)

    called = {"pool": False}

    async def fail_if_pool_opened(*args, **kwargs):
        _ = (args, kwargs)
        called["pool"] = True
        raise AssertionError("pool must not open")

    monkeypatch.setattr(script.asyncpg, "create_pool", fail_if_pool_opened)
    monkeypatch.setattr(
        script,
        "_required_database_url",
        lambda: (_ for _ in ()).throw(AssertionError("DATABASE_URL must not be read")),
    )

    with pytest.raises(RuntimeError):
        await script.run_postgres_mvp_access_fulfillment_e2e()
    assert called["pool"] is False


@pytest.mark.parametrize(
    (
        "mutating_opt_in",
        "billing_ingest_opt_in",
        "billing_apply_opt_in",
        "issuance_opt_in",
        "resend_opt_in",
        "adm02_opt_in",
    ),
    [
        ("0", "1", "1", "1", "1", "1"),
        ("1", "0", "1", "1", "1", "1"),
        ("1", "1", "0", "1", "1", "1"),
        ("1", "1", "1", "0", "1", "1"),
        ("1", "1", "1", "1", "0", "1"),
        ("1", "1", "1", "1", "1", "0"),
        ("false", "1", "1", "1", "1", "1"),
        ("1", "false", "1", "1", "1", "1"),
        ("1", "1", "false", "1", "1", "1"),
        ("1", "1", "1", "false", "1", "1"),
        ("1", "1", "1", "1", "false", "1"),
        ("1", "1", "1", "1", "1", "false"),
        ("no", "1", "1", "1", "1", "1"),
        ("1", "no", "1", "1", "1", "1"),
        ("1", "1", "no", "1", "1", "1"),
        ("1", "1", "1", "no", "1", "1"),
        ("1", "1", "1", "1", "no", "1"),
        ("1", "1", "1", "1", "1", "no"),
        ("random", "1", "1", "1", "1", "1"),
        ("1", "random", "1", "1", "1", "1"),
        ("1", "1", "random", "1", "1", "1"),
        ("1", "1", "1", "random", "1", "1"),
        ("1", "1", "1", "1", "random", "1"),
        ("1", "1", "1", "1", "1", "random"),
    ],
)
def test_falsey_opt_in_main_fails_with_safe_fixed_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutating_opt_in: str,
    billing_ingest_opt_in: str,
    billing_apply_opt_in: str,
    issuance_opt_in: str,
    resend_opt_in: str,
    adm02_opt_in: str,
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", mutating_opt_in)
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", billing_ingest_opt_in)
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", billing_apply_opt_in)
    monkeypatch.setenv("ISSUANCE_OPERATOR_ENABLE", issuance_opt_in)
    monkeypatch.setenv("TELEGRAM_ACCESS_RESEND_ENABLE", resend_opt_in)
    monkeypatch.setenv("ADM02_ENSURE_ACCESS_ENABLE", adm02_opt_in)

    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "postgres_mvp_access_fulfillment_e2e: fail"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


@pytest.mark.asyncio
async def test_missing_database_url_after_all_opt_ins_still_fails_before_pool_open(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.setenv("ISSUANCE_OPERATOR_ENABLE", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_RESEND_ENABLE", "1")
    monkeypatch.setenv("ADM02_ENSURE_ACCESS_ENABLE", "1")

    called = {"pool": False}

    async def fail_if_pool_opened(*args, **kwargs):
        _ = (args, kwargs)
        called["pool"] = True
        raise AssertionError("pool must not open")

    monkeypatch.setattr(script.asyncpg, "create_pool", fail_if_pool_opened)

    with pytest.raises(RuntimeError):
        await script.run_postgres_mvp_access_fulfillment_e2e()
    assert called["pool"] is False


@pytest.mark.asyncio
async def test_access_smoke_uses_billing_path_and_adm02_remediation_not_direct_issue_phase(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    ids = script._new_synthetic_ids()
    calls: list[str] = []

    class _ConnCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _ConnCtx()

        async def close(self):
            calls.append("pool.close")

    async def fake_create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _Pool()

    async def fake_apply_migrations(*args, **kwargs):
        _ = (args, kwargs)

    async def fake_cleanup(*args, **kwargs):
        _ = (args, kwargs)
        calls.append("cleanup")

    async def fake_create_if_absent(_self, _tg_user_id: int):
        class _Identity:
            internal_user_id = ids.internal_user_id

        return _Identity()

    async def fake_billing_run(_ids, _dsn):
        calls.append("billing")

    async def fake_assert_active(_pool, _ids):
        calls.append("assert_active")

    async def fake_assert_adm02_once(*, handler, ids, expected_remediation_results):
        assert handler is not None
        assert ids is not None
        calls.append("adm02_once")
        assert script.Adm02EnsureAccessRemediationResult.ISSUED_ACCESS in expected_remediation_results

    async def fake_assert_adm02_repeat(*, handler, pool, ids):
        assert handler is not None
        assert pool is not None
        assert ids is not None
        calls.append("adm02_repeat")

    async def fake_assert_adm02_durable_audit_readback(*, handler, ids):
        assert handler is not None
        assert ids is not None
        calls.append("adm02_durable_readback")

    async def fake_resolve(_pool):
        calls.append("resolve")

        class _FakeVlessProvider:
            async def create_user(self, *, internal_user_id: str):
                calls.append("create_user")

        class _Composition:
            vless_provider = _FakeVlessProvider()

        return _Composition()

    def fake_build_adm01_handler(_pool):
        calls.append("adm01_handler_built")
        return object()

    def fake_build_adm02_handler(_pool, *, audit_sink=None):
        calls.append("adm02_handler_built")
        assert audit_sink is not None
        assert isinstance(audit_sink, script.PostgresAdm02EnsureAccessAuditSink)
        assert audit_sink._source_marker == script._ADM02_AUDIT_SOURCE_MARKER
        return object()

    def fake_build_adm02_audit_lookup_handler(_pool):
        calls.append("adm02_audit_lookup_handler_built")
        return object()

    async def fake_assert_adm01(*, handler, ids, expected_access_readiness_bucket, expected_next_action):
        assert handler is not None
        assert ids is not None
        calls.append("adm01:" + expected_access_readiness_bucket.value + ":" + expected_next_action.value)

    dispatched_commands: list[str] = []

    async def fake_dispatch(envelope, _composition):
        dispatched_commands.append(envelope.normalized_command_text)
        calls.append(f"dispatch:{envelope.normalized_command_text}")

        class _Resp:
            category = script.TransportResponseCategory.ERROR
            code = "invalid_input"
            correlation_id = "c"
            next_action_hint = None

        return _Resp()

    async def forbid_upsert(*args, **kwargs):
        _ = (args, kwargs)
        raise AssertionError("direct snapshot upsert must not be used")

    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("SLICE1_POSTGRES_MVP_SMOKE_ALLOW_MUTATING_TESTS", "1")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.setenv("ISSUANCE_OPERATOR_ENABLE", "1")
    monkeypatch.setenv("TELEGRAM_ACCESS_RESEND_ENABLE", "1")
    monkeypatch.setenv("ADM02_ENSURE_ACCESS_ENABLE", "1")
    monkeypatch.setattr(script.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(script, "apply_postgres_migrations", fake_apply_migrations)
    monkeypatch.setattr(script, "_cleanup_synthetic_rows", fake_cleanup)
    monkeypatch.setattr(script, "_new_synthetic_ids", lambda: ids)
    monkeypatch.setattr(script.PostgresUserIdentityRepository, "create_if_absent", fake_create_if_absent)
    monkeypatch.setattr(script, "_run_billing_ingest_apply_for_active_subscription", fake_billing_run)
    monkeypatch.setattr(script, "_assert_active_subscription", fake_assert_active)
    monkeypatch.setattr(script, "_build_adm02_ensure_access_handler_with_existing_pool", fake_build_adm02_handler)
    monkeypatch.setattr(
        script,
        "_build_adm02_ensure_access_audit_lookup_handler_with_existing_pool",
        fake_build_adm02_audit_lookup_handler,
    )
    monkeypatch.setattr(script, "_assert_adm02_safe_successful_remediation", fake_assert_adm02_once)
    monkeypatch.setattr(
        script, "_assert_adm02_idempotent_repeat_with_stable_current_issued_state", fake_assert_adm02_repeat
    )
    monkeypatch.setattr(script, "_assert_adm02_durable_audit_readback", fake_assert_adm02_durable_audit_readback)
    monkeypatch.setattr(script, "_resolve_postgres_composition_with_existing_pool", fake_resolve)
    monkeypatch.setattr(script, "_build_adm01_lookup_handler_with_existing_pool", fake_build_adm01_handler)
    monkeypatch.setattr(script, "_assert_adm01_support_readiness", fake_assert_adm01)
    monkeypatch.setattr(script, "dispatch_slice1_transport", fake_dispatch)
    monkeypatch.setattr(script.PostgresSubscriptionSnapshotReader, "upsert_state", forbid_upsert)

    await script.run_postgres_mvp_access_fulfillment_e2e()

    assert "billing" in calls
    assert "assert_active" in calls
    assert "adm01_handler_built" in calls
    assert "adm02_handler_built" in calls
    assert "adm02_audit_lookup_handler_built" in calls
    assert dispatched_commands == ["/status", "/status", "/get_access"]
    first_status_idx = calls.index("dispatch:/status")
    second_status_idx = calls.index("dispatch:/status", first_status_idx + 1)
    adm01_not_ready_idx = calls.index("adm01:active_access_not_ready:investigate_issuance")
    adm02_once_idx = calls.index("adm02_once")
    adm02_repeat_idx = calls.index("adm02_repeat")
    adm02_audit_idx = calls.index("adm02_durable_readback")
    adm01_ready_idx = calls.index("adm01:active_access_ready:ask_user_to_use_get_access")
    get_access_idx = calls.index("dispatch:/get_access")
    assert first_status_idx < adm01_not_ready_idx < adm02_once_idx < adm02_repeat_idx
    assert adm02_repeat_idx < adm02_audit_idx < second_status_idx < adm01_ready_idx
    assert adm01_ready_idx < get_access_idx
    assert calls.count("cleanup") == 2
    assert calls[-1] == "pool.close"


@pytest.mark.asyncio
async def test_transport_rejection_assertion_works_for_removed_commands() -> None:
    script = _load_script_module()

    # ERROR response should pass _assert_transport_rejected
    script._assert_transport_rejected(
        type(
            "_Response",
            (),
            {
                "category": script.TransportResponseCategory.ERROR,
                "code": "invalid_input",
                "correlation_id": "cid-1",
                "next_action_hint": None,
            },
        )(),
    )

    # SUCCESS response should fail _assert_transport_rejected
    with pytest.raises(RuntimeError, match="expected transport rejection"):
        script._assert_transport_rejected(
            type(
                "_Response",
                (),
                {
                    "category": script.TransportResponseCategory.SUCCESS,
                    "code": "some_code",
                    "correlation_id": "cid-2",
                    "next_action_hint": None,
                },
            )(),
        )


@pytest.mark.asyncio
async def test_adm01_support_surface_blob_stays_leak_free(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    leaked: dict[str, str] = {}

    def fake_assert_no_forbidden(text: str) -> None:
        lowered = text.lower()
        for forbidden in _FORBIDDEN:
            assert forbidden.lower() not in lowered
        leaked["ok"] = text

    class _Summary:
        telegram_identity_known = True
        subscription_bucket = "active"
        access_readiness_bucket = "active_access_not_ready"
        recommended_next_action = "investigate_issuance"
        redaction = "none"

    class _Response:
        outcome = "success"
        correlation_id = "cid-adm01"
        summary = _Summary()

    async def fake_execute_adm01_endpoint(*args, **kwargs):
        _ = (args, kwargs)
        return _Response()

    monkeypatch.setattr(script, "_assert_no_forbidden_output", fake_assert_no_forbidden)
    monkeypatch.setattr(script, "execute_adm01_endpoint", fake_execute_adm01_endpoint)

    await script._assert_adm01_support_readiness(
        handler=object(),
        ids=script._new_synthetic_ids(),
        expected_access_readiness_bucket=script.Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_NOT_READY,
        expected_next_action=script.Adm01SupportNextAction.INVESTIGATE_ISSUANCE,
    )

    assert leaked["ok"] == ("adm01|True|active|active_access_not_ready|investigate_issuance|none|cid-adm01|success")


def test_adm02_audit_evidence_requires_issued_then_already_ready_and_stays_leak_free() -> None:
    script = _load_script_module()
    cid = "a" * 32
    principal_marker = script.Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED
    event_type = script.Adm02EnsureAccessAuditEventType.ENSURE_ACCESS
    issued = script.Adm02EnsureAccessAuditEvent(
        event_type=event_type,
        outcome_bucket=script.Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        remediation_result=script.Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        readiness_bucket=script.Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=principal_marker,
        correlation_id=cid,
    )
    already_ready = script.Adm02EnsureAccessAuditEvent(
        event_type=event_type,
        outcome_bucket=script.Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY,
        remediation_result=script.Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
        readiness_bucket=script.Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=principal_marker,
        correlation_id=cid,
    )
    script._assert_adm02_audit_evidence((issued, already_ready))


def test_adm02_audit_evidence_rejects_wrong_outcome_order() -> None:
    script = _load_script_module()
    cid = "b" * 32
    principal_marker = script.Adm02EnsureAccessAuditPrincipalMarker.INTERNAL_ADMIN_REDACTED
    event_type = script.Adm02EnsureAccessAuditEventType.ENSURE_ACCESS
    wrong_first = script.Adm02EnsureAccessAuditEvent(
        event_type=event_type,
        outcome_bucket=script.Adm02EnsureAccessAuditOutcomeBucket.NOOP_ACCESS_ALREADY_READY,
        remediation_result=script.Adm02EnsureAccessRemediationResult.NOOP_ACCESS_ALREADY_READY,
        readiness_bucket=script.Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=principal_marker,
        correlation_id=cid,
    )
    wrong_second = script.Adm02EnsureAccessAuditEvent(
        event_type=event_type,
        outcome_bucket=script.Adm02EnsureAccessAuditOutcomeBucket.ISSUED_ACCESS,
        remediation_result=script.Adm02EnsureAccessRemediationResult.ISSUED_ACCESS,
        readiness_bucket=script.Adm01SupportAccessReadinessBucket.ACTIVE_ACCESS_READY,
        principal_marker=principal_marker,
        correlation_id=cid,
    )
    with pytest.raises(RuntimeError, match="first audit outcome mismatch"):
        script._assert_adm02_audit_evidence((wrong_first, wrong_second))


@pytest.mark.asyncio
async def test_adm02_durable_audit_readback_requires_issued_and_already_ready_and_safe_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    ids = script._new_synthetic_ids()
    leaked: dict[str, str] = {}

    def fake_assert_no_forbidden(text: str) -> None:
        lowered = text.lower()
        for forbidden in _FORBIDDEN:
            assert forbidden.lower() not in lowered
        leaked["ok"] = text

    class _Issued:
        created_at = "2026-04-26T00:00:00+00:00"
        event_type = "ensure_access"
        outcome_bucket = "issued_access"
        remediation_result = "issued_access"
        readiness_bucket = "active_access_ready"
        principal_marker = "internal_admin_redacted"
        correlation_id = ids.correlation_id
        source_marker = script._ADM02_AUDIT_SOURCE_MARKER

    class _AlreadyReady:
        created_at = "2026-04-26T00:00:01+00:00"
        event_type = "ensure_access"
        outcome_bucket = "noop_access_already_ready"
        remediation_result = "noop_access_already_ready"
        readiness_bucket = "active_access_ready"
        principal_marker = "internal_admin_redacted"
        correlation_id = ids.correlation_id
        source_marker = script._ADM02_AUDIT_SOURCE_MARKER

    class _Response:
        outcome = "success"
        items = (_AlreadyReady(), _Issued())

    async def fake_execute(*args, **kwargs):
        _ = (args, kwargs)
        return _Response()

    monkeypatch.setattr(script, "_assert_no_forbidden_output", fake_assert_no_forbidden)
    monkeypatch.setattr(script, "execute_adm02_ensure_access_audit_lookup_endpoint", fake_execute)

    await script._assert_adm02_durable_audit_readback(handler=object(), ids=ids)
    assert "noop_access_already_ready" in leaked["ok"] or "issued_access" in leaked["ok"]


@pytest.mark.asyncio
async def test_cleanup_synthetic_rows_deletes_adm02_audit_only_by_smoke_markers() -> None:
    script = _load_script_module()
    ids = script._new_synthetic_ids()

    class _Conn:
        def __init__(self) -> None:
            self.calls: list[tuple[str, tuple[object, ...]]] = []

        async def execute(self, query: str, *params: object):
            self.calls.append((query, params))
            return "DELETE 0"

    conn = _Conn()
    await script._cleanup_synthetic_rows(conn, ids)
    first_query, first_params = conn.calls[0]
    assert "DELETE FROM adm02_ensure_access_audit_events" in first_query
    assert "correlation_id = $1::text" in first_query
    assert "source_marker = $2::text" in first_query
    assert first_params == (ids.correlation_id, script._ADM02_AUDIT_SOURCE_MARKER)
