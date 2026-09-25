"""Воронка явного бонусного Telegram-checkout, успех подтверждается ledger."""

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.admin_auth import _with_admin_auth

BROADCAST_FUNNEL_SQL = """
WITH jobs AS (
    SELECT job_id, created_at FROM broadcast_jobs
    WHERE bonus->>'type' = 'promo'
    ORDER BY created_at DESC LIMIT 50
), events AS (
    SELECT f.* FROM broadcast_bonus_funnel f
    JOIN jobs j USING (job_id)
    JOIN analytics_eligible_user_ids eligible USING (internal_user_id)
), paid AS (
    SELECT DISTINCT ON (e.payment_id) e.job_id, e.internal_user_id, e.payment_id, b.amount_minor_units
    FROM events e JOIN billing_events_ledger b
      ON b.external_event_id = 'yookassa:' || e.payment_id
     AND b.internal_user_id = e.internal_user_id
    WHERE e.stage = 'payment_created'
      AND b.billing_provider_key IN ('yookassa', 'yookassa_v1')
      AND b.status = 'accepted' AND b.event_type = 'subscription_activated'
      AND b.amount_minor_units > 0 AND b.currency_code = 'RUB'
      AND b.internal_fact_ref NOT LIKE 'grant:%'
    ORDER BY e.payment_id, b.event_received_at, b.internal_fact_ref
)
SELECT j.job_id, j.created_at,
       min(e.occurred_at) AS first_recorded_at,
       count(DISTINCT e.internal_user_id) FILTER (WHERE e.stage = 'bonus_claimed') AS claimed_users,
       count(DISTINCT e.internal_user_id) FILTER (WHERE e.stage = 'plan_selected') AS selected_users,
       count(DISTINCT e.internal_user_id) FILTER (WHERE e.stage = 'payment_created') AS payment_users,
       count(*) FILTER (WHERE e.stage = 'payment_created') AS payments_created,
       (SELECT count(DISTINCT p.internal_user_id) FROM paid p WHERE p.job_id=j.job_id) AS paid_users,
       (SELECT count(*) FROM paid p WHERE p.job_id=j.job_id) AS payments_succeeded,
       COALESCE((SELECT sum(p.amount_minor_units) FROM paid p WHERE p.job_id=j.job_id), 0)::bigint AS revenue_kopecks
FROM jobs j LEFT JOIN events e USING (job_id)
GROUP BY j.job_id, j.created_at
ORDER BY j.created_at DESC
"""


@_with_admin_auth
async def handle_admin_broadcast_funnel(request: Request) -> JSONResponse:
    rows = await request.app.state.pool.fetch(BROADCAST_FUNNEL_SQL)
    jobs = [
        {
            "job_id": str(row["job_id"]),
            "created_at": row["created_at"].isoformat(),
            "first_recorded_at": row["first_recorded_at"].isoformat() if row["first_recorded_at"] else None,
            **{
                key: int(row[key])
                for key in (
                    "claimed_users",
                    "selected_users",
                    "payment_users",
                    "payments_created",
                    "paid_users",
                    "payments_succeeded",
                    "revenue_kopecks",
                )
            },
        }
        for row in rows
    ]
    return JSONResponse({"ok": True, "jobs": jobs})
