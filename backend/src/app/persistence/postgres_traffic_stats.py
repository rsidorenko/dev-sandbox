"""PostgreSQL-персистентность учёта трафика bare_xray (миграция 079).

Две таблицы:
- ``bare_xray_traffic_state`` — последние абсолютные показания per-PROCESS
  счётчиков (для диффа коллектором; см. bare_xray_traffic.diff_deltas);
- ``bare_xray_traffic_daily`` — накопленные дельты по (MSK-дата, server_id, email),
  UPSERT-аккумуляция внутри одной транзакции со state.

Чтение — месячный тотал и разбивка по серверам для одного internal_user_id:
резолв email ТОЛЬКО вперёд (``user-<id[:16]>`` / ``x-user-<id[:16]>``), обратный
парсинг ненадёжен из-за усечения идентификатора.

Шаблон зеркалирует postgres_pricing.py: ctor принимает pool, SQL — статические
литералы. Промоутеры удалений здесь нет и быть не может — только INSERT/UPDATE.
"""

from __future__ import annotations

import asyncpg

from app.domain.trial import TRIAL_DURATION_DAYS
from app.issuance.bare_xray_traffic import (
    TrafficSample,
    msk_month_bounds,
    msk_stat_date,
    user_emails_for_internal,
)


class PostgresTrafficStatsRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def load_state(self, server_id: int) -> dict[str, dict[str, int]]:
        """Сохранённые абсолютные показания сервера: ``{email: {"up","down"}}``."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT email, uplink_last, downlink_last FROM bare_xray_traffic_state WHERE server_id = $1",
                server_id,
            )
        return {row["email"]: {"up": row["uplink_last"], "down": row["downlink_last"]} for row in rows}

    async def apply_batch(self, batch: list[TrafficSample]) -> int:
        """Одна транзакция: дневная аккумуляция дельт + абсолюты в state.

        daily получает ``delta_*`` (прирост), state — абсолютные ``uplink/downlink``
        (последнее показание счётчика для следующего диффа). Атомарность
        обязательна: state должен отражать ровно те показания, чьи дельты попали
        в daily — иначе тик, упавший между двумя записями, породил бы двойной
        подсчёт при повторе. В этой же транзакции первый положительный прирост
        один раз переносит clock активного триала на фактическое подключение.
        Возвращает число стартовавших по трафику триалов. Пустой батч — no-op
        без коннекта.
        """
        if not batch:
            return 0
        stat_date = msk_stat_date()
        positive_emails = sorted({row.email for row in batch if row.delta_up > 0 or row.delta_down > 0})
        started_trials = 0
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(
                    """
                    INSERT INTO bare_xray_traffic_daily
                        (stat_date, server_id, email, uplink_bytes, downlink_bytes, updated_at)
                    VALUES ($1, $2, $3, $4, $5, NOW())
                    ON CONFLICT (stat_date, server_id, email) DO UPDATE SET
                        uplink_bytes = bare_xray_traffic_daily.uplink_bytes + EXCLUDED.uplink_bytes,
                        downlink_bytes = bare_xray_traffic_daily.downlink_bytes + EXCLUDED.downlink_bytes,
                        updated_at = NOW()
                    """,
                    [(stat_date, row.server_id, row.email, row.delta_up, row.delta_down) for row in batch],
                )
                await conn.executemany(
                    """
                    INSERT INTO bare_xray_traffic_state
                        (server_id, email, uplink_last, downlink_last, collected_at)
                    VALUES ($1, $2, $3, $4, NOW())
                    ON CONFLICT (server_id, email) DO UPDATE SET
                        uplink_last = EXCLUDED.uplink_last,
                        downlink_last = EXCLUDED.downlink_last,
                        collected_at = NOW()
                    """,
                    [(row.server_id, row.email, row.uplink, row.downlink) for row in batch],
                )
                if positive_emails:
                    rows = await conn.fetch(
                        """
                        UPDATE subscription_snapshots s
                        SET trial_first_traffic_at = NOW(),
                            trial_started_at = NOW(),
                            trial_expires_at = NOW() + make_interval(days => COALESCE((
                                SELECT CASE WHEN ea.variant = '7d' THEN 7 ELSE $2 END
                                FROM experiment_assignments ea
                                WHERE ea.internal_user_id = s.internal_user_id
                                  AND ea.experiment_key = 'trial_duration_v1'
                            ), $2)),
                            active_until_utc = NOW() + make_interval(days => COALESCE((
                                SELECT CASE WHEN ea.variant = '7d' THEN 7 ELSE $2 END
                                FROM experiment_assignments ea
                                WHERE ea.internal_user_id = s.internal_user_id
                                  AND ea.experiment_key = 'trial_duration_v1'
                            ), $2)),
                            updated_at = NOW()
                        WHERE s.state_label = 'active'
                          AND s.plan_id IS NULL
                          AND s.trial_started_at IS NOT NULL
                          AND s.trial_first_traffic_at IS NULL
                          AND s.trial_expires_at > NOW()
                          AND EXISTS (
                              SELECT 1
                              FROM unnest($1::text[]) AS observed(email)
                              WHERE observed.email IN (
                                  'user-' || left(s.internal_user_id, 16),
                                  'x-user-' || left(s.internal_user_id, 16)
                              )
                          )
                        RETURNING s.internal_user_id
                        """,
                        positive_emails,
                        TRIAL_DURATION_DAYS,
                    )
                    started_trials = len(rows)
        return started_trials

    async def monthly_total_for_user(self, internal_user_id: str) -> int:
        """Суммарный трафик (up+down, байты) пользователя за текущий MSK-месяц."""
        first, last = msk_month_bounds()
        async with self._pool.acquire() as conn:
            total = await conn.fetchval(
                "SELECT COALESCE(SUM(uplink_bytes + downlink_bytes), 0) "
                "FROM bare_xray_traffic_daily "
                "WHERE email = ANY($1) AND stat_date >= $2 AND stat_date <= $3",
                list(user_emails_for_internal(internal_user_id)),
                first,
                last,
            )
        return int(total or 0)

    async def monthly_usage_for_user(self, internal_user_id: str) -> tuple[int, int]:
        """Upload и download (байты) пользователя за текущий MSK-месяц."""
        first, last = msk_month_bounds()
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT COALESCE(SUM(uplink_bytes), 0) AS upload_bytes, "
                "COALESCE(SUM(downlink_bytes), 0) AS download_bytes "
                "FROM bare_xray_traffic_daily "
                "WHERE email = ANY($1) AND stat_date >= $2 AND stat_date <= $3",
                list(user_emails_for_internal(internal_user_id)),
                first,
                last,
            )
        if row is None:
            return 0, 0
        return int(row["upload_bytes"] or 0), int(row["download_bytes"] or 0)

    async def per_server_for_user(self, internal_user_id: str) -> list[dict[str, int]]:
        """Разбивка трафика пользователя за текущий MSK-месяц по server_id.

        Для админ-выдачи: [{server_id, uplink_bytes, downlink_bytes}], по убыванию
        суммарного объёма.
        """
        first, last = msk_month_bounds()
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT server_id, COALESCE(SUM(uplink_bytes), 0) AS up_bytes, "
                "COALESCE(SUM(downlink_bytes), 0) AS down_bytes "
                "FROM bare_xray_traffic_daily "
                "WHERE email = ANY($1) AND stat_date >= $2 AND stat_date <= $3 "
                "GROUP BY server_id "
                "ORDER BY (SUM(uplink_bytes) + SUM(downlink_bytes)) DESC",
                list(user_emails_for_internal(internal_user_id)),
                first,
                last,
            )
        return [
            {
                "server_id": row["server_id"],
                "uplink_bytes": int(row["up_bytes"]),
                "downlink_bytes": int(row["down_bytes"]),
            }
            for row in rows
        ]


__all__ = ["PostgresTrafficStatsRepository"]
