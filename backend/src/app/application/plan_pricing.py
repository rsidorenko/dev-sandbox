"""Runtime-цены тарифов: провайдер с кэшем + расчёт эффективного списания.

Источник цен — таблица plan_price_overrides (миграция 076): отсутствие строки
= код-дефолт из domain/plans.py::_PLANS. Глобальная скидка — синглтон
pricing_global_discount. Оба состояния читаются одним снимком load_state()
и кэшируются in-process:

- бот и web API живут в одном процессе (docker-compose `backend`), поэтому
  инвалидация после админ-записи (invalidate_all_plan_pricing) применяется
  мгновенно на обеих поверхностях;
- TTL 30 секунд — страховка на случай разнесения процессов/реплик: цена
  меняется без рестарта, максимум задержки — TTL + момент инцидента.

Fail-open: при ошибке чтения БД провайдер отдаёт код-дефолты (чек-аут не должен
падать из-за чтения цен) и пишет warning. Плата за надёжность: fallback может
завысить цену относительно заданного оверрайда — безопаснее блокировки продаж.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace

import asyncpg

from app.domain.plans import SubscriptionPlan, get_all_plans
from app.domain.plans import get_plan as get_plan_default
from app.domain.pricing import (
    WINNER_GLOBAL,
    WINNER_PROMO,
    EffectivePrice,
    GlobalDiscount,
    effective_plan_price,
    global_discount_label,
)
from app.domain.promo import PromoCode
from app.persistence.postgres_pricing import PostgresPlanPricingRepository

_LOGGER = logging.getLogger(__name__)

# Сколько живёт кэш цен (сек). Согласован с Cache-Control публичного /api/v1/plans.
PRICING_CACHE_TTL_SECONDS = 30.0

# loader возвращает снимок состояния: (оверрайды {plan_id: цена}, скидка|None).
PricingLoader = Callable[[], Awaitable[tuple[dict[str, int], GlobalDiscount | None]]]


def _default_loader(pool: asyncpg.Pool) -> PricingLoader:
    async def load() -> tuple[dict[str, int], GlobalDiscount | None]:
        return await PostgresPlanPricingRepository(pool).load_state()

    return load


def _effective_plans(overrides: dict[str, int]) -> tuple[SubscriptionPlan, ...]:
    """7 фиксированных планов с применёнными оверрайдами (некорректные значения
    из БД молча игнорируются — CHECK миграции не пускает их, это вторая линия)."""
    plans: list[SubscriptionPlan] = []
    for plan in get_all_plans():
        override = overrides.get(plan.plan_id.value)
        if isinstance(override, int) and not isinstance(override, bool) and override >= 1:
            plans.append(replace(plan, price_rubles=override))
        else:
            plans.append(plan)
    return tuple(plans)


@dataclass(frozen=True, slots=True)
class PricingState:
    """Иммутабельный снимок состояния цен (кэшируется целиком)."""

    plans: tuple[SubscriptionPlan, ...]
    overrides: dict[str, int]
    global_discount: GlobalDiscount | None


def _default_state() -> PricingState:
    return PricingState(plans=get_all_plans(), overrides={}, global_discount=None)


class PlanPricingProvider:
    """Кэширующий провайдер эффективных цен и глобальной скидки."""

    def __init__(
        self,
        pool: asyncpg.Pool | None,
        *,
        ttl_seconds: float = PRICING_CACHE_TTL_SECONDS,
        loader: PricingLoader | None = None,
    ) -> None:
        self._pool = pool
        self._ttl_seconds = ttl_seconds
        self._loader = loader if loader is not None else (_default_loader(pool) if pool is not None else None)
        self._state: PricingState | None = None
        self._state_at: float = 0.0
        self._lock = asyncio.Lock()

    async def get_state(self) -> PricingState:
        """Снимок цен: из кэша, пока он свежее TTL; иначе перезагрузка из БД."""
        if self._loader is None:  # pool=None (in-memory тесты) — всегда дефолты кода.
            return _default_state()
        now = time.monotonic()
        if self._state is not None and now - self._state_at < self._ttl_seconds:
            return self._state
        async with self._lock:
            now = time.monotonic()
            if self._state is not None and now - self._state_at < self._ttl_seconds:
                return self._state
            if self._loader is None:  # повторная проверка внутри лока
                return _default_state()
            try:
                overrides, global_discount = await self._loader()
                state = PricingState(
                    plans=_effective_plans(overrides),
                    overrides=dict(overrides),
                    global_discount=global_discount,
                )
            except Exception:
                # Fail-open: чекаут не должен падать из-за чтения цен. Кэшируем
                # дефолты на тот же TTL, чтобы не долбить упавшую БД каждым запросом.
                _LOGGER.warning("plan_pricing_load_failed_fallback_to_defaults", exc_info=True)
                state = _default_state()
            self._state = state
            self._state_at = time.monotonic()
            return state

    async def get_plan(self, plan_id: str) -> SubscriptionPlan | None:
        """Эффективный план. custom:N — парсинг кодом (оверрайды не действуют)."""
        if plan_id.startswith("custom:"):
            return get_plan_default(plan_id)
        state = await self.get_state()
        for plan in state.plans:
            if plan.plan_id.value == plan_id:
                return plan
        return None

    async def get_fixed_plans(self) -> tuple[SubscriptionPlan, ...]:
        """Все 7 фиксированных планов с эффективными ценами (порядок как в коде)."""
        return (await self.get_state()).plans

    async def get_global_discount(self) -> GlobalDiscount | None:
        return (await self.get_state()).global_discount

    def invalidate(self) -> None:
        """Сброс кэша (следующее чтение пойдёт в БД)."""
        self._state = None
        self._state_at = 0.0


# --- Реестр провайдеров: per-pool синглтон + инвалидация всех инстансов ---

# asyncpg.Pool использует __slots__ БЕЗ __weakref__ → WeakKeyDictionary невозможен
# («cannot create weak reference to 'Pool'»). Храним (pool, provider) по id(pool):
# сильная ссылка на пул исключает переиспользование id, а количество пулов на
# процесс мало (в проде — один). Страховка от утечки в длинных тестовых прогонах —
# мягкий лимит: при превышении реестр сбрасывается целиком (дешёвое восстановление:
# следующий вызов просто создаст провайдер заново и перечитает цены).
_PROVIDERS: dict[int, tuple[asyncpg.Pool, PlanPricingProvider]] = {}
_PROVIDERS_SOFT_LIMIT = 64
_NO_POOL_PROVIDER: PlanPricingProvider | None = None


def get_plan_pricing(pool: asyncpg.Pool | None) -> PlanPricingProvider:
    """Провайдер цен для пула (синглтон на процесс): бот и web делят один кэш.

    pool=None — статические код-дефолты (in-memory тесты бота).
    """
    global _NO_POOL_PROVIDER
    if pool is None:
        if _NO_POOL_PROVIDER is None:
            _NO_POOL_PROVIDER = PlanPricingProvider(None)
        return _NO_POOL_PROVIDER
    key = id(pool)
    entry = _PROVIDERS.get(key)
    if entry is not None and entry[0] is pool:
        return entry[1]
    if len(_PROVIDERS) >= _PROVIDERS_SOFT_LIMIT:
        _PROVIDERS.clear()
    provider = PlanPricingProvider(pool)
    _PROVIDERS[key] = (pool, provider)
    return provider


def invalidate_all_plan_pricing() -> None:
    """Инвалидация кэша всех провайдеров процесса. Вызывается после админ-записи."""
    for _, provider in _PROVIDERS.values():
        provider.invalidate()
    if _NO_POOL_PROVIDER is not None:
        _NO_POOL_PROVIDER.invalidate()


# --- Общий расчёт списания для чекаутов (web / бот / баланс) ---


@dataclass(frozen=True, slots=True)
class ResolvedCharge:
    """Итог расчёта: эффективная цена + поля для metadata платежа."""

    price: EffectivePrice
    charge_rubles: int
    base_amount_kopecks: int

    @property
    def charge_kopecks(self) -> int:
        return self.charge_rubles * 100


def resolve_effective_charge(
    *,
    base_rubles: int,
    plan_id: str | None,
    promo: PromoCode | None,
    global_discount: GlobalDiscount | None,
) -> ResolvedCharge:
    """Best-of(промо, глобальная) на одной базе — единая точка расчёта для всех
    платёжных путей (web payment, бот YooKassa/CryptoBot, баланс)."""
    price = effective_plan_price(base_rubles, plan_id=plan_id, promo=promo, global_discount=global_discount)
    return ResolvedCharge(
        price=price,
        charge_rubles=price.discounted_rubles,
        base_amount_kopecks=price.base_rubles * 100,
    )


def charge_metadata_fields(
    resolved: ResolvedCharge,
    *,
    promo_code: str | None,
    global_discount: GlobalDiscount | None,
) -> dict[str, str]:
    """Поля metadata платежа о ценах (значения — строки, как у YooKassa).

    - base_amount_kopecks — всегда: защита «летающих» платежей при смене цены
      админом (вебхук предпочитает metadata, а не пересчёт из кода);
    - promo_code — ТОЛЬКО когда выиграл промокод: тогда вебхук погасит код
      (redeem) как обычно; global-платежи промокод не расходуют;
    - discount_kopecks — фактическая скидка победителя (вебхук сверяет сумму).
    """
    meta: dict[str, str] = {"base_amount_kopecks": str(resolved.base_amount_kopecks)}
    if resolved.price.winner == WINNER_PROMO and promo_code:
        meta["promo_code"] = promo_code
        meta["discount_kopecks"] = str(resolved.price.discount_kopecks)
    elif resolved.price.winner == WINNER_GLOBAL and global_discount is not None:
        meta["discount_kopecks"] = str(resolved.price.discount_kopecks)
        meta["global_discount"] = global_discount_label(global_discount)
    return meta
