"""Админ-эндпоинт вкладки «Статистика»: агрегаты выручки/регистраций/триалов/подписок за период.

Один GET-эндпоинт возвращает весь дашборд одним JSON-ответом (один round-trip с фронта):
KPI-карточки, три временных ряда (выручка / новые пользователи / триалы) и распределение
активных подписок по тарифам. Образец оформления — web_api/admin_users.py (@_with_admin_auth,
pool из app.state, SQL литералами с $N-параметрами, JSONResponse({"ok": True, ...})).

Источник выручки — billing_events_ledger (status='accepted', amount_minor_units).
Внутренние/операторские гранты исключены: grant_subscription.py пишет факты с
internal_fact_ref LIKE 'grant:%' (под provider_key 'yookassa'), ручные гранты —
provider_key='manual_grant'. Реальные YooKassa-платежи не имеют этих маркеров.
Кавеат: amount_minor_units был NULL до PR #384 и НЕ перезалит → историческая выручка
до PR #384 занижена (вынесено в примечание UI).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.admin_auth import _with_admin_auth
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

_DEFAULT_PERIOD = "7d"
_DEFAULT_TZ = "Europe/Moscow"

# Белый список IANA-зон: не пускаем произвольный текст в SQL (AT TIME ZONE $tz).
_ALLOWED_TZ = frozenset(
    {
        "Europe/Moscow",
        "Europe/Kaliningrad",
        "Europe/Samara",
        "Asia/Yekaterinburg",
        "Asia/Omsk",
        "Asia/Krasnoyarsk",
        "Asia/Novosibirsk",
        "Asia/Irkutsk",
        "Asia/Vladivostok",
        "UTC",
    }
)

# Период → (дней в окне включая сегодня, бакет группировки). None дней = без нижней границы.
_PERIODS: dict[str, tuple[int | None, str]] = {
    "1d": (1, "hour"),
    "7d": (7, "day"),
    "30d": (30, "day"),
    "90d": (90, "day"),
    "all": (None, "month"),
}

# Sentinel «от начала времён» для периода all (приложение запущено с 2026).
_ALL_FROM = datetime(2025, 1, 1, tzinfo=UTC)


def _compute_window(period_key: str, tz_name: str) -> tuple[datetime, str]:
    """Возвращает (from_ts UTC, бакет) для периода: старт окна в локальной зоне, переведённый в UTC."""
    days, trunc = _PERIODS[period_key]
    if days is None:
        return _ALL_FROM, trunc
    zone = ZoneInfo(tz_name)
    now_local = datetime.now(UTC).astimezone(zone)
    start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    from_local = start_today if days == 1 else start_today - timedelta(days=days - 1)
    return from_local.astimezone(UTC), trunc


# Полные SQL как неявная конкатенация литералов (без '+'/интерполяции) — bandit S608 не срабатывает;
# значения подставляются по имени в fetch. $1=trunc, $2=tz, $3=from_ts (там, где нужно).

_REVENUE_SERIES = (
    "SELECT date_trunc($1, event_effective_at AT TIME ZONE $2) AS bucket, "
    "COALESCE(SUM(amount_minor_units), 0)::bigint AS kopecks, COUNT(*) AS n "
    "FROM billing_events_ledger "
    "WHERE status = 'accepted' AND event_effective_at >= $3 "
    "AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = billing_events_ledger.internal_user_id AND i.is_internal) "
    "AND internal_fact_ref NOT LIKE 'grant:%' AND billing_provider_key <> 'manual_grant' "
    "GROUP BY bucket ORDER BY bucket"
)

_REVENUE_KPI = (
    "SELECT COALESCE(SUM(amount_minor_units), 0)::bigint AS kopecks, "
    "COUNT(*) AS payments, COUNT(DISTINCT internal_user_id) AS paying_users "
    "FROM billing_events_ledger WHERE status = 'accepted' AND event_effective_at >= $1 "
    "AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = billing_events_ledger.internal_user_id AND i.is_internal) "
    "AND internal_fact_ref NOT LIKE 'grant:%' AND billing_provider_key <> 'manual_grant'"
)

_NEW_USERS_SERIES = (
    "SELECT date_trunc($1, created_at AT TIME ZONE $2) AS bucket, COUNT(*) AS n "
    "FROM user_identities WHERE created_at >= $3 AND NOT is_internal GROUP BY bucket ORDER BY bucket"
)

_NEW_USERS_KPI = "SELECT COUNT(*) FROM user_identities WHERE created_at >= $1 AND NOT is_internal"

_TRIALS_SERIES = (
    "SELECT date_trunc($1, trial_started_at AT TIME ZONE $2) AS bucket, COUNT(*) AS n "
    "FROM subscription_snapshots "
    "WHERE trial_started_at IS NOT NULL AND trial_started_at >= $3 "
    "AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = subscription_snapshots.internal_user_id AND i.is_internal) "
    "GROUP BY bucket ORDER BY bucket"
)

_TRIALS_KPI = (
    "SELECT COUNT(*) FROM subscription_snapshots WHERE trial_started_at IS NOT NULL AND trial_started_at >= $1"
    " AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = subscription_snapshots.internal_user_id AND i.is_internal)"
)

# Конверсия триал→платный за период: доля юзеров с триалом в окне, у которых есть ≥1 accepted-факт.
_TRIAL_PAID = (
    "SELECT COUNT(*) FILTER (WHERE t.internal_user_id IS NOT NULL) AS trials, "
    "COUNT(*) FILTER (WHERE t.internal_user_id IS NOT NULL AND EXISTS ("
    "SELECT 1 FROM billing_events_ledger b WHERE b.status = 'accepted' "
    "AND b.internal_user_id = t.internal_user_id "
    "AND b.internal_fact_ref NOT LIKE 'grant:%' AND b.billing_provider_key <> 'manual_grant'"
    ")) AS converted "
    "FROM subscription_snapshots t "
    "WHERE t.trial_started_at IS NOT NULL AND t.trial_started_at >= $1"
    " AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = t.internal_user_id AND i.is_internal)"
)

_ACTIVE_BY_PLAN = (
    "SELECT plan_id, COUNT(*) AS n FROM subscription_snapshots "
    "WHERE state_label = 'active' AND active_until_utc > now() AND plan_id IS NOT NULL "
    "AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = subscription_snapshots.internal_user_id AND i.is_internal) "
    "GROUP BY plan_id ORDER BY n DESC"
)

# bare_xray-трафик (сбор с 2026-09-07): stat_date — DATE в MSK, поэтому группировка
# всегда по дню, без TZ-конверсий (в отличие от временных рядов выше). ГБ = up+down.
_TRAFFIC_SERIES = (
    "SELECT stat_date AS bucket, "
    "ROUND(SUM(uplink_bytes + downlink_bytes) / 1073741824.0, 2) AS gb, "
    "COUNT(DISTINCT email) AS users "
    "FROM bare_xray_traffic_daily WHERE stat_date >= $1::date "
    "GROUP BY stat_date ORDER BY stat_date"
)

_TRAFFIC_BY_SERVER = (
    "SELECT COALESCE(v.label, 'server ' || t.server_id::text) AS label, "
    "ROUND(SUM(t.uplink_bytes + t.downlink_bytes) / 1073741824.0, 2) AS gb, "
    "COUNT(DISTINCT t.email) AS users "
    "FROM bare_xray_traffic_daily t LEFT JOIN vpn_servers v ON v.id = t.server_id "
    "WHERE t.stat_date >= $1::date "
    "GROUP BY t.server_id, label ORDER BY gb DESC"
)

_ACTIVE_TOTAL = (
    "SELECT COUNT(*) FROM subscription_snapshots WHERE state_label = 'active' AND active_until_utc > now()"
    " AND NOT EXISTS (SELECT 1 FROM user_identities i WHERE i.internal_user_id = subscription_snapshots.internal_user_id AND i.is_internal)"
)

_MATURE_FUNNEL = """
WITH trials AS (
    SELECT t.internal_user_id, t.trial_started_at,
           t.trial_started_at + interval '10 days' AS deadline
    FROM subscription_snapshots t
    JOIN user_identities i USING (internal_user_id)
    WHERE NOT i.is_internal AND t.trial_started_at >= $1 AND t.trial_started_at <= $2
), cohort AS (
    SELECT t.*, (SELECT min(e.occurred_at) FROM funnel_events e
                 WHERE e.internal_user_id = t.internal_user_id
                   AND e.event_name = 'payment_succeeded') AS first_paid_at
    FROM trials t
)
SELECT count(*) AS trials,
       count(*) FILTER (WHERE deadline <= $2) AS mature_trials,
       count(*) FILTER (WHERE deadline > $2) AS pending_trials,
       count(*) FILTER (WHERE deadline <= $2 AND first_paid_at >= trial_started_at
                        AND first_paid_at < deadline) AS converted_trials
