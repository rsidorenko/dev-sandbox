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
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.admin_auth import _with_admin_auth
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

_DEFAULT_PERIOD = "7d"
_DEFAULT_TZ = "Europe/Moscow"
_CUSTOM_PERIOD = "custom"
_MAX_CUSTOM_DAYS = 366
_MEANINGFUL_TRAFFIC_BYTES = 1_048_576

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


@dataclass(frozen=True, slots=True)
class _StatsWindow:
    period_key: str
    from_ts: datetime
    to_ts: datetime
    from_date: date
    to_date: date
    trunc: str


def _compute_window(
    period_key: str,
    tz_name: str,
    *,
    date_from_raw: str | None = None,
    date_to_raw: str | None = None,
    now: datetime | None = None,
) -> _StatsWindow:
    """Строит полуоткрытое UTC-окно из preset или включительных локальных дат."""
    zone = ZoneInfo(tz_name)
    now_utc = now or datetime.now(UTC)
    now_local = now_utc.astimezone(zone)

    if period_key == _CUSTOM_PERIOD:
        if not date_from_raw or not date_to_raw:
            raise ValueError("date_from and date_to are required for custom period")
        try:
            from_date = date.fromisoformat(date_from_raw)
            to_date = date.fromisoformat(date_to_raw)
        except ValueError as exc:
            raise ValueError("date_from and date_to must use YYYY-MM-DD") from exc
        if from_date > to_date:
            raise ValueError("date_from must not be later than date_to")
        if to_date > now_local.date():
            raise ValueError("date_to must not be in the future")
        span_days = (to_date - from_date).days + 1
        if span_days > _MAX_CUSTOM_DAYS:
            raise ValueError(f"custom period must not exceed {_MAX_CUSTOM_DAYS} days")

        from_local = datetime.combine(from_date, time.min, tzinfo=zone)
        to_local_exclusive = datetime.combine(to_date + timedelta(days=1), time.min, tzinfo=zone)
        to_ts = min(to_local_exclusive.astimezone(UTC), now_utc)
        if span_days == 1:
            trunc = "hour"
        elif span_days <= 90:
            trunc = "day"
        else:
            trunc = "month"
        return _StatsWindow(
            period_key=period_key,
            from_ts=from_local.astimezone(UTC),
            to_ts=to_ts,
            from_date=from_date,
            to_date=to_date,
            trunc=trunc,
        )

    days, trunc = _PERIODS[period_key]
    if days is None:
        from_ts = _ALL_FROM
    else:
        start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
        from_local = start_today if days == 1 else start_today - timedelta(days=days - 1)
        from_ts = from_local.astimezone(UTC)

    start_today = now_local.replace(hour=0, minute=0, second=0, microsecond=0)
    return _StatsWindow(
        period_key=period_key,
        from_ts=from_ts,
        to_ts=now_utc,
        from_date=from_ts.astimezone(zone).date(),
        to_date=start_today.date(),
        trunc=trunc,
    )


# Полные SQL как неявная конкатенация литералов (без '+'/интерполяции) — bandit S608 не срабатывает;
# значения подставляются по имени в fetch. Окна времени полуоткрытые: [from, to).

_REVENUE_SERIES = (
    "SELECT date_trunc($1, b.event_effective_at AT TIME ZONE $2) AS bucket, "
    "COALESCE(SUM(b.amount_minor_units), 0)::bigint AS kopecks, COUNT(*) AS n "
    "FROM billing_events_ledger b JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE b.status = 'accepted' AND b.event_effective_at >= $3 AND b.event_effective_at < $4 "
    "AND b.internal_fact_ref NOT LIKE 'grant:%' AND b.billing_provider_key <> 'manual_grant' "
    "GROUP BY bucket ORDER BY bucket"
)

_REVENUE_KPI = (
    "SELECT COALESCE(SUM(amount_minor_units), 0)::bigint AS kopecks, "
    "COUNT(*) AS payments, COUNT(DISTINCT b.internal_user_id) AS paying_users "
    "FROM billing_events_ledger b JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE b.status = 'accepted' AND b.event_effective_at >= $1 AND b.event_effective_at < $2 "
    "AND b.internal_fact_ref NOT LIKE 'grant:%' AND b.billing_provider_key <> 'manual_grant'"
)

_NEW_USERS_SERIES = (
    "SELECT date_trunc($1, i.created_at AT TIME ZONE $2) AS bucket, COUNT(*) AS n "
    "FROM user_identities i JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE i.created_at >= $3 AND i.created_at < $4 GROUP BY bucket ORDER BY bucket"
)

_NEW_USERS_KPI = (
    "SELECT COUNT(*) FROM user_identities i JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE i.created_at >= $1 AND i.created_at < $2"
)

_TRIALS_SERIES = (
    "SELECT date_trunc($1, trial_started_at AT TIME ZONE $2) AS bucket, COUNT(*) AS n "
    "FROM subscription_snapshots s JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE s.trial_started_at IS NOT NULL AND s.trial_started_at >= $3 AND s.trial_started_at < $4 "
    "GROUP BY bucket ORDER BY bucket"
)

_TRIALS_KPI = (
    "SELECT COUNT(*) FROM subscription_snapshots s JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE s.trial_started_at IS NOT NULL AND s.trial_started_at >= $1 AND s.trial_started_at < $2"
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
    "JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE t.trial_started_at IS NOT NULL AND t.trial_started_at >= $1 AND t.trial_started_at < $2"
)

_ACTIVE_BY_PLAN = (
    "SELECT plan_id, COUNT(*) AS n FROM subscription_snapshots "
    "JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE state_label = 'active' AND active_until_utc > now() AND plan_id IS NOT NULL "
    "GROUP BY plan_id ORDER BY n DESC"
)

# bare_xray-трафик (сбор с 2026-09-07): stat_date — DATE в MSK, поэтому группировка
# всегда по дню, без TZ-конверсий (в отличие от временных рядов выше). ГБ = up+down.
_TRAFFIC_SERIES = (
    "WITH eligible_emails AS ("
    "SELECT 'user-' || left(internal_user_id, 16) AS email FROM analytics_eligible_user_ids "
    "UNION SELECT 'x-user-' || left(internal_user_id, 16) FROM analytics_eligible_user_ids) "
    "SELECT t.stat_date AS bucket, "
    "ROUND(SUM(t.uplink_bytes + t.downlink_bytes) / 1073741824.0, 2) AS gb, "
    "COUNT(DISTINCT t.email) AS users "
    "FROM bare_xray_traffic_daily t JOIN eligible_emails e ON e.email = t.email "
    "WHERE t.stat_date >= $1::date AND t.stat_date <= $2::date "
    "GROUP BY stat_date ORDER BY stat_date"
)

_TRAFFIC_BY_SERVER = (
    "WITH eligible_emails AS ("
    "SELECT 'user-' || left(internal_user_id, 16) AS email FROM analytics_eligible_user_ids "
    "UNION SELECT 'x-user-' || left(internal_user_id, 16) FROM analytics_eligible_user_ids) "
    "SELECT COALESCE(v.label, 'server ' || t.server_id::text) AS label, "
    "ROUND(SUM(t.uplink_bytes + t.downlink_bytes) / 1073741824.0, 2) AS gb, "
    "COUNT(DISTINCT t.email) AS users "
    "FROM bare_xray_traffic_daily t JOIN eligible_emails e ON e.email = t.email "
    "LEFT JOIN vpn_servers v ON v.id = t.server_id "
    "WHERE t.stat_date >= $1::date AND t.stat_date <= $2::date "
    "GROUP BY t.server_id, label ORDER BY gb DESC"
)

_ACTIVE_TOTAL = (
    "SELECT COUNT(*) FROM subscription_snapshots s JOIN analytics_eligible_user_ids e USING (internal_user_id) "
    "WHERE s.state_label = 'active' AND s.active_until_utc > now()"
)

_PRODUCT_FUNNEL = """
WITH period_events AS (
    SELECT f.internal_user_id, f.event_name
    FROM funnel_events f
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE f.occurred_at >= $1 AND f.occurred_at < $2
)
SELECT
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'registered') AS registered_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'trial_started') AS trial_started_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'setup_started') AS setup_started_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'platform_selected') AS platform_selected_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'connection_confirmed') AS connection_confirmed_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'checkout_opened') AS checkout_opened_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'payment_created') AS payment_created_users,
    count(DISTINCT internal_user_id) FILTER (WHERE event_name = 'payment_succeeded') AS payment_succeeded_users
FROM period_events
"""

