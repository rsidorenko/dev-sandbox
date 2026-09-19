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
)
SELECT a.experiment_key, a.variant,
       count(*)::int AS exposures,
       count(pc.internal_user_id)::int AS payment_created_users,
       count(p.internal_user_id)::int AS paid_users,
       COALESCE(sum(p.revenue_kopecks), 0)::bigint AS revenue_kopecks
FROM assignments a
LEFT JOIN payment_created pc
  ON pc.internal_user_id = a.internal_user_id AND pc.experiment_key = a.experiment_key
LEFT JOIN paid p
  ON p.internal_user_id = a.internal_user_id AND p.experiment_key = a.experiment_key
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
    rows = await pool.fetch(_TELEGRAM_ADS_CAMPAIGNS, from_ts, to_ts, from_date, to_date)
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


async def _experiment_results(pool: asyncpg.Pool, from_ts: datetime, to_ts: datetime) -> list[dict]:
    rows = await pool.fetch(_EXPERIMENT_RESULTS, from_ts, to_ts)
    results: list[dict] = []
    for row in rows:
        exposures = int(row["exposures"] or 0)
        created = int(row["payment_created_users"] or 0)
        paid = int(row["paid_users"] or 0)
        results.append(
            {
                "experiment_key": row["experiment_key"],
                "variant": row["variant"],
                "exposures": exposures,
                "payment_created_users": created,
                "paid_users": paid,
                "revenue_rubles": round(int(row["revenue_kopecks"] or 0) / 100, 2),
                "checkout_rate": round(created / exposures, 4) if exposures else None,
                "paid_rate": round(paid / exposures, 4) if exposures else None,
            }
        )
    return results


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
        experiments,
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
        _experiment_results(pool, window.from_ts, window.to_ts),
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
            "experiments": experiments,
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