FROM cohort
"""

_TELEGRAM_ADS_CAMPAIGNS_LIMIT = 200
_TELEGRAM_ADS_CAMPAIGNS = """
WITH attributed AS (
    SELECT a.campaign_key, a.internal_user_id, a.attributed_at
    FROM telegram_ads_attributions a
    JOIN user_identities i USING (internal_user_id)
    WHERE NOT i.is_internal AND a.attributed_at >= $1 AND a.attributed_at <= $2
), payment_ranked AS (
    SELECT e.internal_user_id, e.occurred_at,
           row_number() OVER (PARTITION BY e.internal_user_id ORDER BY e.occurred_at, e.event_key) AS payment_number
    FROM funnel_events e
    JOIN attributed a USING (internal_user_id)
    WHERE e.event_name = 'payment_succeeded'
), payment_moments AS (
    SELECT internal_user_id,
           min(occurred_at) FILTER (WHERE payment_number = 1) AS first_paid_at,
           min(occurred_at) FILTER (WHERE payment_number = 2) AS second_paid_at
    FROM payment_ranked
    GROUP BY internal_user_id
), base AS (
    SELECT a.campaign_key, a.internal_user_id, a.attributed_at,
           CASE WHEN s.trial_started_at >= a.attributed_at THEN s.trial_started_at END AS trial_started_at,
           p.first_paid_at, p.second_paid_at
    FROM attributed a
    LEFT JOIN subscription_snapshots s USING (internal_user_id)
    LEFT JOIN payment_moments p USING (internal_user_id)
), cohort AS (
    SELECT b.*, b.trial_started_at + interval '10 days' AS deadline
    FROM base b
)
SELECT campaign_key,
       count(*) AS attributed_users,
       count(*) FILTER (WHERE trial_started_at IS NOT NULL) AS trials_started,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM funnel_events e WHERE e.internal_user_id = cohort.internal_user_id
           AND cohort.trial_started_at IS NOT NULL
           AND e.event_name = 'setup_started' AND e.occurred_at >= cohort.trial_started_at
       )) AS setup_started_users,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM funnel_events e WHERE e.internal_user_id = cohort.internal_user_id
           AND cohort.trial_started_at IS NOT NULL
           AND e.event_name = 'connection_confirmed' AND e.occurred_at >= cohort.trial_started_at
       )) AS connection_confirmed_users,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM funnel_events e WHERE e.internal_user_id = cohort.internal_user_id
           AND cohort.trial_started_at IS NOT NULL
           AND e.event_name = 'checkout_opened' AND e.occurred_at >= cohort.trial_started_at
       )) AS checkout_opened_users,
       count(*) FILTER (WHERE EXISTS (
           SELECT 1 FROM funnel_events e WHERE e.internal_user_id = cohort.internal_user_id
           AND cohort.trial_started_at IS NOT NULL
           AND e.event_name = 'payment_created' AND e.occurred_at >= cohort.trial_started_at
       )) AS payment_created_users,
       count(*) FILTER (WHERE deadline <= $2) AS mature_trials,
       count(*) FILTER (WHERE deadline > $2) AS pending_trials,
       count(*) FILTER (WHERE deadline <= $2 AND first_paid_at >= trial_started_at
                        AND first_paid_at < deadline) AS first_payment_users,
       count(*) FILTER (WHERE deadline <= $2 AND first_paid_at >= trial_started_at
                        AND first_paid_at < deadline AND second_paid_at IS NOT NULL) AS second_payment_users