_FUNNEL_DIAGNOSTICS_V2 = """
WITH trials AS (
    SELECT s.internal_user_id, s.trial_started_at,
           (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date AS trial_date
    FROM subscription_snapshots s
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE s.trial_started_at IS NOT NULL
      AND s.trial_started_at >= $1 AND s.trial_started_at < $2
), daily AS (
    SELECT tr.internal_user_id, t.stat_date,
           t.stat_date - tr.trial_date AS day_offset,
           sum(t.uplink_bytes + t.downlink_bytes)::bigint AS bytes
    FROM trials tr
    JOIN bare_xray_traffic_daily t
      ON t.email IN ('user-' || left(tr.internal_user_id, 16),
                     'x-user-' || left(tr.internal_user_id, 16))
    WHERE t.stat_date >= greatest($3::date, tr.trial_date)
      AND t.stat_date <= $4::date
    GROUP BY tr.internal_user_id, t.stat_date, tr.trial_date
), running_traffic AS (
    SELECT d.*,
           sum(d.bytes) OVER (
               PARTITION BY d.internal_user_id ORDER BY d.stat_date
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           )::bigint AS running_bytes
    FROM daily d
), traffic AS (
    SELECT r.internal_user_id,
           COALESCE(sum(r.bytes), 0)::bigint AS total_bytes,
           min(r.stat_date) FILTER (WHERE r.bytes > 0) AS first_traffic_date,
           min(r.stat_date) FILTER (WHERE r.running_bytes >= $6) AS first_meaningful_date,
           bool_or(r.day_offset = 1 AND r.bytes >= $6) AS retained_d1,
           bool_or(r.day_offset = 2 AND r.bytes >= $6) AS retained_d2,
           bool_or(r.day_offset = 3 AND r.bytes >= $6) AS retained_d3
    FROM running_traffic r
    GROUP BY r.internal_user_id
), routes AS (
    SELECT tr.internal_user_id,
           min(f.occurred_at) FILTER (WHERE f.connection_route = 'direct') AS direct_at,
           min(f.occurred_at) FILTER (WHERE f.connection_route = 'guided') AS guided_at
    FROM trials tr
    LEFT JOIN funnel_interactions f
      ON f.internal_user_id = tr.internal_user_id
     AND f.connection_route <> ''
     AND f.occurred_at >= tr.trial_started_at
     AND f.occurred_at < $2
    GROUP BY tr.internal_user_id
), cohort AS (
    SELECT tr.*, tf.total_bytes, tf.first_traffic_date, tf.first_meaningful_date,
           COALESCE(tf.retained_d1, false) AS retained_d1,
           COALESCE(tf.retained_d2, false) AS retained_d2,
           COALESCE(tf.retained_d3, false) AS retained_d3,
           ro.direct_at, ro.guided_at
    FROM trials tr
    LEFT JOIN traffic tf USING (internal_user_id)
    LEFT JOIN routes ro USING (internal_user_id)
)
SELECT count(*)::int AS trials,
       count(*) FILTER (WHERE first_traffic_date IS NOT NULL)::int AS first_traffic_users,
       count(*) FILTER (WHERE first_meaningful_date IS NOT NULL)::int AS meaningful_traffic_users,
       count(*) FILTER (WHERE trial_date + 1 <= $5::date)::int AS d1_eligible_users,
       count(*) FILTER (WHERE trial_date + 1 <= $5::date AND retained_d1)::int AS d1_retained_users,
       count(*) FILTER (WHERE trial_date + 2 <= $5::date)::int AS d2_eligible_users,
       count(*) FILTER (WHERE trial_date + 2 <= $5::date AND retained_d2)::int AS d2_retained_users,
       count(*) FILTER (WHERE trial_date + 3 <= $5::date)::int AS d3_eligible_users,
       count(*) FILTER (WHERE trial_date + 3 <= $5::date AND retained_d3)::int AS d3_retained_users,
       count(*) FILTER (WHERE direct_at IS NOT NULL)::int AS direct_started_users,
       count(*) FILTER (WHERE guided_at IS NOT NULL)::int AS guided_started_users,
       count(*) FILTER (WHERE direct_at IS NOT NULL AND guided_at > direct_at)::int AS guided_after_direct_users,
       count(*) FILTER (
           WHERE first_meaningful_date IS NOT NULL AND direct_at IS NOT NULL
             AND (direct_at AT TIME ZONE 'Europe/Moscow')::date <= first_meaningful_date
             AND (guided_at IS NULL OR (guided_at AT TIME ZONE 'Europe/Moscow')::date > first_meaningful_date)
       )::int AS direct_meaningful_users,
       count(*) FILTER (
           WHERE first_meaningful_date IS NOT NULL AND guided_at IS NOT NULL
             AND (guided_at AT TIME ZONE 'Europe/Moscow')::date <= first_meaningful_date
       )::int AS guided_meaningful_users
FROM cohort
"""

_TRIAL_FEEDBACK_STATS = """
WITH base AS (
    SELECT f.internal_user_id, f.feedback_type, f.reason_code, f.platform, f.connection_route
    FROM trial_feedback f
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE f.updated_at >= $1 AND f.updated_at < $2
), breakdown AS (
    SELECT feedback_type, 'total'::text AS dimension, ''::text AS value,
           count(DISTINCT internal_user_id)::int AS users
    FROM base GROUP BY feedback_type
    UNION ALL
    SELECT feedback_type, 'reason', reason_code, count(DISTINCT internal_user_id)::int
    FROM base GROUP BY feedback_type, reason_code
    UNION ALL
    SELECT feedback_type, 'platform', platform, count(DISTINCT internal_user_id)::int
    FROM base WHERE platform <> '' GROUP BY feedback_type, platform
    UNION ALL
    SELECT feedback_type, 'route', connection_route, count(DISTINCT internal_user_id)::int
    FROM base WHERE connection_route <> '' GROUP BY feedback_type, connection_route
)
SELECT feedback_type, dimension, value, users
FROM breakdown
ORDER BY feedback_type, dimension, users DESC, value
"""

_EXPERIMENT_RESULTS = """
WITH assignments AS (
    SELECT a.internal_user_id, a.experiment_key, a.variant, a.assigned_at
    FROM experiment_assignments a
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE a.assigned_at >= $1 AND a.assigned_at < $2
), payment_created AS (
    SELECT DISTINCT a.internal_user_id, a.experiment_key
    FROM assignments a
    JOIN funnel_interactions f
      ON f.internal_user_id = a.internal_user_id
     AND f.event_name = 'payment_created'
     AND f.occurred_at >= a.assigned_at
     AND f.occurred_at < $2
), paid AS (
    SELECT a.internal_user_id, a.experiment_key,
           COALESCE(sum(b.amount_minor_units), 0)::bigint AS revenue_kopecks
    FROM assignments a
    JOIN billing_events_ledger b
      ON b.internal_user_id = a.internal_user_id
     AND b.status = 'accepted'
     AND b.event_type = 'subscription_activated'
     AND b.billing_provider_key IN ('yookassa', 'yookassa_v1', 'cryptobot', 'cryptobot_v1')
     AND b.internal_fact_ref NOT LIKE 'grant:%'
     AND b.amount_minor_units > 0
     AND b.currency_code = 'RUB'
     AND b.event_effective_at >= a.assigned_at
     AND b.event_effective_at < $2
    GROUP BY a.internal_user_id, a.experiment_key
), meaningful AS (
    -- Накопленный bare_xray-трафик с даты назначения ≥ порога: «дошёл до
    -- осмысленного использования VPN» (эквивалент running-sum ≥ порога из v2).
    SELECT DISTINCT a.internal_user_id, a.experiment_key
    FROM assignments a
    JOIN LATERAL (
        SELECT sum(t.uplink_bytes + t.downlink_bytes) AS total_bytes
        FROM bare_xray_traffic_daily t
        WHERE t.email IN ('user-' || left(a.internal_user_id, 16),
                          'x-user-' || left(a.internal_user_id, 16))
          AND t.stat_date >= (a.assigned_at AT TIME ZONE 'Europe/Moscow')::date
          AND t.stat_date <= $3::date
    ) traffic ON traffic.total_bytes >= $4
)
SELECT a.experiment_key, a.variant,
       count(*)::int AS exposures,
       count(pc.internal_user_id)::int AS payment_created_users,
       count(p.internal_user_id)::int AS paid_users,
       count(m.internal_user_id)::int AS meaningful_traffic_users,
       COALESCE(sum(p.revenue_kopecks), 0)::bigint AS revenue_kopecks
FROM assignments a
LEFT JOIN payment_created pc
  ON pc.internal_user_id = a.internal_user_id AND pc.experiment_key = a.experiment_key
LEFT JOIN paid p
  ON p.internal_user_id = a.internal_user_id AND p.experiment_key = a.experiment_key
LEFT JOIN meaningful m
  ON m.internal_user_id = a.internal_user_id AND m.experiment_key = a.experiment_key
GROUP BY a.experiment_key, a.variant
ORDER BY a.experiment_key, a.variant
"""

