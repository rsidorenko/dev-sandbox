from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.domain.experiments import (
    CHECKOUT_REASSURANCE_EXPERIMENT,
    assign_experiment_variant,
    get_experiment,
)
from app.web_api.admin_stats import _experiment_results, _product_funnel
from app.web_api.experiments import checkout_reassurance_enabled, handle_resolve_experiment


def test_assignment_is_stable_and_uses_both_variants() -> None:
    first = assign_experiment_variant(
        internal_user_id="user-42",
        experiment_key=CHECKOUT_REASSURANCE_EXPERIMENT,
    )
    assert first == assign_experiment_variant(
        internal_user_id="user-42",
        experiment_key=CHECKOUT_REASSURANCE_EXPERIMENT,
    )
    variants = {
        assign_experiment_variant(
            internal_user_id=f"user-{index}",
            experiment_key=CHECKOUT_REASSURANCE_EXPERIMENT,
        )
        for index in range(100)
    }
    assert variants == {"control", "reassurance"}


def test_unknown_experiment_is_rejected() -> None:
    assert get_experiment("unknown") is None
    with pytest.raises(ValueError, match="unknown_experiment"):
        assign_experiment_variant(internal_user_id="user", experiment_key="unknown")


class _AssignmentPool:
    def __init__(self) -> None:
        self.variant: str | None = None
        self.calls = 0

    async def execute(self, _query: str, _user_id: str, experiment_key: str, proposed: str):
        self.calls += 1
        if self.variant is None:
            self.variant = proposed
        return "INSERT 0 1"

    async def fetchrow(self, _query: str, _user_id: str, experiment_key: str):
        return {
            "experiment_key": experiment_key,
            "variant": self.variant,
            "assigned_at": datetime(2026, 9, 17, tzinfo=UTC),
        }


class _Request:
    def __init__(self, pool: _AssignmentPool, payload: dict) -> None:
        self.state = SimpleNamespace(user={"internal_user_id": "user-42"})
        self.app = SimpleNamespace(state=SimpleNamespace(pool=pool))
        self._payload = payload

    async def json(self) -> dict:
        return self._payload


@pytest.mark.asyncio
async def test_resolve_endpoint_preserves_first_assignment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERIMENT_CHECKOUT_REASSURANCE_ENABLE", "1")
    pool = _AssignmentPool()
    request = _Request(pool, {"experiment_key": CHECKOUT_REASSURANCE_EXPERIMENT})
    first = await handle_resolve_experiment(request)  # type: ignore[arg-type]
    second = await handle_resolve_experiment(request)  # type: ignore[arg-type]
    assert first.status_code == 200
    assert first.body == second.body
    assert pool.calls == 2


@pytest.mark.asyncio
async def test_resolve_endpoint_does_not_assign_disabled_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERIMENT_CHECKOUT_REASSURANCE_ENABLE", raising=False)
    pool = _AssignmentPool()
    response = await handle_resolve_experiment(  # type: ignore[arg-type]
        _Request(pool, {"experiment_key": CHECKOUT_REASSURANCE_EXPERIMENT})
    )
    assert response.status_code == 409
    assert b'"error":"experiment_disabled"' in response.body
    assert pool.calls == 0


def test_checkout_reassurance_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("EXPERIMENT_CHECKOUT_REASSURANCE_ENABLE", raising=False)
    assert checkout_reassurance_enabled() is False
    monkeypatch.setenv("EXPERIMENT_CHECKOUT_REASSURANCE_ENABLE", "true")
    assert checkout_reassurance_enabled() is True


@pytest.mark.asyncio
async def test_resolve_endpoint_rejects_trial_experiment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EXPERIMENT_CHECKOUT_REASSURANCE_ENABLE", "1")
    response = await handle_resolve_experiment(  # type: ignore[arg-type]
        _Request(_AssignmentPool(), {"experiment_key": "trial_channel_gate_v1"})
    )
    assert response.status_code == 400
    assert b'"error":"unsupported_experiment"' in response.body


@pytest.mark.asyncio
async def test_resolve_endpoint_rejects_unknown_key() -> None:
    response = await handle_resolve_experiment(  # type: ignore[arg-type]
        _Request(_AssignmentPool(), {"experiment_key": "unknown"})
    )
    assert response.status_code == 400


class _StatsPool:
    async def fetchrow(self, _query: str, *_args):
        return {
            "registered_users": 10,
            "trial_started_users": 8,
            "setup_started_users": 7,
            "platform_selected_users": 6,
            "connection_confirmed_users": 5,
            "checkout_opened_users": 4,
            "payment_created_users": 3,
            "payment_succeeded_users": 2,
        }

    async def fetch(self, _query: str, *_args):
        return [
            {
                "experiment_key": CHECKOUT_REASSURANCE_EXPERIMENT,
                "variant": "reassurance",
                "exposures": 10,
                "mature_exposures": 5,
                "pending_exposures": 5,
                "payment_created_users": 4,
                "paid_users": 2,
                "meaningful_traffic_users": 5,
                "revenue_kopecks": 49800,
            }
        ]


@pytest.mark.asyncio
async def test_stats_helpers_return_rates_and_revenue() -> None:
    pool = _StatsPool()
    now = datetime.now(UTC)
    funnel = await _product_funnel(pool, now, now)  # type: ignore[arg-type]
    assert funnel["registered_users"] == 10
    assert funnel["payment_succeeded_users"] == 2
    experiments = await _experiment_results(pool, now, now)  # type: ignore[arg-type]
    assert experiments == [
        {
            "experiment_key": CHECKOUT_REASSURANCE_EXPERIMENT,
            "variant": "reassurance",
            "exposures": 10,
            "mature_exposures": 5,
            "pending_exposures": 5,
            "payment_created_users": 4,
            "paid_users": 2,
            "meaningful_traffic_users": 5,
            "revenue_rubles": 498.0,
            "checkout_rate": 0.8,
            "paid_rate": 0.4,
            "meaningful_traffic_rate": 1.0,
        }
    ]
