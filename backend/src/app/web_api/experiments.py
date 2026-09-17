"""Авторизованное разрешение first-party A/B-назначений."""

from __future__ import annotations

import logging

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.application.funnel_events import record_interaction
from app.domain.experiments import assign_experiment_variant, get_experiment
from app.domain.plans import get_plan
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)


async def handle_resolve_experiment(request: Request) -> JSONResponse:
    claims = request.state.user
    internal_user_id = claims.get("internal_user_id")
    if not isinstance(internal_user_id, str) or not internal_user_id:
        return safe_json_error(403, "no_identity")

    try:
        payload = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_json")
    if not isinstance(payload, dict):
        return safe_json_error(400, "invalid_payload")

    experiment_key = payload.get("experiment_key")
    if not isinstance(experiment_key, str) or get_experiment(experiment_key) is None:
        return safe_json_error(400, "unknown_experiment")

    plan_id = payload.get("plan_id")
    if plan_id is not None and (not isinstance(plan_id, str) or get_plan(plan_id) is None):
        return safe_json_error(400, "invalid_plan_id")

    proposed_variant = assign_experiment_variant(
        internal_user_id=internal_user_id,
        experiment_key=experiment_key,
    )
    pool: asyncpg.Pool = request.app.state.pool
    try:
        await pool.execute(
            "INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant) "
            "VALUES ($1, $2, $3) ON CONFLICT (internal_user_id, experiment_key) DO NOTHING",
            internal_user_id,
            experiment_key,
            proposed_variant,
        )
        row = await pool.fetchrow(
            "SELECT experiment_key, variant, assigned_at FROM experiment_assignments "
            "WHERE internal_user_id = $1 AND experiment_key = $2",
            internal_user_id,
            experiment_key,
        )
    except Exception:
        _LOGGER.exception("experiment_assignment_failed experiment=%s", experiment_key)
        return safe_json_error(500, "experiment_unavailable")

    if plan_id is not None:
        await record_interaction(
            pool,
            event_name="checkout_opened",
            surface="web",
            dedup_key=f"experiment:{experiment_key}:{internal_user_id}:{plan_id}",
            internal_user_id=internal_user_id,
            plan_id=plan_id,
        )

    if row is None:
        return safe_json_error(500, "experiment_unavailable")

    return JSONResponse(
        {
            "ok": True,
            "experiment": {
                "key": row["experiment_key"],
                "variant": row["variant"],
                "assigned_at": row["assigned_at"].isoformat(),
            },
        }
    )