_MATURE_FUNNEL = """
WITH trials AS (
    SELECT t.internal_user_id, t.trial_started_at,
           t.trial_started_at + interval '10 days' AS deadline
    FROM subscription_snapshots t
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE t.trial_started_at >= $1 AND t.trial_started_at < $2
), cohort AS (
    SELECT t.*, (SELECT min(e.occurred_at) FROM funnel_events e
                 WHERE e.internal_user_id = t.internal_user_id
                   AND e.event_name = 'payment_succeeded'
                   AND e.occurred_at < $2) AS first_paid_at
    FROM trials t
)
SELECT count(*) AS trials,
       count(*) FILTER (WHERE deadline <= $2) AS mature_trials,
       count(*) FILTER (WHERE deadline > $2) AS pending_trials,
       count(*) FILTER (WHERE deadline <= $2 AND first_paid_at >= trial_started_at
                        AND first_paid_at < deadline) AS converted_trials
FROM cohort
"""

# ─── Data health: когда что начало собираться и где дыры в данных ─────────────

_DATA_HEALTH_SOURCES = """
SELECT 'funnel_events' AS source, event_name AS detail,
       count(*)::bigint AS rows, min(occurred_at) AS first_occurred_at
FROM funnel_events GROUP BY event_name
UNION ALL
SELECT 'funnel_interactions', event_name || ' / ' || surface,
       count(*)::bigint, min(occurred_at)
FROM funnel_interactions GROUP BY event_name, surface
UNION ALL
SELECT 'trial_feedback', feedback_type, count(*)::bigint, min(created_at)
FROM trial_feedback GROUP BY feedback_type
UNION ALL
SELECT 'connection_checks', 'check', count(*)::bigint, min(created_at)
FROM connection_checks
UNION ALL
SELECT 'telegram_ads_attributions', 'attribution', count(*)::bigint, min(attributed_at)
FROM telegram_ads_attributions
ORDER BY source, detail
"""

_DATA_HEALTH_ATTRIBUTION_SOURCES = """
SELECT a.campaign_key,
       count(DISTINCT a.internal_user_id)::int AS attributed_users,
       (a.campaign_key = 'nakrut') AS excluded_from_analytics
FROM telegram_ads_attributions a
WHERE a.attributed_at >= $1 AND a.attributed_at < $2
GROUP BY a.campaign_key
ORDER BY attributed_users DESC, a.campaign_key
LIMIT 20
"""

_DATA_HEALTH_CAMPAIGNS_MISSING_SPEND = """
WITH attr AS (
    SELECT a.campaign_key, count(DISTINCT a.internal_user_id)::int AS attributed_users
    FROM telegram_ads_attributions a
    WHERE a.campaign_key <> 'nakrut'
      AND a.attributed_at >= $1 AND a.attributed_at < $2
    GROUP BY a.campaign_key
)
SELECT a.campaign_key, a.attributed_users
FROM attr a
WHERE NOT EXISTS (
    SELECT 1 FROM telegram_ads_spend_daily s
    WHERE s.campaign_key = a.campaign_key
      AND s.spend_date >= $3 AND s.spend_date <= $4
)
ORDER BY a.attributed_users DESC, a.campaign_key
LIMIT 20
"""

# Несогласованные цепочки на когорте триалов периода (eligible): meaningful-трафик
# без маршрута, первый трафик раньше любого события подключения, «тихие» триалы.
_DATA_HEALTH_CHAIN_ANOMALIES = """
WITH trials AS (
    SELECT s.internal_user_id, s.trial_started_at,
           (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date AS trial_date
    FROM subscription_snapshots s
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE s.trial_started_at IS NOT NULL
      AND s.trial_started_at >= $1 AND s.trial_started_at < $2
), daily AS (
    SELECT tr.internal_user_id, t.stat_date,
           sum(t.uplink_bytes + t.downlink_bytes)::bigint AS bytes
    FROM trials tr
    JOIN bare_xray_traffic_daily t
      ON t.email IN ('user-' || left(tr.internal_user_id, 16),
                     'x-user-' || left(tr.internal_user_id, 16))
    WHERE t.stat_date >= tr.trial_date
    GROUP BY tr.internal_user_id, t.stat_date
), running_traffic AS (
    SELECT d.*,
           sum(d.bytes) OVER (
               PARTITION BY d.internal_user_id ORDER BY d.stat_date
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           )::bigint AS running_bytes
    FROM daily d
), traffic AS (
    SELECT r.internal_user_id,
           min(r.stat_date) FILTER (WHERE r.bytes > 0) AS first_traffic_date,
           min(r.stat_date) FILTER (WHERE r.running_bytes >= $3) AS first_meaningful_date
    FROM running_traffic r
    GROUP BY r.internal_user_id
), interactions AS (
    SELECT tr.internal_user_id,
           min(f.occurred_at) FILTER (WHERE f.event_name IN ('setup_started', 'platform_selected'))
               AS setup_at,
           bool_or(f.connection_route <> '') AS has_route
    FROM trials tr
    LEFT JOIN funnel_interactions f
      ON f.internal_user_id = tr.internal_user_id
     AND f.occurred_at >= tr.trial_started_at
     AND f.occurred_at < $2
    GROUP BY tr.internal_user_id
), cohort AS (
    SELECT tr.internal_user_id, tr.trial_date,
           tf.first_traffic_date, tf.first_meaningful_date,
           i.setup_at, i.has_route
    FROM trials tr
    LEFT JOIN traffic tf USING (internal_user_id)
    LEFT JOIN interactions i USING (internal_user_id)
)
SELECT count(*)::int AS trials,
       count(*) FILTER (WHERE first_meaningful_date IS NOT NULL AND NOT COALESCE(has_route, false))::int
           AS meaningful_without_route,
       count(*) FILTER (
           WHERE first_traffic_date IS NOT NULL AND setup_at IS NOT NULL
             AND first_traffic_date < (setup_at AT TIME ZONE 'Europe/Moscow')::date
       )::int AS traffic_before_setup,
       count(*) FILTER (WHERE setup_at IS NULL)::int AS trials_without_setup
FROM cohort
"""

# ─── Недельные когорты триалов: meaningful → D1/D2/D3 → checkout → оплата ≤10д ──

