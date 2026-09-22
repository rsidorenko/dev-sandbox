"""Читающая модель поддержки: фильтры по всей базе и безопасная диагностика."""

from datetime import UTC, datetime

_BASE = """
WITH users AS (
 SELECT i.internal_user_id, i.telegram_user_id, i.is_internal,
 COALESCE(i.trial_used,FALSE) trial_used, (i.telegram_user_id<0) is_web,
 s.state_label, s.active_until_utc, s.trial_expires_at, s.keys_deleted_at,
 s.plan_id, s.device_count, COALESCE(rb.balance_kopecks,0) balance_kopecks,
 CASE WHEN s.state_label='active' AND s.keys_deleted_at IS NULL
      AND GREATEST(s.active_until_utc,s.trial_expires_at)>NOW() THEN 'active'
      WHEN s.state_label IN ('active','expired') THEN 'expired'
      WHEN s.state_label='cancelled' THEN 'cancelled' ELSE 'none' END status,
 COALESCE(s.plan_id,'none') plan,
 CASE WHEN COALESCE(rb.balance_kopecks,0)<=0 THEN '0'
      WHEN rb.balance_kopecks<10000 THEN '1-99' ELSE '100+' END balance,
 CASE WHEN i.trial_used THEN 'used' ELSE 'not' END trial,
 CASE WHEN s.device_count IS NULL THEN 'none' WHEN s.device_count>=5 THEN '5+'
      ELSE s.device_count::text END devices,
 CASE WHEN i.is_internal THEN 'internal' ELSE 'regular' END kind
 FROM user_identities i
 LEFT JOIN subscription_snapshots s USING(internal_user_id)
 LEFT JOIN referral_balances rb USING(internal_user_id)
), searched AS (
 SELECT * FROM users WHERE $1='' OR POSITION(LOWER($1) IN LOWER(
 telegram_user_id::text || ' ' || internal_user_id))>0
)
"""
_FILTER = """
 WHERE ($8='status' OR cardinality($2::text[])=0 OR status=ANY($2))
 AND ($8='plan' OR cardinality($3::text[])=0 OR plan=ANY($3))
 AND ($8='balance' OR cardinality($4::text[])=0 OR balance=ANY($4))
 AND ($8='trial' OR cardinality($5::text[])=0 OR trial=ANY($5))
 AND ($8='devices' OR cardinality($6::text[])=0 OR devices=ANY($6))
 AND (cardinality($7::text[])=0 OR kind=ANY($7))
"""
_LIST = "".join(
    (
        _BASE,
        "SELECT * FROM searched ",
        _FILTER,
        """
 ORDER BY CASE WHEN $9='expiry' THEN GREATEST(active_until_utc,trial_expires_at) END ASC NULLS LAST,
 CASE WHEN $9='balance' THEN balance_kopecks END DESC NULLS LAST,
 telegram_user_id DESC, internal_user_id LIMIT $10 OFFSET $11
""",
    )
)
_COUNT = "".join((_BASE, "SELECT COUNT(*) FROM searched ", _FILTER))
_FACETS = "".join(
    (
        _BASE,
        "SELECT status,plan,balance,trial,devices,COUNT(*) count FROM searched ",
        _FILTER,
        " GROUP BY status,plan,balance,trial,devices",
    )
)


async def list_users(pool, params, limit: int, offset: int):
    """Одна согласованная транзакция для страницы, total и независимых фасет."""
    values = [params.get("search", "").strip()[:200]]
    values.extend(
        [v for v in params.get(name, "").split(",") if v][:30]
        for name in ("status", "plan", "balance", "trial", "devices", "kind")
    )
    sort = params.get("sort", "id")
    if sort not in {"id", "expiry", "balance"}:
        sort = "id"
    async with pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            rows = await conn.fetch(_LIST, *values, "", sort, limit, offset)
            total = await conn.fetchval(_COUNT, *values, "")
            facets = {}
            for name in ("status", "plan", "balance", "trial", "devices"):
                grouped = await conn.fetch(_FACETS, *values, name)
                counts = {}
                for row in grouped:
                    key = row[name]
                    counts[key] = counts.get(key, 0) + row["count"]
                facets[name] = [{"key": key, "count": count} for key, count in sorted(counts.items())]
    return rows, total, facets


def _json_rows(rows):
    return [
        {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}
        for row in rows
    ]


async def user_diagnostics(pool, uid):
    """Только сохранённые факты; не запускает синхронизацию и не читает ключи."""
    rotation = await pool.fetchval("SELECT vless_rotation_pending FROM user_identities WHERE internal_user_id=$1", uid)
    pending_count = await pool.fetchval(
        "SELECT COUNT(*) FROM balance_access_jobs WHERE internal_user_id=$1 AND status='pending'", uid
    )
    jobs = await pool.fetch(
        "SELECT status,attempts,created_at,updated_at,next_attempt_at FROM balance_access_jobs "
        "WHERE internal_user_id=$1 ORDER BY created_at DESC LIMIT 20",
        uid,
    )
    checks = await pool.fetch(
        "SELECT connected,detected_server_label,latency_ms,throughput_kbps,platform,created_at "
        "FROM connection_checks WHERE internal_user_id=$1 ORDER BY created_at DESC,id DESC LIMIT 20",
        uid,
    )
    actions = await pool.fetch(
        "SELECT action,days,old_active_until,new_active_until,provisioning,created_at "
        "FROM admin_subscription_actions WHERE internal_user_id=$1 ORDER BY created_at DESC,id DESC LIMIT 30",
        uid,
    )
    return {
        "observed_at": datetime.now(UTC).isoformat(),
        "source": "database",
        "rotation_pending": bool(rotation),
        "pending_access_jobs": int(pending_count),
        "access_jobs": _json_rows(jobs),
        "connection_checks": _json_rows(checks),
        "subscription_actions": _json_rows(actions),
        "live_access": "not_checked",
    }