FROM cohort
GROUP BY campaign_key
ORDER BY attributed_users DESC, campaign_key
LIMIT 200
"""

_TELEGRAM_ADS_CAMPAIGNS_TOTAL = (
    "SELECT count(DISTINCT a.campaign_key) FROM telegram_ads_attributions a "
    "JOIN user_identities i USING (internal_user_id) "
    "WHERE NOT i.is_internal AND a.attributed_at >= $1 AND a.attributed_at <= $2"
)


async def _mature_funnel(pool: asyncpg.Pool, from_ts: datetime, now: datetime) -> dict:
    row = await pool.fetchrow(_MATURE_FUNNEL, from_ts, now)
    mature = int(row["mature_trials"])
    converted = int(row["converted_trials"])
    return {
        "window_days": 10,
        "trials": int(row["trials"]),
        "mature_trials": mature,
        "pending_trials": int(row["pending_trials"]),
        "converted_trials": converted,
        "trial_to_first_payment_rate": round(converted / mature, 4) if mature else None,
    }


async def _telegram_ads_campaigns(pool: asyncpg.Pool, from_ts: datetime, now: datetime) -> list[dict]:
    rows = await pool.fetch(_TELEGRAM_ADS_CAMPAIGNS, from_ts, now)
    result = []
    for row in rows:
        attributed = int(row["attributed_users"])
        trials = int(row["trials_started"])
        mature = int(row["mature_trials"])
        first_payment = int(row["first_payment_users"])
        result.append(
            {
                "campaign_key": row["campaign_key"],
                "attributed_users": attributed,
                "trials_started": trials,
                "setup_started_users": int(row["setup_started_users"]),
                "connection_confirmed_users": int(row["connection_confirmed_users"]),
                "checkout_opened_users": int(row["checkout_opened_users"]),
                "payment_created_users": int(row["payment_created_users"]),
                "mature_trials": mature,
                "pending_trials": int(row["pending_trials"]),
                "first_payment_users": first_payment,
                "second_payment_users": int(row["second_payment_users"]),
                "trial_activation_rate": round(trials / attributed, 4) if attributed else None,
                "trial_to_first_payment_rate": round(first_payment / mature, 4) if mature else None,
            }
        )
    return result


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _revenue_series(pool: asyncpg.Pool, trunc: str, tz: str, from_ts: datetime) -> list[dict]:
    rows = await pool.fetch(_REVENUE_SERIES, trunc, tz, from_ts)
    return [
        {"bucket": _iso(r["bucket"]), "rubles": round(int(r["kopecks"]) / 100, 2), "count": int(r["n"])} for r in rows
    ]


async def _revenue_kpi(pool: asyncpg.Pool, from_ts: datetime) -> dict:
    row = await pool.fetchrow(_REVENUE_KPI, from_ts)
    return {
        "revenue_rubles": round(int(row["kopecks"]) / 100, 2),
        "payments_count": int(row["payments"]),
        "paying_users": int(row["paying_users"]),
    }


async def _count_series(pool: asyncpg.Pool, sql: str, trunc: str, tz: str, from_ts: datetime) -> list[dict]:
    rows = await pool.fetch(sql, trunc, tz, from_ts)
    return [{"bucket": _iso(r["bucket"]), "count": int(r["n"])} for r in rows]


async def _scalar(pool: asyncpg.Pool, sql: str, *args) -> int:
    """Скалярный COUNT(*) — args передаются как $N ровно столько, сколько плейсхолдеров в SQL."""
    return int(await pool.fetchval(sql, *args) or 0)


async def _trial_paid_rate(pool: asyncpg.Pool, from_ts: datetime) -> float | None:
    row = await pool.fetchrow(_TRIAL_PAID, from_ts)
    trials = int(row["trials"] or 0)
    if trials == 0:
        return None
    return round(int(row["converted"] or 0) / trials, 4)


async def _active_by_plan(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(_ACTIVE_BY_PLAN)
    return [{"plan_id": r["plan_id"], "count": int(r["n"])} for r in rows]


async def _traffic_series(pool: asyncpg.Pool, from_date: date) -> list[dict]:
    rows = await pool.fetch(_TRAFFIC_SERIES, from_date)
    return [{"bucket": r["bucket"].isoformat(), "gb": float(r["gb"]), "users": int(r["users"])} for r in rows]


async def _traffic_by_server(pool: asyncpg.Pool, from_date: date) -> list[dict]:
    rows = await pool.fetch(_TRAFFIC_BY_SERVER, from_date)
    return [{"label": r["label"], "gb": float(r["gb"]), "users": int(r["users"])} for r in rows]


@_with_admin_auth
async def handle_admin_stats(request: Request) -> JSONResponse:
    """GET /api/v1/admin/stats?period=7d&tz=Europe/Moscow — агрегированный дашборд статистики."""
    pool: asyncpg.Pool = request.app.state.pool

    period = (request.query_params.get("period") or _DEFAULT_PERIOD).strip()
    if period not in _PERIODS:
        return safe_json_error(400, "invalid_period", f"period must be one of {sorted(_PERIODS)}")

    tz = (request.query_params.get("tz") or _DEFAULT_TZ).strip()
    if tz not in _ALLOWED_TZ:
        return safe_json_error(400, "invalid_tz", "Unsupported timezone")

    from_ts, trunc = _compute_window(period, tz)
    now = datetime.now(UTC)
    # stat_date — DATE в MSK: нижняя граница окна в той же зоне, чтобы 1d/7d совпали с рядом.
    from_date = from_ts.astimezone(ZoneInfo(tz)).date()

    # Все агрегаты — независимы, гоняем параллельно (каждый берёт своё соединение из pool).
    (
        revenue_series,
        revenue_kpi,
        new_users_series,
        new_users_kpi,
        trials_series,
        trials_kpi,
        trial_paid_rate,
        active_by_plan,
        active_total,
        traffic_series,
        traffic_by_server,
        funnel,
        telegram_ads_campaigns,
        telegram_ads_campaigns_total,
    ) = await asyncio.gather(
        _revenue_series(pool, trunc, tz, from_ts),
        _revenue_kpi(pool, from_ts),
        _count_series(pool, _NEW_USERS_SERIES, trunc, tz, from_ts),
        _scalar(pool, _NEW_USERS_KPI, from_ts),
        _count_series(pool, _TRIALS_SERIES, trunc, tz, from_ts),
        _scalar(pool, _TRIALS_KPI, from_ts),
        _trial_paid_rate(pool, from_ts),
        _active_by_plan(pool),
        _scalar(pool, _ACTIVE_TOTAL),  # запрос без параметров — текущий живой gauge
        _traffic_series(pool, from_date),
        _traffic_by_server(pool, from_date),
        _mature_funnel(pool, from_ts, now),
        _telegram_ads_campaigns(pool, from_ts, now),
        _scalar(pool, _TELEGRAM_ADS_CAMPAIGNS_TOTAL, from_ts, now),
    )

    traffic_total_gb = round(sum(p["gb"] for p in traffic_series), 2)
    traffic_users = max((p["users"] for p in traffic_series), default=0)

    return JSONResponse(
        {
            "ok": True,
            "period": {
                "key": period,
                "from": _iso(from_ts),
                "to": _iso(now),
                "bucket": trunc,
            },
            "kpi": {
                **revenue_kpi,
                "new_users": new_users_kpi,
                "trials_started": trials_kpi,
                "active_subscriptions": active_total,
                "trial_to_paid_rate": trial_paid_rate,
                "traffic_gb": traffic_total_gb,
                "traffic_users": traffic_users,
            },
            "series": {
                "revenue": revenue_series,
                "new_users": new_users_series,
                "trials": trials_series,
                "traffic": traffic_series,
            },
            "by_plan": active_by_plan,
            "funnel": funnel,
            "telegram_ads": {
                "campaigns": telegram_ads_campaigns,
                "total_campaigns": telegram_ads_campaigns_total,
                "truncated": telegram_ads_campaigns_total > _TELEGRAM_ADS_CAMPAIGNS_LIMIT,
            },
            "traffic_by_server": traffic_by_server,
        }
    )