_TRIAL_COHORTS = """
WITH trials AS (
    SELECT s.internal_user_id, s.trial_started_at,
           date_trunc('week', s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date AS cohort_week,
           (s.trial_started_at AT TIME ZONE 'Europe/Moscow')::date AS trial_date
    FROM subscription_snapshots s
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE s.trial_started_at IS NOT NULL
      AND s.trial_started_at >= $1 AND s.trial_started_at < $2
), daily AS (
    SELECT tr.internal_user_id, t.stat_date,
           t.stat_date - tr.trial_date AS day_offset,
           sum(t.uplink_bytes + t.downlink_bytes)::bigint AS bytes
    FROM trials tr
    JOIN bare_xray_traffic_daily t
      ON t.email IN ('user-' || left(tr.internal_user_id, 16),
                     'x-user-' || left(tr.internal_user_id, 16))
    WHERE t.stat_date >= tr.trial_date
    GROUP BY tr.internal_user_id, t.stat_date, tr.trial_date
), running_traffic AS (
    SELECT d.*,
           sum(d.bytes) OVER (
               PARTITION BY d.internal_user_id ORDER BY d.stat_date
               ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
           )::bigint AS running_bytes
    FROM daily d
), traffic AS (
    SELECT r.internal_user_id,
           min(r.stat_date) FILTER (WHERE r.running_bytes >= $3) AS first_meaningful_date,
           bool_or(r.day_offset = 1 AND r.bytes >= $3) AS retained_d1,
           bool_or(r.day_offset = 2 AND r.bytes >= $3) AS retained_d2,
           bool_or(r.day_offset = 3 AND r.bytes >= $3) AS retained_d3
    FROM running_traffic r
    GROUP BY r.internal_user_id
), checkout AS (
    SELECT tr.internal_user_id, min(f.occurred_at) AS checkout_at
    FROM trials tr
    JOIN funnel_interactions f
      ON f.internal_user_id = tr.internal_user_id
     AND f.event_name = 'checkout_opened'
     AND f.occurred_at >= tr.trial_started_at
     AND f.occurred_at < $2
    GROUP BY tr.internal_user_id
), paid AS (
    SELECT tr.internal_user_id, min(e.occurred_at) AS paid_at
    FROM trials tr
    JOIN funnel_events e
      ON e.internal_user_id = tr.internal_user_id
     AND e.event_name = 'payment_succeeded'
     AND e.occurred_at >= tr.trial_started_at
     AND e.occurred_at < tr.trial_started_at + interval '10 days'
    GROUP BY tr.internal_user_id
), cohort AS (
    SELECT tr.cohort_week, tr.trial_date,
           tf.first_meaningful_date,
           COALESCE(tf.retained_d1, false) AS retained_d1,
           COALESCE(tf.retained_d2, false) AS retained_d2,
           COALESCE(tf.retained_d3, false) AS retained_d3,
           (c.checkout_at IS NOT NULL) AS opened_checkout,
           (p.paid_at IS NOT NULL) AS paid
    FROM trials tr
    LEFT JOIN traffic tf USING (internal_user_id)
    LEFT JOIN checkout c USING (internal_user_id)
    LEFT JOIN paid p USING (internal_user_id)
)
SELECT cohort_week::date AS cohort_week,
       count(*)::int AS trials,
       count(*) FILTER (WHERE first_meaningful_date IS NOT NULL)::int AS meaningful_users,
       count(*) FILTER (WHERE trial_date + 1 <= $4::date)::int AS d1_eligible_users,
       count(*) FILTER (WHERE trial_date + 1 <= $4::date AND retained_d1)::int AS d1_retained_users,
       count(*) FILTER (WHERE trial_date + 2 <= $4::date)::int AS d2_eligible_users,
       count(*) FILTER (WHERE trial_date + 2 <= $4::date AND retained_d2)::int AS d2_retained_users,
       count(*) FILTER (WHERE trial_date + 3 <= $4::date)::int AS d3_eligible_users,
       count(*) FILTER (WHERE trial_date + 3 <= $4::date AND retained_d3)::int AS d3_retained_users,
       count(*) FILTER (WHERE opened_checkout)::int AS checkout_users,
       count(*) FILTER (WHERE trial_date + 10 <= $4::date)::int AS mature_users,
       count(*) FILTER (WHERE trial_date + 10 <= $4::date AND paid)::int AS paid_users
FROM cohort
GROUP BY cohort_week
ORDER BY cohort_week
"""

# ─── Качество VPN по connection_checks ────────────────────────────────────────

_VPN_QUALITY_SERVERS = """
SELECT c.detected_server_label AS server_label,
       count(*)::int AS checks,
       count(DISTINCT c.internal_user_id)::int AS users,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY c.latency_ms)::int AS latency_p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY c.latency_ms)::int AS latency_p95_ms,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY c.throughput_kbps)::int AS throughput_p50_kbps
FROM connection_checks c
JOIN analytics_eligible_user_ids e USING (internal_user_id)
WHERE c.connected AND c.created_at >= $1 AND c.created_at < $2
  AND c.detected_server_label <> ''
GROUP BY c.detected_server_label
ORDER BY checks DESC, c.detected_server_label
"""

_VPN_QUALITY_OVERALL = """
SELECT count(*)::int AS checks,
       count(*) FILTER (WHERE connected)::int AS connected_checks,
       count(DISTINCT internal_user_id) FILTER (WHERE connected)::int AS connected_users,
       percentile_cont(0.5) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE connected)::int
           AS latency_p50_ms,
       percentile_cont(0.95) WITHIN GROUP (ORDER BY latency_ms) FILTER (WHERE connected)::int
           AS latency_p95_ms
FROM connection_checks c
JOIN analytics_eligible_user_ids e USING (internal_user_id)
WHERE c.created_at >= $1 AND c.created_at < $2
"""

_VPN_QUALITY_WEB_VERDICTS = """
SELECT f.event_name,
       count(DISTINCT f.internal_user_id)::int AS users
FROM funnel_interactions f
JOIN analytics_eligible_user_ids e USING (internal_user_id)
WHERE f.surface = 'web'
  AND f.event_name IN ('connection_confirmed', 'connection_problem')
  AND f.occurred_at >= $1 AND f.occurred_at < $2
GROUP BY f.event_name
"""

_VPN_QUALITY_WEB_REASONS = """
SELECT t.reason_code, count(DISTINCT t.internal_user_id)::int AS users
FROM trial_feedback t
JOIN analytics_eligible_user_ids e USING (internal_user_id)
WHERE t.feedback_type = 'connection_problem'
  AND t.connection_route = ''
  AND t.updated_at >= $1 AND t.updated_at < $2
GROUP BY t.reason_code
ORDER BY users DESC, t.reason_code
"""

# Эффект nudge-уведомлений: delivered vs holdout (контрольная группа без отправки),
# оплата в течение 7 дней после даты уведомления.
_NOTIFICATION_HOLDOUT_STATS = """
WITH notes AS (
    SELECT n.internal_user_id, n.notification_type, n.delivery_status, n.sent_date
    FROM notification_log n
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE n.delivery_status IN ('delivered', 'holdout')
      AND n.sent_at >= $1 AND n.sent_at < $2
)
SELECT n.notification_type,
       n.delivery_status,
       count(DISTINCT n.internal_user_id)::int AS users,
       count(DISTINCT b.internal_user_id)::int AS paid_users
FROM notes n
LEFT JOIN billing_events_ledger b
  ON b.internal_user_id = n.internal_user_id
 AND b.status = 'accepted'
 AND b.event_type = 'subscription_activated'
 AND b.billing_provider_key IN ('yookassa', 'yookassa_v1', 'cryptobot', 'cryptobot_v1')
 AND b.internal_fact_ref NOT LIKE 'grant:%'
 AND b.amount_minor_units > 0
 AND b.currency_code = 'RUB'
 AND b.event_effective_at >= n.sent_date
 AND b.event_effective_at < n.sent_date + 8
GROUP BY n.notification_type, n.delivery_status
ORDER BY n.notification_type, n.delivery_status
"""

