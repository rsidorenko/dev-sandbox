"""Unit tests for normalized billing operator JSON + async_main (mocks, no real DB)."""

from __future__ import annotations

import json
from datetime import UTC
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.application.billing_ingestion import IngestNormalizedBillingFactResult, NormalizedBillingFactInput
from app.application.billing_ingestion_main import (
    BILLING_NORMALIZED_INGEST_ENABLE,
    async_main,
    async_run_billing_ingest_from_parsed,
    parse_json_to_normalized_billing_input,
)
from app.persistence.billing_events_ledger_contracts import (
    BillingEventAmountCurrency,
    BillingEventLedgerRecord,
    BillingEventLedgerStatus,
)
from app.security.validation import ValidationError


def _min_json(overrides: dict | None = None) -> str:
    d = {
        "schema_version": 1,
        "billing_provider_key": "pkey",
        "external_event_id": "ext-1",
        "event_type": "payment_succeeded",
        "event_effective_at": "2026-01-10T08:00:00+00:00",
        "event_received_at": "2026-01-10T08:00:01+00:00",
        "status": "accepted",
        "ingestion_correlation_id": "corr-1",
    }
    if overrides:
        d |= overrides
    return json.dumps(d, separators=(",", ":"), sort_keys=True)


def test_parse_json_maps_to_normalized_billing_input() -> None:
    t = "2026-01-10T10:00:00+00:00"
    raw = _min_json(
        {
            "event_effective_at": t,
            "event_received_at": t,
            "internal_user_id": "user-a",
            "amount_currency": {"amount_minor_units": 50, "currency_code": "USD"},
        }
    )
    got = parse_json_to_normalized_billing_input(raw)
    assert isinstance(got, NormalizedBillingFactInput)
    assert got.billing_provider_key == "pkey"
    assert got.status == BillingEventLedgerStatus.ACCEPTED
    assert got.amount_currency == BillingEventAmountCurrency(50, "USD")
    assert got.internal_user_id == "user-a"


def test_reject_extra_field() -> None:
    p = _min_json()
    data = json.loads(p)
    data["raw_provider_payload"] = "x"
    with pytest.raises(ValidationError, match="unknown or disallowed"):
        parse_json_to_normalized_billing_input(json.dumps(data))


def test_reject_unsupported_schema_version() -> None:
    raw = _min_json({"schema_version": 2})
    with pytest.raises(ValidationError, match="schema_version must be 1"):
        parse_json_to_normalized_billing_input(raw)


def test_reject_invalid_json() -> None:
    with pytest.raises(ValidationError, match="not valid JSON"):
        parse_json_to_normalized_billing_input("not json{")


def test_reject_naive_datetime() -> None:
    raw = _min_json(
        {
            "event_effective_at": "2026-01-10T08:00:00",
            "event_received_at": "2026-01-10T10:00:00+00:00",
        }
    )
    with pytest.raises(ValidationError, match="timezone offset"):
        parse_json_to_normalized_billing_input(raw)


def test_non_string_required_field() -> None:
    data = json.loads(_min_json())
    data["billing_provider_key"] = 123
    with pytest.raises(ValidationError, match="billing_provider_key must be a non-empty string"):
        parse_json_to_normalized_billing_input(json.dumps(data))


@pytest.mark.asyncio
async def test_no_opt_in_does_not_call_ingest(monkeypatch, tmp_path: Path) -> None:
    f = tmp_path / "a.json"
    f.write_text(_min_json(), encoding="utf-8")
    with patch(
        "app.application.billing_ingestion_main.async_run_billing_ingest_from_parsed", new_callable=AsyncMock
    ) as m:
        monkeypatch.delenv(BILLING_NORMALIZED_INGEST_ENABLE, raising=False)
        code = await async_main(["--input-file", str(f)])
        assert code == 1
        m.assert_not_awaited()


