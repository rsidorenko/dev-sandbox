"""Зарегистрированные платёжные факты и независимые стадии исполнения."""

from datetime import UTC, datetime, timedelta

from starlette.responses import JSONResponse

from app.web_api.admin_auth import _with_admin_auth

_PAYMENTS = """
WITH payments AS (
 SELECT 'external:'||b.internal_fact_ref row_id,b.event_received_at occurred_at,b.internal_user_id,
 b.billing_provider_key provider,b.event_type payment_kind,b.amount_minor_units,b.currency_code,
 b.status payment_status,COALESCE(b.checkout_attempt_id,b.external_event_id) reference,
 CASE WHEN b.event_type='device_topup' THEN 'not_applicable'
 ELSE COALESCE(a.apply_outcome,'not_recorded') END apply_status,
 'unknown'::text access_status,NULL::integer access_attempts,NULL::timestamptz access_created_at,
 NULL::timestamptz access_next_attempt_at
 FROM billing_events_ledger b LEFT JOIN billing_subscription_apply_records a USING(internal_fact_ref)
 WHERE b.event_received_at >= $1 AND b.event_received_at <= $2
 UNION ALL
 SELECT 'balance:'||t.transaction_id,t.created_at,t.internal_user_id,'balance','balance_purchase',
 -t.amount_kopecks,'RUB','accepted',t.transaction_id,
 CASE WHEN j.operation_key IS NULL THEN 'legacy_unknown' ELSE 'applied_atomically' END,
 COALESCE(j.status,'legacy_unknown'),j.attempts,j.created_at,j.next_attempt_at
 FROM referral_transactions t LEFT JOIN balance_access_jobs j
 ON j.internal_user_id=t.internal_user_id AND j.operation_key=t.idempotency_key
 WHERE t.transaction_type='subscription_payment' AND t.amount_kopecks<0
 AND t.created_at >= $1 AND t.created_at <= $2
), classified AS (
 SELECT p.*,u.telegram_user_id,
 CASE WHEN payment_status='accepted' AND p.internal_user_id IS NULL THEN 'missing_user'
 WHEN apply_status='needs_review' THEN 'apply_needs_review'
 WHEN payment_status='accepted' AND payment_kind='subscription_activated'
 AND apply_status IN ('not_recorded','no_activation') AND occurred_at < $2-INTERVAL '10 minutes'
 THEN 'subscription_not_applied'
 WHEN access_status='pending' AND access_created_at < $2-INTERVAL '10 minutes' THEN 'access_delayed'
 ELSE NULL END attention_reason
 FROM payments p LEFT JOIN user_identities u ON u.internal_user_id=p.internal_user_id
), filtered AS (
 SELECT * FROM classified WHERE ($3='' OR POSITION(LOWER($3) IN LOWER(
 COALESCE(telegram_user_id::text,'')||' '||COALESCE(internal_user_id,'')||' '||reference))>0)
 AND ($4='' OR provider=$4) AND (NOT $5 OR attention_reason IS NOT NULL)
)
"""
_LIST = "".join((_PAYMENTS, "SELECT * FROM filtered ORDER BY occurred_at DESC,row_id DESC LIMIT $6 OFFSET $7"))
_COUNT = "".join((_PAYMENTS, "SELECT COUNT(*) FROM filtered"))


async def payment_snapshot(pool, params):
    try:
        days = int(params.get("days", "30"))
    except ValueError:
        days = 30
    if days not in {1, 7, 30, 90}:
        days = 30
    try:
        page = max(1, min(100000, int(params.get("page", "1"))))
    except ValueError:
        page = 1
    now = datetime.now(UTC)
    args = (
        now - timedelta(days=days),
        now,
        params.get("search", "").strip()[:200],
        params.get("provider", "")[:50],
        params.get("attention") == "1",
    )
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            rows = await conn.fetch(_LIST, *args, 50, (page - 1) * 50)
            total = await conn.fetchval(_COUNT, *args)
    items = []
    for row in rows:
        item = dict(row)
        for key in ("occurred_at", "access_created_at", "access_next_attempt_at"):
            item[key] = item[key].isoformat() if item[key] else None
        items.append(item)
    return {
        "ok": True,
        "observed_at": now.isoformat(),
        "days": days,
        "page": page,
        "limit": 50,
        "total": total,
        "payments": items,
    }


@_with_admin_auth
async def handle_admin_payments(request):
    return JSONResponse(await payment_snapshot(request.app.state.pool, request.query_params))