_TELEGRAM_ADS_CAMPAIGNS_LIMIT = 200
_TELEGRAM_ADS_CAMPAIGNS = """
WITH attributed AS (
    SELECT a.campaign_key, a.internal_user_id, a.attributed_at
    FROM telegram_ads_attributions a
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE a.campaign_key <> 'nakrut' AND a.attributed_at >= $1 AND a.attributed_at < $2
), external_payments AS (
    SELECT b.internal_user_id, b.event_effective_at AS occurred_at,
           b.internal_fact_ref AS event_key, b.amount_minor_units::bigint AS amount_kopecks
    FROM billing_events_ledger b
    JOIN attributed a USING (internal_user_id)
    WHERE b.status = 'accepted' AND b.event_type = 'subscription_activated'
      AND b.billing_provider_key IN ('yookassa', 'yookassa_v1', 'cryptobot', 'cryptobot_v1')
      AND b.internal_fact_ref NOT LIKE 'grant:%'
      AND b.amount_minor_units > 0 AND b.currency_code = 'RUB'
      AND b.event_effective_at < $2
), payment_ranked AS (
    SELECT p.*,
           row_number() OVER (
               PARTITION BY p.internal_user_id ORDER BY p.occurred_at, p.event_key
           ) AS payment_number
    FROM external_payments p
), payment_moments AS (
    SELECT internal_user_id,
           min(occurred_at) FILTER (WHERE payment_number = 1) AS first_paid_at,
           min(occurred_at) FILTER (WHERE payment_number = 2) AS second_paid_at
    FROM payment_ranked
    GROUP BY internal_user_id
), base AS (
    SELECT a.campaign_key, a.internal_user_id, a.attributed_at,
           CASE WHEN s.trial_started_at >= a.attributed_at AND s.trial_started_at < $2
                THEN s.trial_started_at END AS trial_started_at,
           p.first_paid_at, p.second_paid_at
    FROM attributed a
    LEFT JOIN subscription_snapshots s USING (internal_user_id)
    LEFT JOIN payment_moments p USING (internal_user_id)
), cohort AS (
    SELECT b.*, b.trial_started_at + interval '10 days' AS deadline,
           b.trial_started_at IS NOT NULL
           AND b.first_paid_at >= b.trial_started_at
           AND b.first_paid_at < b.trial_started_at + interval '10 days' AS acquired
    FROM base b
), cohort_with_revenue AS (
    SELECT c.*,
           CASE WHEN c.acquired THEN COALESCE((
               SELECT sum(p.amount_kopecks) FROM external_payments p
               WHERE p.internal_user_id = c.internal_user_id
                 AND p.occurred_at >= c.first_paid_at
           ), 0)::bigint ELSE 0::bigint END AS revenue_kopecks
    FROM cohort c
), cohort_with_signals AS (
    SELECT c.*,
           EXISTS (SELECT 1 FROM funnel_events e
                   WHERE e.internal_user_id = c.internal_user_id
                     AND c.trial_started_at IS NOT NULL
                     AND e.event_name = 'setup_started'
                     AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS setup_started,
           EXISTS (SELECT 1 FROM funnel_events e
                   WHERE e.internal_user_id = c.internal_user_id
                     AND c.trial_started_at IS NOT NULL
                     AND e.event_name = 'platform_selected'
                     AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS platform_selected,
           EXISTS (SELECT 1 FROM funnel_events e
                   WHERE e.internal_user_id = c.internal_user_id
                     AND c.trial_started_at IS NOT NULL
                     AND e.event_name = 'connection_confirmed'
                     AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS connection_confirmed,
           EXISTS (SELECT 1 FROM funnel_events e
                   WHERE e.internal_user_id = c.internal_user_id
                     AND c.trial_started_at IS NOT NULL
                     AND e.event_name = 'connection_problem'
                     AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS connection_problem,
           EXISTS (SELECT 1 FROM funnel_events e
                   WHERE e.internal_user_id = c.internal_user_id
                     AND c.trial_started_at IS NOT NULL
                     AND e.event_name = 'checkout_opened'
                     AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS checkout_opened,
           EXISTS (SELECT 1 FROM funnel_events e
                   WHERE e.internal_user_id = c.internal_user_id
                     AND c.trial_started_at IS NOT NULL
                     AND e.event_name = 'payment_created'
                     AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS payment_created,
           EXISTS (SELECT 1 FROM bare_xray_traffic_daily t
                   WHERE c.trial_started_at IS NOT NULL
                     AND t.email IN ('user-' || left(c.internal_user_id, 16),
                                     'x-user-' || left(c.internal_user_id, 16))
                     AND t.stat_date >= (c.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
                     AND t.stat_date >= $3 AND t.stat_date <= $4
                     AND t.uplink_bytes + t.downlink_bytes > 0) AS first_vpn_traffic,
           COALESCE((SELECT sum(t.uplink_bytes + t.downlink_bytes)
                     FROM bare_xray_traffic_daily t
                     WHERE c.trial_started_at IS NOT NULL
                       AND t.email IN ('user-' || left(c.internal_user_id, 16),
                                       'x-user-' || left(c.internal_user_id, 16))
                       AND t.stat_date >= (c.trial_started_at AT TIME ZONE 'Europe/Moscow')::date
                       AND t.stat_date >= $3 AND t.stat_date <= $4), 0) >= $5
               AS meaningful_vpn_traffic,
           EXISTS (SELECT 1 FROM funnel_events e WHERE e.internal_user_id = c.internal_user_id
                   AND c.trial_started_at IS NOT NULL AND e.event_name = 'platform_selected'
                   AND e.platform = 'ios' AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS platform_ios,
           EXISTS (SELECT 1 FROM funnel_events e WHERE e.internal_user_id = c.internal_user_id
                   AND c.trial_started_at IS NOT NULL AND e.event_name = 'platform_selected'
                   AND e.platform = 'android' AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS platform_android,
           EXISTS (SELECT 1 FROM funnel_events e WHERE e.internal_user_id = c.internal_user_id
                   AND c.trial_started_at IS NOT NULL AND e.event_name = 'platform_selected'
                   AND e.platform = 'mac' AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS platform_mac,
           EXISTS (SELECT 1 FROM funnel_events e WHERE e.internal_user_id = c.internal_user_id
                   AND c.trial_started_at IS NOT NULL AND e.event_name = 'platform_selected'
                   AND e.platform = 'win' AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS platform_win,
           EXISTS (SELECT 1 FROM funnel_events e WHERE e.internal_user_id = c.internal_user_id
                   AND c.trial_started_at IS NOT NULL AND e.event_name = 'platform_selected'
                   AND e.platform = 'tv' AND e.occurred_at >= c.trial_started_at AND e.occurred_at < $2) AS platform_tv
    FROM cohort_with_revenue c
), spend AS (
    SELECT campaign_key, sum(amount_kopecks)::bigint AS spend_kopecks
    FROM telegram_ads_spend_daily
    WHERE campaign_key <> 'nakrut' AND spend_date >= $3 AND spend_date <= $4
    GROUP BY campaign_key
), campaign_keys AS (
    SELECT campaign_key FROM attributed
    UNION
    SELECT campaign_key FROM spend
), campaign_stats AS (
SELECT k.campaign_key,
       count(c.internal_user_id) AS attributed_users,
       count(c.internal_user_id) FILTER (WHERE c.trial_started_at IS NOT NULL) AS trials_started,
       count(c.internal_user_id) FILTER (WHERE c.setup_started) AS setup_started_users,
       count(c.internal_user_id) FILTER (WHERE c.platform_selected) AS platform_selected_users,
       count(c.internal_user_id) FILTER (WHERE c.first_vpn_traffic) AS first_vpn_traffic_users,
       count(c.internal_user_id) FILTER (WHERE c.meaningful_vpn_traffic) AS meaningful_vpn_traffic_users,
       count(c.internal_user_id) FILTER (WHERE c.connection_confirmed) AS connection_confirmed_users,
       count(c.internal_user_id) FILTER (WHERE c.connection_problem) AS connection_problem_users,
       count(c.internal_user_id) FILTER (WHERE c.checkout_opened) AS checkout_opened_users,
       count(c.internal_user_id) FILTER (WHERE c.payment_created) AS payment_created_users,
       count(c.internal_user_id) FILTER (WHERE c.platform_ios) AS platform_ios_users,
       count(c.internal_user_id) FILTER (WHERE c.platform_android) AS platform_android_users,
       count(c.internal_user_id) FILTER (WHERE c.platform_mac) AS platform_mac_users,
       count(c.internal_user_id) FILTER (WHERE c.platform_win) AS platform_win_users,
       count(c.internal_user_id) FILTER (WHERE c.platform_tv) AS platform_tv_users,
       count(c.internal_user_id) FILTER (WHERE c.deadline <= $2) AS mature_trials,
       count(c.internal_user_id) FILTER (WHERE c.deadline > $2) AS pending_trials,
       count(c.internal_user_id) FILTER (WHERE c.deadline <= $2 AND c.acquired) AS first_payment_users,
       count(c.internal_user_id) FILTER (
           WHERE c.deadline <= $2 AND c.acquired AND c.second_paid_at IS NOT NULL
       ) AS second_payment_users,
       count(c.internal_user_id) FILTER (WHERE c.acquired) AS acquired_users,
       COALESCE(sum(c.revenue_kopecks), 0)::bigint AS revenue_kopecks,
       COALESCE(max(s.spend_kopecks), 0)::bigint AS spend_kopecks
FROM campaign_keys k
LEFT JOIN cohort_with_signals c ON c.campaign_key = k.campaign_key
LEFT JOIN spend s ON s.campaign_key = k.campaign_key
GROUP BY k.campaign_key
)
SELECT campaign_stats.*,
       sum(spend_kopecks) OVER ()::bigint AS total_spend_kopecks,
       sum(revenue_kopecks) OVER ()::bigint AS total_revenue_kopecks,
       sum(attributed_users) OVER ()::bigint AS total_attributed_users,
       sum(trials_started) OVER ()::bigint AS total_trials_started,
       sum(setup_started_users) OVER ()::bigint AS total_setup_started_users,
       sum(platform_selected_users) OVER ()::bigint AS total_platform_selected_users,
       sum(first_vpn_traffic_users) OVER ()::bigint AS total_first_vpn_traffic_users,
       sum(meaningful_vpn_traffic_users) OVER ()::bigint AS total_meaningful_vpn_traffic_users,
       sum(connection_confirmed_users) OVER ()::bigint AS total_connection_confirmed_users,
       sum(connection_problem_users) OVER ()::bigint AS total_connection_problem_users,
       sum(checkout_opened_users) OVER ()::bigint AS total_checkout_opened_users,
       sum(payment_created_users) OVER ()::bigint AS total_payment_created_users,
       sum(mature_trials) OVER ()::bigint AS total_mature_trials,
       sum(pending_trials) OVER ()::bigint AS total_pending_trials,
       sum(first_payment_users) OVER ()::bigint AS total_first_payment_users,
       sum(second_payment_users) OVER ()::bigint AS total_second_payment_users,
       sum(platform_ios_users) OVER ()::bigint AS total_platform_ios_users,
       sum(platform_android_users) OVER ()::bigint AS total_platform_android_users,
       sum(platform_mac_users) OVER ()::bigint AS total_platform_mac_users,
       sum(platform_win_users) OVER ()::bigint AS total_platform_win_users,
       sum(platform_tv_users) OVER ()::bigint AS total_platform_tv_users,
       sum(acquired_users) OVER ()::bigint AS total_acquired_users
FROM campaign_stats
ORDER BY spend_kopecks DESC, attributed_users DESC, campaign_key
LIMIT 200
"""

