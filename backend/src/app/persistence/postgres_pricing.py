"""PostgreSQL-персистентность runtime-цен тарифов и глобальной скидки.

Шаблон зеркалирует postgres_promo.py: ctor принимает pool, SQL — статические
литералы (без интерполяции), JSONB пишется через ::jsonb-каст (кодек не
зарегистрирован). Таблицы — миграция 076_pricing_admin.sql:

- plan_price_overrides — оверрайды цен 7 фиксированных тарифов (отсутствие
  строки = код-дефолт из domain/plans.py);
- pricing_global_discount — синглтон глобальной скидки (засеян неактивным);
- admin_pricing_actions — аудит действий оператора (паттерн 064).
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

from app.domain.pricing import GlobalDiscount
from app.domain.promo import PromoDiscountKind

# Колонки синглтона глобальной скидки (единый список SELECT/RETURNING).
_GLOBAL_DISCOUNT_COLUMNS = "singleton, is_active, discount_kind, discount_value, public_label, updated_by, updated_at"


def global_discount_from_row(row: asyncpg.Record | None) -> GlobalDiscount | None:
    """Строка синглтона → доменный GlobalDiscount (None, если строки нет).

    Неактивная скидка может хранить NULL в kind/value — маппим на нейтральные
    значения (percent/0): пока is_active=False, математика их не читает.
    """
    if row is None:
        return None
    kind_raw = row["discount_kind"]
    value_raw = row["discount_value"]
    return GlobalDiscount(
        is_active=row["is_active"],
        discount_kind=PromoDiscountKind(kind_raw) if kind_raw else PromoDiscountKind.PERCENT,
        discount_value=value_raw if value_raw is not None else 0,
        public_label=row["public_label"],
    )


class PostgresPlanPricingRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def load_state(self) -> tuple[dict[str, int], GlobalDiscount | None]:
        """Снимок состояния одним коннектом: ({plan_id: цена}, скидка|None).

        Читается провайдером цен при каждом промахе/истечении кэша — держать
        одним acquire, чтобы оверрайды и скидка не разъехались между запросами.
        """
        async with self._pool.acquire() as conn:
            override_rows = await conn.fetch("SELECT plan_id, price_rubles FROM plan_price_overrides")
            gd_row = await conn.fetchrow(
                f"SELECT {_GLOBAL_DISCOUNT_COLUMNS} "  # noqa: S608
                "FROM pricing_global_discount WHERE singleton = TRUE"
            )
        overrides = {row["plan_id"]: row["price_rubles"] for row in override_rows}
        return overrides, global_discount_from_row(gd_row)

    async def get_plan_price_overrides(self) -> dict[str, int]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT plan_id, price_rubles, updated_by, updated_at FROM plan_price_overrides ORDER BY plan_id"
            )
        return {row["plan_id"]: row["price_rubles"] for row in rows}

    async def upsert_plan_price(self, *, plan_id: str, price_rubles: int, actor: str) -> dict[str, Any]:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO plan_price_overrides (plan_id, price_rubles, updated_by) "
                "VALUES ($1, $2, $3) "
                "ON CONFLICT (plan_id) DO UPDATE "
                "SET price_rubles = EXCLUDED.price_rubles, updated_by = EXCLUDED.updated_by, "
                "updated_at = NOW() "
                "RETURNING plan_id, price_rubles, updated_by, updated_at",
                plan_id,
                price_rubles,
                actor,
            )
        assert row is not None
        return dict(row)

    async def get_plan_price(self, plan_id: str) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT plan_id, price_rubles, updated_by, updated_at FROM plan_price_overrides WHERE plan_id = $1",
                plan_id,
            )
        return None if row is None else dict(row)

    async def delete_plan_price(self, plan_id: str) -> bool:
        async with self._pool.acquire() as conn:
            result = await conn.execute("DELETE FROM plan_price_overrides WHERE plan_id = $1", plan_id)
        return "DELETE 1" in result

    async def get_global_discount_raw(self) -> dict[str, Any]:
        """Сырая строка синглона для админ-API (kind/value могут быть NULL)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_GLOBAL_DISCOUNT_COLUMNS} "  # noqa: S608
                "FROM pricing_global_discount WHERE singleton = TRUE"
            )
        return dict(row) if row is not None else {}

    async def set_global_discount(
        self,
        *,
        is_active: bool,
        discount_kind: str | None,
        discount_value: int | None,
        public_label: str | None,
        actor: str,
    ) -> dict[str, Any]:
        """Полная замена состояния синглотона (хендлер сам решает, что хранить
        при выключении — например, сохранить последние kind/value для повторного
        включения). CHECK миграции 076 не пустит активную скидку без kind/value."""
        async with self._pool.acquire() as conn:
            try:
                row = await conn.fetchrow(
                    "UPDATE pricing_global_discount SET "  # noqa: S608
                    "is_active = $1, discount_kind = $2, discount_value = $3, "
                    "public_label = $4, updated_by = $5, updated_at = NOW() "
                    "WHERE singleton = TRUE "
                    f"RETURNING {_GLOBAL_DISCOUNT_COLUMNS}",
                    is_active,
                    discount_kind,
                    discount_value,
                    public_label,
                    actor,
                )
            except asyncpg.CheckViolationError as exc:
                raise ValueError("global_discount_inconsistent") from exc
        assert row is not None
        return dict(row)

    async def record_action(
        self,
        *,
        actor: str,
        action: str,
        plan_id: str | None,
        old_value: Any,
        new_value: Any,
        correlation_id: str,
    ) -> None:
        """Аудит действия оператора с ценами (old/new — JSON-сериализуемые значения)."""
        async with self._pool.acquire() as conn:
            await conn.execute(
                "INSERT INTO admin_pricing_actions "
                "(actor, action, plan_id, old_value, new_value, correlation_id) "
                "VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6)",
                actor,
                action,
                plan_id,
                json.dumps(old_value, ensure_ascii=False) if old_value is not None else None,
                json.dumps(new_value, ensure_ascii=False) if new_value is not None else None,
                correlation_id,
            )