@pytest.mark.asyncio
async def test_success_one_summary_line(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    f = tmp_path / "a.json"
    f.write_text(_min_json(), encoding="utf-8")
    with patch(
        "app.application.billing_ingestion_main.async_run_billing_ingest_from_parsed", new_callable=AsyncMock
    ) as m:
        m.return_value = ("accepted", "fact-uuid-1", "accepted", "corr-abc")
        monkeypatch.setenv(BILLING_NORMALIZED_INGEST_ENABLE, "1")
        monkeypatch.setenv("BOT_TOKEN", "0" * 20)
        monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@127.0.0.1:1/db")
        code = await async_main(["--input-file", str(f)])
        assert code == 0
        m.assert_awaited_once()
    out = capsys.readouterr()
    line = out.out.strip()
    assert line.count("\n") == 0, "expected a single line on stdout"
    assert "billing_normalized_ingest: ok" in line
    assert "internal_fact_ref=fact-uuid-1" in line
    assert "outcome=accepted" in line
    assert "status=accepted" in line
    assert "correlation_id=corr-abc" in line
    if out.err:
        assert "postgres" not in out.err.lower()


@pytest.mark.asyncio
async def test_extra_field_failure_stderr_safe(monkeypatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    p = _min_json()
    data = json.loads(p)
    data["sensitive_artifact"] = "ghp_xxxxxxxx"
    f = tmp_path / "a.json"
    f.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setenv(BILLING_NORMALIZED_INGEST_ENABLE, "1")
    monkeypatch.setenv("BOT_TOKEN", "0" * 20)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:secrethunter@192.0.2.0:5432/secretname")
    with patch(
        "app.application.billing_ingestion_main.async_run_billing_ingest_from_parsed", new_callable=AsyncMock
    ) as m:
        code = await async_main(["--input-file", str(f)])
    assert code == 1
    m.assert_not_awaited()
    err = capsys.readouterr().err
    assert "ghp_" not in err
    assert "secrethunter" not in err
    assert "{" not in err
    assert "billing_normalized_ingest: failed" in err


def test_reject_unsupported_schema_string_version() -> None:
    raw = _min_json()
    d = json.loads(raw)
    d["schema_version"] = "1"
    with pytest.raises(ValidationError, match="schema_version must be 1"):
        parse_json_to_normalized_billing_input(json.dumps(d))


@pytest.mark.asyncio
async def test_async_run_billing_ingest_uses_postgres_atomic_billing_ingestion() -> None:
    """Wiring: one PostgresAtomicBillingIngestion ingest per DSN run (no split ledger+audit pool calls)."""
    from datetime import datetime

    t = datetime(2026, 1, 18, 9, 0, 0, tzinfo=UTC)
    inp = NormalizedBillingFactInput(
        billing_provider_key="p_wiring",
        external_event_id="ext-wiring-1",
        event_type="payment_succeeded",
        event_effective_at=t,
        event_received_at=t,
        status=BillingEventLedgerStatus.ACCEPTED,
        ingestion_correlation_id="corr-wiring",
    )
    rec = BillingEventLedgerRecord(
        internal_fact_ref="if-ref-1",
        billing_provider_key="p_wiring",
        external_event_id="ext-wiring-1",
        event_type="payment_succeeded",
        event_effective_at=t,
        event_received_at=t,
        internal_user_id=None,
        checkout_attempt_id=None,
        amount_currency=None,
        status=BillingEventLedgerStatus.ACCEPTED,
        ingestion_correlation_id="corr-wiring",
    )
    result = IngestNormalizedBillingFactResult(record=rec, is_idempotent_replay=False)
    pool = MagicMock()
    pool.close = AsyncMock()

    async def fake_open(_dsn: str):
        return pool

    with patch("app.application.billing_ingestion_main.PostgresAtomicBillingIngestion") as c_atomic:
        inst = c_atomic.return_value
        inst.ingest_normalized_billing_fact = AsyncMock(return_value=result)
        out = await async_run_billing_ingest_from_parsed(
            inp,
            dsn="postgresql://u:p@127.0.0.1:5432/wiring",
            open_pool=fake_open,
        )
        c_atomic.assert_called_once_with(pool)
        inst.ingest_normalized_billing_fact.assert_awaited_once_with(inp)
    assert out == ("accepted", "if-ref-1", "accepted", "corr-wiring")
    pool.close.assert_awaited_once()
