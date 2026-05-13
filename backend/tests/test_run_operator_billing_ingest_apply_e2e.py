"""Tests for operator ingest/apply e2e smoke script output, safety, and cleanup hooks."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from app.application.apply_billing_subscription import ApplyAcceptedBillingFactResult
from app.persistence.billing_subscription_apply_contracts import BillingSubscriptionApplyOutcome
from app.shared.types import OperationOutcomeCategory

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "check_operator_billing_ingest_apply_e2e.py"
_FORBIDDEN = (
    "DATABASE_URL",
    "postgres://",
    "postgresql://",
    "Bearer ",
    "PRIVATE KEY",
    "provider_issuance_ref",
    "issue_idempotency_key",
    "schema_version",
)


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "check_operator_billing_ingest_apply_e2e",
        _SCRIPT_PATH,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_missing_database_url_returns_fail_without_leak(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "operator_billing_ingest_apply_e2e: fail"
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


@pytest.mark.parametrize("missing_env", ["BILLING_NORMALIZED_INGEST_ENABLE", "BILLING_SUBSCRIPTION_APPLY_ENABLE"])
def test_missing_billing_opt_in_fails_closed_without_opening_db_pool(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    missing_env: str,
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.delenv(missing_env, raising=False)

    create_pool_called = {"called": False}

    async def fail_if_create_pool_called(*args, **kwargs):
        _ = (args, kwargs)
        create_pool_called["called"] = True
        raise AssertionError("asyncpg.create_pool must not be called")

    monkeypatch.setattr(script.asyncpg, "create_pool", fail_if_create_pool_called)

    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert create_pool_called["called"] is False
    assert out.out == ""
    assert out.err.strip() == "operator_billing_ingest_apply_e2e: fail"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


@pytest.mark.parametrize(
    ("ingest_opt_in", "apply_opt_in"),
    [
        ("0", "1"),
        ("1", "0"),
        ("false", "1"),
        ("1", "false"),
        ("no", "1"),
        ("1", "no"),
        ("random", "1"),
        ("1", "random"),
        ("", "1"),
        ("1", ""),
    ],
)
def test_falsey_billing_opt_ins_fail_closed_without_opening_db_pool(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    ingest_opt_in: str,
    apply_opt_in: str,
) -> None:
    script = _load_script_module()
    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", ingest_opt_in)
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", apply_opt_in)

    create_pool_called = {"called": False}

    async def fail_if_create_pool_called(*args, **kwargs):
        _ = (args, kwargs)
        create_pool_called["called"] = True
        raise AssertionError("asyncpg.create_pool must not be called")

    monkeypatch.setattr(script.asyncpg, "create_pool", fail_if_create_pool_called)

    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert create_pool_called["called"] is False
    assert out.out == ""
    assert out.err.strip() == "operator_billing_ingest_apply_e2e: fail"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.out
        assert frag not in out.err


def test_success_path_fixed_ok_line(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    script = _load_script_module()

    async def ok_run() -> None:
        return None

    monkeypatch.setattr(script, "run_operator_billing_ingest_apply_e2e", ok_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 0
    assert out.out.strip() == "operator_billing_ingest_apply_e2e: ok"
    assert out.err == ""


def test_runtime_error_returns_fail_no_forbidden_output(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    async def fail_run() -> None:
        raise RuntimeError("postgresql://u:secret@localhost/db")

    monkeypatch.setattr(script, "run_operator_billing_ingest_apply_e2e", fail_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "operator_billing_ingest_apply_e2e: fail"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.err


def test_migration_ledger_drift_returns_safe_stderr_line(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    async def drift_run() -> None:
        raise script.MigrationLedgerDriftError("ledger drift detail must not leak")

    monkeypatch.setattr(script, "run_operator_billing_ingest_apply_e2e", drift_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "operator_billing_ingest_apply_e2e: fail_ledger_drift"
    assert "Traceback" not in out.err
    assert "ledger drift" not in out.err.lower()
    for frag in _FORBIDDEN:
        assert frag not in out.err


def test_unexpected_exception_returns_failed_without_traceback(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    script = _load_script_module()

    async def boom_run() -> None:
        raise ValueError("DATABASE_URL=postgresql://bad")

    monkeypatch.setattr(script, "run_operator_billing_ingest_apply_e2e", boom_run)
    rc = script.main([])
    out = capsys.readouterr()
    assert rc == 1
    assert out.out == ""
    assert out.err.strip() == "operator_billing_ingest_apply_e2e: failed"
    assert "Traceback" not in out.err
    for frag in _FORBIDDEN:
        assert frag not in out.err


@pytest.mark.asyncio
async def test_cleanup_called_on_partial_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    script = _load_script_module()
    calls: list[str] = []
    ids = script._new_synthetic_ids()

    class _ConnCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _ConnCtx()

        async def close(self) -> None:
            calls.append("pool.close")

    async def fake_cleanup(_conn, ids_obj) -> None:
        assert ids_obj.uid.startswith("operator-e2e-")
        assert ids_obj.fact_ref.startswith("operator-e2e-")
        assert ids_obj.ext_event_id.startswith("operator-e2e-")
        calls.append("cleanup")

    async def fake_create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _Pool()

    async def fake_apply_migrations(*args, **kwargs) -> None:
        _ = (args, kwargs)
        return

    async def fail_ingest(*args, **kwargs):
        _ = (args, kwargs)
        raise RuntimeError("forced ingest failure")

    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.setattr(script.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(script, "apply_postgres_migrations", fake_apply_migrations)
    monkeypatch.setattr(script, "_cleanup_synthetic_rows", fake_cleanup)
    monkeypatch.setattr(script, "_new_synthetic_ids", lambda: ids)
    monkeypatch.setattr(script, "async_run_billing_ingest_from_parsed", fail_ingest)

    with pytest.raises(RuntimeError):
        await script.run_operator_billing_ingest_apply_e2e()
    assert calls.count("cleanup") == 2
    assert calls[-1] == "pool.close"


@pytest.mark.asyncio
async def test_cleanup_called_on_partial_failure_after_second_ingest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    calls: list[str] = []
    ids = script._new_synthetic_ids()
    ingest_n = {"n": 0}

    class _ConnCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _ConnCtx()

        async def close(self) -> None:
            calls.append("pool.close")

    async def fake_cleanup(_conn, ids_obj) -> None:
        assert ids_obj.uid.startswith("operator-e2e-")
        calls.append("cleanup")

    async def fake_create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _Pool()

    async def fake_apply_migrations(*args, **kwargs) -> None:
        _ = (args, kwargs)
        return

    async def ingest_twice_then_fail(input_, *, dsn):
        _ = dsn
        ingest_n["n"] += 1
        if ingest_n["n"] == 1:
            return ("accepted", ids.fact_ref, "accepted", "c1")
        if ingest_n["n"] == 2:
            raise RuntimeError("forced second ingest failure")
        raise AssertionError("unexpected third ingest")

    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.setattr(script.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(script, "apply_postgres_migrations", fake_apply_migrations)
    monkeypatch.setattr(script, "_cleanup_synthetic_rows", fake_cleanup)
    monkeypatch.setattr(script, "_new_synthetic_ids", lambda: ids)
    monkeypatch.setattr(script, "async_run_billing_ingest_from_parsed", ingest_twice_then_fail)

    with pytest.raises(RuntimeError):
        await script.run_operator_billing_ingest_apply_e2e()
    assert ingest_n["n"] == 2
    assert calls.count("cleanup") == 2
    assert calls[-1] == "pool.close"


@pytest.mark.asyncio
async def test_duplicate_ingest_invokes_ingest_twice_same_parsed_then_applies_twice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    script = _load_script_module()
    calls: list[str] = []
    ids = script._new_synthetic_ids()
    ingest_inputs: list[object] = []
    apply_refs: list[str] = []

    class _ConnCtx:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Pool:
        def acquire(self):
            return _ConnCtx()

        async def close(self) -> None:
            calls.append("pool.close")

    async def fake_cleanup(_conn, ids_obj) -> None:
        _ = ids_obj
        calls.append("cleanup")

    async def fake_create_pool(*args, **kwargs):
        _ = (args, kwargs)
        return _Pool()

    async def fake_apply_migrations(*args, **kwargs) -> None:
        _ = (args, kwargs)
        return

    async def fake_ingest(input_, *, dsn):
        _ = dsn
        ingest_inputs.append(input_)
        n = len(ingest_inputs)
        if n == 1:
            return ("accepted", ids.fact_ref, "accepted", "c1")
        if n == 2:
            return ("idempotent_replay", ids.fact_ref, "accepted", "c2")
        raise AssertionError("unexpected extra ingest")

    async def fake_apply(ref: str, *, dsn: str):
        _ = dsn
        apply_refs.append(ref)
        if len(apply_refs) == 1:
            return ApplyAcceptedBillingFactResult(
                operation_outcome=OperationOutcomeCategory.SUCCESS,
                idempotent_replay=False,
                apply_outcome=BillingSubscriptionApplyOutcome.ACTIVE_APPLIED,
            )
        if len(apply_refs) == 2:
            return ApplyAcceptedBillingFactResult(
                operation_outcome=OperationOutcomeCategory.IDEMPOTENT_NOOP,
                idempotent_replay=True,
                apply_outcome=BillingSubscriptionApplyOutcome.ACTIVE_APPLIED,
            )
        raise AssertionError("unexpected extra apply")

    async def fake_assert_active(pool, *, internal_user_id: str) -> None:
        _ = (pool, internal_user_id)

    monkeypatch.setenv("DATABASE_URL", "postgresql://local/dev")
    monkeypatch.setenv("BILLING_NORMALIZED_INGEST_ENABLE", "1")
    monkeypatch.setenv("BILLING_SUBSCRIPTION_APPLY_ENABLE", "1")
    monkeypatch.setattr(script.asyncpg, "create_pool", fake_create_pool)
    monkeypatch.setattr(script, "apply_postgres_migrations", fake_apply_migrations)
    monkeypatch.setattr(script, "_cleanup_synthetic_rows", fake_cleanup)
    monkeypatch.setattr(script, "_new_synthetic_ids", lambda: ids)
    monkeypatch.setattr(script, "async_run_billing_ingest_from_parsed", fake_ingest)
    monkeypatch.setattr(script, "async_run_apply", fake_apply)
    monkeypatch.setattr(script, "_assert_subscription_active", fake_assert_active)

    await script.run_operator_billing_ingest_apply_e2e()

    assert len(ingest_inputs) == 2
    assert ingest_inputs[0] is ingest_inputs[1]
    assert apply_refs == [ids.fact_ref, ids.fact_ref]
    assert calls.count("cleanup") == 2
    assert calls[-1] == "pool.close"


def test_synthetic_ids_use_required_prefix() -> None:
    script = _load_script_module()
    ids = script._new_synthetic_ids()
    assert ids.uid.startswith("operator-e2e-")
    assert ids.fact_ref.startswith("operator-e2e-")
    assert ids.ext_event_id.startswith("operator-e2e-")
    assert ids.correlation_id.startswith("operator-e2e-")


def test_leak_guard_rejects_forbidden_fragments() -> None:
    script = _load_script_module()
    for frag in _FORBIDDEN:
        with pytest.raises(RuntimeError, match="leak guard"):
            script._assert_no_forbidden_output(f"x {frag} y")