_TELEGRAM_ADS_CAMPAIGNS_TOTAL = """
WITH campaign_keys AS (
    SELECT a.campaign_key FROM telegram_ads_attributions a
    JOIN analytics_eligible_user_ids e USING (internal_user_id)
    WHERE a.campaign_key <> 'nakrut' AND a.attributed_at >= $1 AND a.attributed_at < $2
    UNION
    SELECT campaign_key FROM telegram_ads_spend_daily
    WHERE campaign_key <> 'nakrut' AND spend_date >= $3 AND spend_date <= $4
)
SELECT count(*) FROM campaign_keys
"""


async def _mature_funnel(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> dict:
    row = await pool.fetchrow(_MATURE_FUNNEL, from_ts, to_ts)
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


async def _telegram_ads_campaigns(
    pool: asyncpg.Pool,
    from_ts: datetime,
    to_ts: datetime,
    from_date: date,
    to_date: date,
) -> dict:
    rows = await pool.fetch(
        _TELEGRAM_ADS_CAMPAIGNS,
        from_ts,
        to_ts,
        from_date,
        to_date,
        _MEANINGFUL_TRAFFIC_BYTES,
    )
    result = []
    for row in rows:
        attributed = int(row["attributed_users"])
        trials = int(row["trials_started"])
        mature = int(row["mature_trials"])
        first_payment = int(row["first_payment_users"])
        acquired = int(row["acquired_users"])
        spend_kopecks = int(row["spend_kopecks"])
        revenue_kopecks = int(row["revenue_kopecks"])
        result.append(
            {
                "campaign_key": row["campaign_key"],
                "attributed_users": attributed,
                "trials_started": trials,
                "setup_started_users": int(row["setup_started_users"]),
                "platform_selected_users": int(row["platform_selected_users"]),
                "first_vpn_traffic_users": int(row["first_vpn_traffic_users"]),
                "meaningful_vpn_traffic_users": int(row["meaningful_vpn_traffic_users"]),
                "connection_confirmed_users": int(row["connection_confirmed_users"]),
                "connection_problem_users": int(row["connection_problem_users"]),
                "checkout_opened_users": int(row["checkout_opened_users"]),
                "payment_created_users": int(row["payment_created_users"]),
                "mature_trials": mature,
                "pending_trials": int(row["pending_trials"]),
                "first_payment_users": first_payment,
                "second_payment_users": int(row["second_payment_users"]),
                "acquired_users": acquired,
                "spend_rubles": round(spend_kopecks / 100, 2),
                "revenue_rubles": round(revenue_kopecks / 100, 2),
                "cost_per_attributed_user_rubles": (round(spend_kopecks / 100 / attributed, 2) if attributed else None),
                "cost_per_trial_rubles": round(spend_kopecks / 100 / trials, 2) if trials else None,
                "cac_rubles": round(spend_kopecks / 100 / acquired, 2) if acquired else None,
                "roas": round(revenue_kopecks / spend_kopecks, 4) if spend_kopecks else None,
                "trial_activation_rate": round(trials / attributed, 4) if attributed else None,
                "trial_to_first_payment_rate": round(first_payment / mature, 4) if mature else None,
                "platform_users": {
                    "ios": int(row["platform_ios_users"]),
                    "android": int(row["platform_android_users"]),
                    "mac": int(row["platform_mac_users"]),
                    "win": int(row["platform_win_users"]),
                    "tv": int(row["platform_tv_users"]),
                },
            }
        )
    if not rows:
        economics = _telegram_ads_economics([])
    else:
        total_spend = int(rows[0]["total_spend_kopecks"]) / 100
        total_revenue = int(rows[0]["total_revenue_kopecks"]) / 100
        economics = _telegram_ads_economics(
            [
                {
                    "spend_rubles": total_spend,
                    "revenue_rubles": total_revenue,
                    "attributed_users": int(rows[0]["total_attributed_users"]),
                    "trials_started": int(rows[0]["total_trials_started"]),
                    "acquired_users": int(rows[0]["total_acquired_users"]),
                }
            ]
        )
    return {
        "campaigns": result,
        "economics": economics,
        "funnel": _telegram_ads_funnel(rows[0] if rows else None),
    }


def _telegram_ads_funnel(totals: Mapping[str, object] | None) -> dict:
    def value(name: str) -> int:
        if totals is None:
            return 0
        return int(totals[f"total_{name}"])

    return {
        "attributed_users": value("attributed_users"),
        "trials_started": value("trials_started"),
        "setup_started_users": value("setup_started_users"),
        "platform_selected_users": value("platform_selected_users"),
        "first_vpn_traffic_users": value("first_vpn_traffic_users"),
        "meaningful_vpn_traffic_users": value("meaningful_vpn_traffic_users"),
        "connection_confirmed_users": value("connection_confirmed_users"),
        "connection_problem_users": value("connection_problem_users"),
        "checkout_opened_users": value("checkout_opened_users"),
        "payment_created_users": value("payment_created_users"),
        "mature_trials": value("mature_trials"),
        "pending_trials": value("pending_trials"),
        "first_payment_users": value("first_payment_users"),
        "second_payment_users": value("second_payment_users"),
        "acquired_users": value("acquired_users"),
        "platform_users": {
            "ios": value("platform_ios_users"),
            "android": value("platform_android_users"),
            "mac": value("platform_mac_users"),
            "win": value("platform_win_users"),
            "tv": value("platform_tv_users"),
        },
    }


def _telegram_ads_economics(campaigns: list[dict]) -> dict:
    spend_rubles = round(sum(float(row["spend_rubles"]) for row in campaigns), 2)
    revenue_rubles = round(sum(float(row["revenue_rubles"]) for row in campaigns), 2)
    attributed = sum(int(row["attributed_users"]) for row in campaigns)
    trials = sum(int(row["trials_started"]) for row in campaigns)
    acquired = sum(int(row["acquired_users"]) for row in campaigns)
    return {
        "spend_rubles": spend_rubles,
        "revenue_rubles": revenue_rubles,
        "attributed_users": attributed,
        "trials_started": trials,
        "acquired_users": acquired,
        "cost_per_attributed_user_rubles": round(spend_rubles / attributed, 2) if attributed else None,
        "cost_per_trial_rubles": round(spend_rubles / trials, 2) if trials else None,
        "cac_rubles": round(spend_rubles / acquired, 2) if acquired else None,
        "roas": round(revenue_rubles / spend_rubles, 4) if spend_rubles else None,
    }


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


async def _revenue_series(
    pool: asyncpg.Pool,
    trunc: str,
    tz: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[dict]:
    rows = await pool.fetch(_REVENUE_SERIES, trunc, tz, from_ts, to_ts)
    return [
        {"bucket": _iso(r["bucket"]), "rubles": round(int(r["kopecks"]) / 100, 2), "count": int(r["n"])} for r in rows
    ]


async def _revenue_kpi(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> dict:
    row = await pool.fetchrow(_REVENUE_KPI, from_ts, to_ts)
    return {
        "revenue_rubles": round(int(row["kopecks"]) / 100, 2),
        "payments_count": int(row["payments"]),
        "paying_users": int(row["paying_users"]),
    }


async def _count_series(
    pool: asyncpg.Pool,
    sql: str,
    trunc: str,
    tz: str,
    from_ts: datetime,
    to_ts: datetime,
) -> list[dict]:
    rows = await pool.fetch(sql, trunc, tz, from_ts, to_ts)
    return [{"bucket": _iso(r["bucket"]), "count": int(r["n"])} for r in rows]


async def _scalar(pool: asyncpg.Pool, sql: str, *args) -> int:
    """Скалярный COUNT(*) — args передаются как $N ровно столько, сколько плейсхолдеров в SQL."""
    return int(await pool.fetchval(sql, *args) or 0)


async def _trial_paid_rate(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> float | None:
    row = await pool.fetchrow(_TRIAL_PAID, from_ts, to_ts)
    trials = int(row["trials"] or 0)
    if trials == 0:
        return None
    return round(int(row["converted"] or 0) / trials, 4)


async def _active_by_plan(pool: asyncpg.Pool) -> list[dict]:
    rows = await pool.fetch(_ACTIVE_BY_PLAN)
    return [{"plan_id": r["plan_id"], "count": int(r["n"])} for r in rows]


async def _traffic_series(pool: asyncpg.Pool, from_date: date, to_date: date) -> list[dict]:
    rows = await pool.fetch(_TRAFFIC_SERIES, from_date, to_date)
    return [{"bucket": r["bucket"].isoformat(), "gb": float(r["gb"]), "users": int(r["users"])} for r in rows]


async def _traffic_by_server(pool: asyncpg.Pool, from_date: date, to_date: date) -> list[dict]:
    rows = await pool.fetch(_TRAFFIC_BY_SERVER, from_date, to_date)
    return [{"label": r["label"], "gb": float(r["gb"]), "users": int(r["users"])} for r in rows]


async def _product_funnel(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> dict[str, int]:
    row = await pool.fetchrow(_PRODUCT_FUNNEL, from_ts, to_ts)
    return {key: int(row[key] or 0) for key in row.keys()}


def _completed_through_date(window: _StatsWindow, tz: str) -> date:
    """Последний полностью завершённый локальный день для retention-знаменателя."""
    end_local_date = window.to_ts.astimezone(ZoneInfo(tz)).date()
    if end_local_date > window.to_date:
        return window.to_date
    return window.to_date - timedelta(days=1)


async def _funnel_diagnostics_v2(
    pool: asyncpg.Pool,
    window: _StatsWindow,
    completed_through: date,
) -> dict:
    row = await pool.fetchrow(
        _FUNNEL_DIAGNOSTICS_V2,
        window.from_ts,
        window.to_ts,
        window.from_date,
        window.to_date,
        completed_through,
        _MEANINGFUL_TRAFFIC_BYTES,
    )

    def count(name: str) -> int:
        return int(row[name] or 0)

    def rate(numerator: int, denominator: int) -> float | None:
        return round(numerator / denominator, 4) if denominator else None

    trials = count("trials")
    meaningful = count("meaningful_traffic_users")
    retention = {}
    for day in (1, 2, 3):
        eligible = count(f"d{day}_eligible_users")
        retained = count(f"d{day}_retained_users")
        retention[f"d{day}"] = {
            "eligible_users": eligible,
            "retained_users": retained,
            "rate": rate(retained, eligible),
        }

    direct_started = count("direct_started_users")
    guided_started = count("guided_started_users")
    direct_meaningful = count("direct_meaningful_users")
    guided_meaningful = count("guided_meaningful_users")
    return {
        "meaningful_traffic_threshold_bytes": _MEANINGFUL_TRAFFIC_BYTES,
        "trials": trials,
        "first_traffic_users": count("first_traffic_users"),
        "meaningful_traffic_users": meaningful,
        "trial_to_meaningful_traffic_rate": rate(meaningful, trials),
        "retention": retention,
        "routes": {
            "direct": {
                "started_users": direct_started,
                "meaningful_traffic_users": direct_meaningful,
                "meaningful_traffic_rate": rate(direct_meaningful, direct_started),
            },
            "guided": {
                "started_users": guided_started,
                "meaningful_traffic_users": guided_meaningful,
                "meaningful_traffic_rate": rate(guided_meaningful, guided_started),
            },
            "guided_after_direct_users": count("guided_after_direct_users"),
        },
    }


async def _trial_feedback_stats(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> dict:
    rows = await pool.fetch(_TRIAL_FEEDBACK_STATS, from_ts, to_ts)

    def empty_bucket() -> dict:
        return {"respondents": 0, "reasons": [], "platforms": [], "routes": []}

    result = {
        "connection_problem": empty_bucket(),
        "trial_nonpayment": empty_bucket(),
    }
    dimension_keys = {"reason": "reasons", "platform": "platforms", "route": "routes"}
    for row in rows:
        feedback_type = str(row["feedback_type"])
        if feedback_type not in result:
            continue
        dimension = str(row["dimension"])
        users = int(row["users"] or 0)
        if dimension == "total":
            result[feedback_type]["respondents"] = users
            continue
        target = dimension_keys.get(dimension)
        if target is not None:
            result[feedback_type][target].append({"code": str(row["value"]), "users": users})
    return result


async def _experiment_results(
    pool: asyncpg.Pool,
    from_ts: datetime,
    to_ts: datetime,
    to_date: date,
) -> list[dict]:
    rows = await pool.fetch(
        _EXPERIMENT_RESULTS,
        from_ts,
        to_ts,
        to_date,
        _MEANINGFUL_TRAFFIC_BYTES,
    )
    results: list[dict] = []
    for row in rows:
        exposures = int(row["exposures"] or 0)
        created = int(row["payment_created_users"] or 0)
        paid = int(row["paid_users"] or 0)
        meaningful = int(row["meaningful_traffic_users"] or 0)
        results.append(
            {
                "experiment_key": row["experiment_key"],
                "variant": row["variant"],
                "exposures": exposures,
                "payment_created_users": created,
                "paid_users": paid,
                "meaningful_traffic_users": meaningful,
                "revenue_rubles": round(int(row["revenue_kopecks"] or 0) / 100, 2),
                "checkout_rate": round(created / exposures, 4) if exposures else None,
                "paid_rate": round(paid / exposures, 4) if exposures else None,
                "meaningful_traffic_rate": round(meaningful / exposures, 4) if exposures else None,
            }
        )
    return results


async def _data_health(
    pool: asyncpg.Pool,
    from_ts: datetime,
    to_ts: datetime,
    from_date: date,
    to_date: date,
) -> dict:
    """Контекст достоверности остальных блоков: даты старта сбора, аномальные
    источники, кампании без расходов, несогласованные цепочки."""
    sources_rows, attribution_rows, missing_spend_rows, anomaly_row = await asyncio.gather(
        pool.fetch(_DATA_HEALTH_SOURCES),
        pool.fetch(_DATA_HEALTH_ATTRIBUTION_SOURCES, from_ts, to_ts),
        pool.fetch(_DATA_HEALTH_CAMPAIGNS_MISSING_SPEND, from_ts, to_ts, from_date, to_date),
        pool.fetchrow(_DATA_HEALTH_CHAIN_ANOMALIES, from_ts, to_ts, _MEANINGFUL_TRAFFIC_BYTES),
    )
    sources = [
        {
            "source": str(row["source"]),
            "detail": str(row["detail"]),
            "rows": int(row["rows"] or 0),
            "first_occurred_at": _iso(row["first_occurred_at"]),
        }
        for row in sources_rows
    ]
    attribution_sources = [
        {
            "campaign_key": str(row["campaign_key"]),
            "attributed_users": int(row["attributed_users"] or 0),
            "excluded_from_analytics": bool(row["excluded_from_analytics"]),
        }
        for row in attribution_rows
    ]
    campaigns_missing_spend = [
        {
            "campaign_key": str(row["campaign_key"]),
            "attributed_users": int(row["attributed_users"] or 0),
        }
        for row in missing_spend_rows
    ]
    return {
        "sources": sources,
        "attribution_sources": attribution_sources,
        "campaigns_missing_spend": campaigns_missing_spend,
        "chain_anomalies": {
            "trials": int(anomaly_row["trials"] or 0),
            "meaningful_without_route": int(anomaly_row["meaningful_without_route"] or 0),
            "traffic_before_setup": int(anomaly_row["traffic_before_setup"] or 0),
            "trials_without_setup": int(anomaly_row["trials_without_setup"] or 0),
        },
    }


async def _trial_cohorts(
    pool: asyncpg.Pool,
    window: _StatsWindow,
    completed_through: date,
) -> list[dict]:
    rows = await pool.fetch(
        _TRIAL_COHORTS,
        window.from_ts,
        window.to_ts,
        _MEANINGFUL_TRAFFIC_BYTES,
        completed_through,
    )
    cohorts: list[dict] = []
    for row in rows:
        trials = int(row["trials"] or 0)
        meaningful = int(row["meaningful_users"] or 0)
        d1_eligible = int(row["d1_eligible_users"] or 0)
        d2_eligible = int(row["d2_eligible_users"] or 0)
        d3_eligible = int(row["d3_eligible_users"] or 0)
        mature = int(row["mature_users"] or 0)
        cohorts.append(
            {
                "cohort_week": row["cohort_week"].isoformat(),
                "trials": trials,
                "meaningful_users": meaningful,
                "meaningful_rate": round(meaningful / trials, 4) if trials else None,
                "d1": {
                    "eligible_users": d1_eligible,
                    "retained_users": int(row["d1_retained_users"] or 0),
                    "rate": (round(int(row["d1_retained_users"] or 0) / d1_eligible, 4) if d1_eligible else None),
                },
                "d2": {
                    "eligible_users": d2_eligible,
                    "retained_users": int(row["d2_retained_users"] or 0),
                    "rate": (round(int(row["d2_retained_users"] or 0) / d2_eligible, 4) if d2_eligible else None),
                },
                "d3": {
                    "eligible_users": d3_eligible,
                    "retained_users": int(row["d3_retained_users"] or 0),
                    "rate": (round(int(row["d3_retained_users"] or 0) / d3_eligible, 4) if d3_eligible else None),
                },
                "checkout_users": int(row["checkout_users"] or 0),
                "mature_users": mature,
                "paid_users": int(row["paid_users"] or 0),
                "paid_rate": (round(int(row["paid_users"] or 0) / mature, 4) if mature else None),
            }
        )
    return cohorts


async def _vpn_quality(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> dict:
    server_rows, overall_row, verdict_rows, reason_rows = await asyncio.gather(
        pool.fetch(_VPN_QUALITY_SERVERS, from_ts, to_ts),
        pool.fetchrow(_VPN_QUALITY_OVERALL, from_ts, to_ts),
        pool.fetch(_VPN_QUALITY_WEB_VERDICTS, from_ts, to_ts),
        pool.fetch(_VPN_QUALITY_WEB_REASONS, from_ts, to_ts),
    )
    servers = [
        {
            "server_label": str(row["server_label"]),
            "checks": int(row["checks"] or 0),
            "users": int(row["users"] or 0),
            "latency_p50_ms": int(row["latency_p50_ms"]) if row["latency_p50_ms"] is not None else None,
            "latency_p95_ms": int(row["latency_p95_ms"]) if row["latency_p95_ms"] is not None else None,
            "throughput_p50_kbps": (
                int(row["throughput_p50_kbps"]) if row["throughput_p50_kbps"] is not None else None
            ),
        }
        for row in server_rows
    ]
    checks = int(overall_row["checks"] or 0)
    connected = int(overall_row["connected_checks"] or 0)
    verdicts = {str(row["event_name"]): int(row["users"] or 0) for row in verdict_rows}
    return {
        "servers": servers,
        "overall": {
            "checks": checks,
            "connected_checks": connected,
            "connected_rate": round(connected / checks, 4) if checks else None,
            "connected_users": int(overall_row["connected_users"] or 0),
            "latency_p50_ms": (
                int(overall_row["latency_p50_ms"]) if overall_row["latency_p50_ms"] is not None else None
            ),
            "latency_p95_ms": (
                int(overall_row["latency_p95_ms"]) if overall_row["latency_p95_ms"] is not None else None
            ),
        },
        "web_verdicts": {
            "confirmed_users": verdicts.get("connection_confirmed", 0),
            "problem_users": verdicts.get("connection_problem", 0),
        },
        "web_problem_reasons": [
            {"code": str(row["reason_code"]), "users": int(row["users"] or 0)} for row in reason_rows
        ],
    }


async def _notification_holdout_stats(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> dict:
    rows = await pool.fetch(_NOTIFICATION_HOLDOUT_STATS, from_ts, to_ts)
    result: dict[str, dict] = {}
    for row in rows:
        notification_type = str(row["notification_type"])
        status = str(row["delivery_status"])
        users = int(row["users"] or 0)
        paid = int(row["paid_users"] or 0)
        bucket = result.setdefault(
            notification_type,
            {
                "delivered": {"users": 0, "paid_users": 0, "paid_rate": None},
                "holdout": {"users": 0, "paid_users": 0, "paid_rate": None},
            },
        )
        bucket[status] = {
            "users": users,
            "paid_users": paid,
            "paid_rate": round(paid / users, 4) if users else None,
        }
    return result


@_with_admin_auth
async def handle_admin_stats(request: Request) -> JSONResponse:
    """GET /api/v1/admin/stats?period=7d&tz=Europe/Moscow — агрегированный дашборд статистики."""
    pool: asyncpg.Pool = request.app.state.pool

    period = (request.query_params.get("period") or _DEFAULT_PERIOD).strip()
    allowed_periods = {*_PERIODS, _CUSTOM_PERIOD}
    if period not in allowed_periods:
        return safe_json_error(400, "invalid_period", f"period must be one of {sorted(allowed_periods)}")

    tz = (request.query_params.get("tz") or _DEFAULT_TZ).strip()
    if tz not in _ALLOWED_TZ:
        return safe_json_error(400, "invalid_tz", "Unsupported timezone")

    now = datetime.now(UTC)
    try:
        window = _compute_window(
            period,
            tz,
            date_from_raw=request.query_params.get("date_from"),
            date_to_raw=request.query_params.get("date_to"),
            now=now,
        )
    except ValueError as exc:
        return safe_json_error(400, "invalid_date_range", str(exc))

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
        telegram_ads_report,
        telegram_ads_campaigns_total,
        product_funnel,
        funnel_diagnostics_v2,
        trial_feedback,
        experiments,
        data_health,
        trial_cohorts,
        vpn_quality,
        notification_holdout,
    ) = await asyncio.gather(
        _revenue_series(pool, window.trunc, tz, window.from_ts, window.to_ts),
        _revenue_kpi(pool, window.from_ts, window.to_ts),
        _count_series(pool, _NEW_USERS_SERIES, window.trunc, tz, window.from_ts, window.to_ts),
        _scalar(pool, _NEW_USERS_KPI, window.from_ts, window.to_ts),
        _count_series(pool, _TRIALS_SERIES, window.trunc, tz, window.from_ts, window.to_ts),
        _scalar(pool, _TRIALS_KPI, window.from_ts, window.to_ts),
        _trial_paid_rate(pool, window.from_ts, window.to_ts),
        _active_by_plan(pool),
        _scalar(pool, _ACTIVE_TOTAL),  # запрос без параметров — текущий живой gauge
        _traffic_series(pool, window.from_date, window.to_date),
        _traffic_by_server(pool, window.from_date, window.to_date),
        _mature_funnel(pool, window.from_ts, window.to_ts),
        _telegram_ads_campaigns(pool, window.from_ts, window.to_ts, window.from_date, window.to_date),
        _scalar(
            pool,
            _TELEGRAM_ADS_CAMPAIGNS_TOTAL,
            window.from_ts,
            window.to_ts,
            window.from_date,
            window.to_date,
        ),
        _product_funnel(pool, window.from_ts, window.to_ts),
        _funnel_diagnostics_v2(pool, window, _completed_through_date(window, tz)),
        _trial_feedback_stats(pool, window.from_ts, window.to_ts),
        _experiment_results(pool, window.from_ts, window.to_ts, window.to_date),
        _data_health(pool, window.from_ts, window.to_ts, window.from_date, window.to_date),
        _trial_cohorts(pool, window, _completed_through_date(window, tz)),
        _vpn_quality(pool, window.from_ts, window.to_ts),
        _notification_holdout_stats(pool, window.from_ts, window.to_ts),
    )

    traffic_total_gb = round(sum(p["gb"] for p in traffic_series), 2)
    traffic_users = max((p["users"] for p in traffic_series), default=0)

    return JSONResponse(
        {
            "ok": True,
            "period": {
                "key": window.period_key,
                "from": _iso(window.from_ts),
                "to": _iso(window.to_ts),
                "date_from": window.from_date.isoformat(),
                "date_to": window.to_date.isoformat(),
                "bucket": window.trunc,
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
            "product_funnel": product_funnel,
            "funnel_diagnostics_v2": funnel_diagnostics_v2,
            "trial_feedback": trial_feedback,
            "experiments": experiments,
            "data_health": data_health,
            "trial_cohorts": trial_cohorts,
            "vpn_quality": vpn_quality,
            "notification_holdout": notification_holdout,
            "telegram_ads": {
                "campaigns": telegram_ads_report["campaigns"],
                "economics": telegram_ads_report["economics"],
                "funnel": telegram_ads_report["funnel"],
                "total_campaigns": telegram_ads_campaigns_total,
                "truncated": telegram_ads_campaigns_total > _TELEGRAM_ADS_CAMPAIGNS_LIMIT,
            },
            "traffic_by_server": traffic_by_server,
        }
    )
