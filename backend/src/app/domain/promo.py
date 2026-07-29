"""Промокоды: чистая доменная логика (скидки, валидация, критерии применимости).

Без ввода-вывода и без зависимостей от БД — только frozen-dataclass, константы и
чистые функции. Это позволяет покрывать математику скидки и критерии «нового
пользователя» unit-тестами изолированно.

Ключевые инварианты (выявлены адверсариальной проверкой платёжного конвейера):
- деньги на границе YooKassa — целые рубли (create_payment принимает amount_rubles: int),
  поэтому итог скидки округляется до целого рубля (half-up) и ТОТ ЖЕ результат
  используется и в create_payment, и в пересчёте вебхука → сверка не расходится;
- скидка клампится так, чтобы итог был >= 1 рубля (YooKassa не принимает 0.00) и
  никогда не превышал базу (иначе отрицательный дебит баланса кредитовал бы юзера);
- «новый пользователь» определяется НЕ по доступности триала, а по отсутствию
  принятых платёжных фактов (billing_events_ledger status='accepted') — иначе
  юзер с истёкшей платной подпиской прошёл бы как «новый».
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum

# Применённый промокод живёт в БД (promo_applied) и должен пересчитываться при
# оплате — поэтому TTL по умолчанию задаём здесь, в едином месте.
PROMO_APPLIED_TTL_HOURS = 24


class PromoAudience(StrEnum):
    """Аудитория промокода: только новые, только «не новые» или все пользователи."""

    NEW_USERS = "new_users"
    EXISTING_USERS = "existing_users"
    ALL_USERS = "all_users"  # подходит любому аккаунту — и новому, и «не новому»


class PromoDiscountKind(StrEnum):
    """Тип скидки: процент от цены или фиксированная сумма в рублях."""

    PERCENT = "percent"
    FIXED_RUBLES = "fixed_rubles"


# Фиксированные тарифы, для которых можно задать per-tariff скидку.
# «Свой» (custom:*) сюда НЕ входит — на нём per-tariff промокод не действует.
FIXED_PLAN_IDS: frozenset[str] = frozenset({"1d", "7d", "14d", "1m", "3m", "6m", "365d"})


@dataclass(frozen=True, slots=True)
class PromoCode:
    """Определение промокода (зеркало строки promo_codes)."""

    code: str
    audience: PromoAudience
    discount_kind: PromoDiscountKind
    discount_value: int
    max_redemptions: int | None  # None = без глобального лимита
    redemptions_count: int
    expires_at: datetime | None  # None = бессрочно
    is_active: bool
    note: str | None
    created_at: datetime
    updated_at: datetime
    created_by: str | None
    # Per-tariff скидки: {plan_id: value} для фиксированных тарифов. None/пусто —
    # плоская мода (скидка берётся из discount_value и применяется ко всему).
    # Непустая — per-tariff: скидка только для тарифов из map (см. apply_promo_discount).
    discounts_by_plan: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class PromoPrice:
    """Результат применения скидки к базовой цене (в целых рублях)."""

    base_rubles: int
    discounted_rubles: int
    discount_kopecks: int

    @property
    def discounted_kopecks(self) -> int:
        return self.discounted_rubles * 100


def _round_half_up(value: float) -> int:
    """Округление к ближайшему целому, .5 — вверх (не банковское)."""
    return math.floor(value + 0.5)


def normalize_code(code: str) -> str:
    """Нормализация введённого кода: trim + upper. Применяется и при создании, и при поиске."""
    return code.strip().upper()


def validate_code_format(code: str) -> str | None:
    """Возвращает текст ошибки либо None, если формат кода допустим."""
    normalized = normalize_code(code)
    if not normalized:
        return "Код не может быть пустым"
    if len(normalized) < 3 or len(normalized) > 32:
        return "Длина кода — от 3 до 32 символов"
    if not all(ch.isascii() and (ch.isalnum()) for ch in normalized):
        return "Код содержит недопустимые символы (только латиница и цифры)"
    return None


def validate_discount(kind: PromoDiscountKind, value: int) -> str | None:
    """Возвращает текст ошибки либо None, если значение скидки допустимо."""
    if value <= 0:
        return "Значение скидки должно быть положительным"
    if kind == PromoDiscountKind.PERCENT and value > 100:
        return "Процент скидки не может превышать 100"
    if kind == PromoDiscountKind.FIXED_RUBLES and value < 1:
        return "Фиксированная скидка — минимум 1 рубль"
    return None


def validate_discounts_by_plan(kind: PromoDiscountKind, mapping: dict[str, int] | None) -> str | None:
    """Валидация per-tariff карты скидок. None/пусто — не per-tariff мода (ошибка,
    если ожидается per-tariff). Возвращает текст ошибки либо None."""
    if not mapping:
        return "Укажите скидку хотя бы для одного тарифа"
    for plan_id, value in mapping.items():
        if plan_id not in FIXED_PLAN_IDS:
            return f"Неизвестный тариф: {plan_id}"
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            return f"Скидка для тарифа {plan_id} должна быть положительным целым"
        if kind == PromoDiscountKind.PERCENT and value > 100:
            return f"Процент для тарифа {plan_id} не может превышать 100"
    return None


def is_expired(expires_at: datetime | None, now: datetime | None = None) -> bool:
    if expires_at is None:
        return False
    now = now or datetime.now(UTC)
    return now >= expires_at


def redeem_block_reason(promo: PromoCode, now: datetime | None = None) -> str | None:
    """Причина, по которой промокод нельзя гасить (None = можно).

    Проверяет только свойства самого кода (активность, срок, глобальный лимит).
    Принадлежность к аудитории и лимит «один на пользователя» проверяются отдельно,
    т.к. требуют контекста пользователя и/или транзакции.
    """
    if not promo.is_active:
        return "promo_inactive"
    if is_expired(promo.expires_at, now):
        return "promo_expired"
    if promo.max_redemptions is not None and promo.redemptions_count >= promo.max_redemptions:
        return "promo_exhausted"
    return None


def is_new_user_eligible(*, has_accepted_payment: bool) -> bool:
    """true, если пользователь НИКОГДА не платил — пригоден для new-only промокода.

    «Новый» = нет принятых платёжных фактов (billing_events_ledger status='accepted').
    Триал и активная (триал/бесплатная) подписка НЕ делают пользователя «не новым» —
    триал бесплатный и не является конверсией: юзер на триале всё ещё потенциальный
    первый покупатель и должен видеть аквизиционный промокод. Кто хоть раз платил
    (активная или истёкшая платная подписка) — не новый.

    Раньше здесь дополнительно гейтились trial_used и has_active_subscription, но из-за
    авто-триала это блокировало new-only промокоды почти для всей базы.
    """
    return not has_accepted_payment


def audience_eligible(audience: PromoAudience, *, is_new_user: bool) -> bool:
    """Подходит ли пользователь под аудиторию промокода."""
    if audience == PromoAudience.ALL_USERS:
        return True
    if audience == PromoAudience.NEW_USERS:
        return is_new_user
    if audience == PromoAudience.EXISTING_USERS:
        return not is_new_user
    return False


def _resolve_discount_spec(
    promo: PromoCode, *, plan_id: str | None, is_addon: bool
) -> tuple[PromoDiscountKind, int] | None:
    """Эффективная скидка для данной покупки: (kind, value) либо None (= без скидки).

    - Плоская мода (discounts_by_plan пусто/None): discount_value применяется ко
      всему — подписки любого тарифа (вкл. «Свой»), add-device. Backward-compat.
    - Per-tariff мода (discounts_by_plan непусто): скидка только для подписки
      фиксированного тарифа из map. add-device, «Свой» (custom:*) и тарифы без
      явной записи → без скидки (None).
    """
    if promo.discounts_by_plan:
        if is_addon or not plan_id or plan_id.startswith("custom:"):
            return None
        value = promo.discounts_by_plan.get(plan_id)
        if value is None:
            return None
        return promo.discount_kind, value
    return promo.discount_kind, promo.discount_value


def apply_promo_discount(
    base_rubles: int,
    promo: PromoCode,
    *,
    plan_id: str | None = None,
    is_addon: bool = False,
) -> PromoPrice:
    """Применить скидку промокода к базовой цене (целые рубли).

    plan_id/is_addon определяют, какая скидка актуальна (см. ``_resolve_discount_spec``):
    per-tariff промокод на «Свой»/add-device/не заданном тарифе → без скидки.

    Гарантии:
    - при base_rubles <= 0 скидка не применяется (нечего скидывать — например,
      add-device без доплаты);
    - итог округляется до целого рубля (half-up) — требование границы YooKassa;
    - итог >= 1 рубля, чтобы платёжный путь никогда не получал 0.00;
    - итог <= base_rubles — скидка не может сделать цену выше базы или отрицательной;
    - discount_kopecks = (base - discounted) * 100 — именно это значение кладётся в
      metadata платежа и вычитается вебхуком при пересчёте ожидаемой суммы.
    """
    if base_rubles <= 0:
        return PromoPrice(base_rubles=0, discounted_rubles=0, discount_kopecks=0)
    spec = _resolve_discount_spec(promo, plan_id=plan_id, is_addon=is_addon)
    if spec is None:
        # Скидка неприменима (per-tariff: addon/custom/не задан) → полная цена.
        return PromoPrice(
            base_rubles=base_rubles,
            discounted_rubles=base_rubles,
            discount_kopecks=0,
        )
    kind, value = spec
    if kind == PromoDiscountKind.PERCENT:
        discounted_exact = base_rubles * (100 - value) / 100
        discounted = _round_half_up(discounted_exact)
    else:  # PromoDiscountKind.FIXED_RUBLES
        discounted = base_rubles - value
    discounted = max(1, min(discounted, base_rubles))
    discount_kopecks = (base_rubles - discounted) * 100
    return PromoPrice(
        base_rubles=base_rubles,
        discounted_rubles=discounted,
        discount_kopecks=discount_kopecks,
    )
