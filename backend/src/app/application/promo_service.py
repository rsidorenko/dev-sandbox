"""Промокоды: разрешение активной скидки и атомарное погашение при успехе оплаты.

Единый сервис для web/bot/balance — каноническое место повторной валидации применённого
промокода (активность/срок/лимит/аудитория) и применения скидки через
``domain.promo.apply_promo_discount`` (итог в целых рублях). Погашение выполняется
атомарной транзакцией (advisory lock + ``redeem_in_connection`` + ``increment_redemptions``)
и сделано best-effort: оно никогда не должно блокировать или откатывать успех оплаты —
к моменту погашения деньги и fulfillment уже прошли, поэтому любая ошибка лишь логируется
(аудиторский пробел, но не потеря денег).

Идемпотентность погашения опирается на два UNIQUE-констрейнта ``promo_redemptions``:
``idempotency_key`` (повтор вебхука/двойной клик) и ``(code, internal_user_id)`` (один код
на пользователя). Повторная попытка погасить тот же платёж — no-op без повторного инкремента.

Функции принимают репозитории явно (а не весь composition): web-обработчики работают с
``pool`` напрямую и composition не имеют (как balance_payment_service), а bot/balance
передают ``composition.promo_*``. ``pool`` определяет платформу: не None → postgres-путь
(транзакция + advisory lock, is_new_user через SQL), иначе in-memory (composition-репозитории).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import asyncpg

from app.domain.promo import (
    PromoCode,
    PromoPrice,
    apply_promo_discount,
    audience_eligible,
    is_new_user_eligible,
    redeem_block_reason,
)
from app.persistence.postgres_promo import (
    PostgresPromoCodeRepository,
    PostgresPromoRedemptionRepository,
    new_redemption_id,
)
from app.persistence.promo_contracts import (
    PromoAppliedRepository,
    PromoRedemptionRecord,
    PromoRedemptionRepository,
)

if TYPE_CHECKING:
    from app.application.interfaces import SubscriptionSnapshotReader
    from app.domain.promo import PromoCodeRepository

_LOGGER = logging.getLogger(__name__)

# Ключ идемпотентности погашения привязан к платёжной ссылке: повторный вебхук или
# двойной клик дают тот же ключ → UNIQUE(idempotency_key) гасит дубль без повторного +1.
_REDEEM_IDEMPOTENCY_PREFIX = "redeem:"


@dataclass(frozen=True, slots=True)
class ActivePromo:
    """Активный (прошедший повторную валидацию) промокод с готовой скидкой для применения."""

    code: str
    promo: PromoCode
    price: PromoPrice


async def compute_is_new_user(
    *,
    pool: asyncpg.Pool | None,
    snapshots: SubscriptionSnapshotReader | None,
    internal_user_id: str,
) -> bool:
    """true, если пользователь «новый» (см. domain.promo.is_new_user_eligible).

    На postgres — один запрос собирает три сигнала: trial_used (user_identities),
    активная подписка (subscription_snapshots.state_label='active') и принятый платёжный
    факт (billing_events_ledger status='accepted'). На in-memory (unit-тесты бота) берём
    активность из snapshots, а trial_used/принятый платёж считаем False — глубокая
    проверка аудитории покрывается доменными тестами и postgres-интеграцией.
    """
    if pool is not None:
        row = await pool.fetchrow(
            "SELECT "
            "COALESCE((SELECT trial_used FROM user_identities WHERE internal_user_id = $1), FALSE) AS trial_used, "
            "EXISTS(SELECT 1 FROM subscription_snapshots WHERE internal_user_id = $1 AND state_label = 'active') AS has_active, "
            "EXISTS(SELECT 1 FROM billing_events_ledger WHERE internal_user_id = $1 AND status = 'accepted') AS has_paid",
            internal_user_id,
        )
        if row is None:
            return True  # идентичности нет → пользователь гарантированно новый
        return is_new_user_eligible(
            trial_used=bool(row["trial_used"]),
            has_active_subscription=bool(row["has_active"]),
            has_accepted_payment=bool(row["has_paid"]),
        )
    if snapshots is None:
        return True
    snap = await snapshots.get_for_user(internal_user_id)
    has_active = snap is not None and snap.state_label == "active"
    return is_new_user_eligible(
        trial_used=False,
        has_active_subscription=has_active,
        has_accepted_payment=False,
    )


async def resolve_active_promo(
    *,
    pool: asyncpg.Pool | None,
    promo_applied_repo: PromoAppliedRepository,
    promo_code_repo: PromoCodeRepository,
    snapshots: SubscriptionSnapshotReader | None,
    internal_user_id: str,
    base_rubles: int,
    now: datetime | None = None,
) -> ActivePromo | None:
    """Канонически разрешить применённый промокод и посчитать скидку.

    Возвращает ``ActivePromo`` (code + promo + PromoPrice), либо ``None`` если у
    пользователя нет пригодного промокода. Все причины «нет скидки» дают ``None``
    (fail-closed → пользователь платит полную цену, что безопасно: нет утечки денег).
    """
    if base_rubles <= 0:
        return None
    moment = now or datetime.now(UTC)

    applied = await promo_applied_repo.get(internal_user_id)
    if applied is None or applied.is_expired(moment):
        return None

    promo = await promo_code_repo.get(applied.code)
    if promo is None:
        return None

    if redeem_block_reason(promo, moment) is not None:
        return None

    is_new = await compute_is_new_user(pool=pool, snapshots=snapshots, internal_user_id=internal_user_id)
    if not audience_eligible(promo.audience, is_new_user=is_new):
        return None

    price = apply_promo_discount(base_rubles, promo)
    return ActivePromo(code=promo.code, promo=promo, price=price)


async def redeem_promo_on_success(
    *,
    pool: asyncpg.Pool | None,
    promo_applied_repo: PromoAppliedRepository,
    promo_code_repo: PromoCodeRepository,
    promo_redemption_repo: PromoRedemptionRepository,
    internal_user_id: str,
    code: str,
    base_rubles: int,
    discounted_rubles: int,
    discount_kopecks: int,
    payment_ref: str | None,
    now: datetime | None = None,
) -> bool:
    """Атомарно погасить промокод при успехе оплаты. Best-effort: не бросает исключения.

    Возвращает ``True`` если погашение было freshly вставлено, ``False`` при дубле/пропуске.
    Snapshot математики скидки (base/discounted/discount_kopecks) передаётся вызывающим —
    это те же значения, по которым считалась сумма платежа, т.ч. аудит совпадает с фактом
    оплаты. ``promo_applied`` очищается после (отдельной операцией — это «намерение», а не
    деньги), поэтому сбой очистки не откатывает уже записанное погашение.
    """
    if not code:
        return False
    moment = now or datetime.now(UTC)
    idempotency_key = f"{_REDEEM_IDEMPOTENCY_PREFIX}{payment_ref or 'nopay'}"

    record = PromoRedemptionRecord(
        redemption_id=new_redemption_id(),
        code=code,
        internal_user_id=internal_user_id,
        idempotency_key=idempotency_key,
        base_rubles=base_rubles,
        discounted_rubles=discounted_rubles,
        discount_kopecks=discount_kopecks,
        payment_ref=payment_ref,
        created_at=moment,
    )

    try:
        inserted = await _redeem_atomic(
            pool=pool,
            promo_code_repo=promo_code_repo,
            promo_redemption_repo=promo_redemption_repo,
            record=record,
            code=code,
        )
    except Exception:
        # Деньги и fulfillment уже прошли — погашение лишь аудит/счётчик. Логируем и идём дальше:
        # UNIQUE-констрейнты + ретрай на следующем платеже не дадут «потерять» погашение навсегда,
        # а двойная идемпотентность защитит от повторного инкремента при повторной попытке.
        _LOGGER.warning(
            "promo redemption failed user=%s code=%s ref=%s",
            internal_user_id,
            code,
            payment_ref,
            exc_info=True,
        )
        return False

    # Очистка «намерения» — после погашения. Даже если она упадёт, деньги в безопасности:
    # скидка уже погашена, а повторно применить тот же код пользователю не даст
    # UNIQUE(code, internal_user_id). Сбой очистки лишь оставит «висящее» намерение.
    try:
        await promo_applied_repo.clear(internal_user_id)
    except Exception:
        _LOGGER.debug("promo_applied clear failed user=%s", internal_user_id, exc_info=True)
    return inserted


async def _redeem_atomic(
    *,
    pool: asyncpg.Pool | None,
    promo_code_repo: PromoCodeRepository,
    promo_redemption_repo: PromoRedemptionRepository,
    record: PromoRedemptionRecord,
    code: str,
) -> bool:
    """Платформо-специфичная часть погашения. Возвращает True при freshly вставленной записи."""
    # max_redemptions берём одним чтением (best-effort); инкремент дополнительно защищён
    # на уровне БД (WHERE redemptions_count < max), поэтому рассинхронизация чтения не даёт
    # превысить лимит.
    promo = await promo_code_repo.get(code)
    max_redemptions = promo.max_redemptions if promo is not None else None

    if pool is not None:
        async with pool.acquire() as conn, conn.transaction():
            locked = await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", record.internal_user_id)
            if not locked:
                return False  # другая транзакция погашения в полёте — идемпотентность доберёт
            inserted = await PostgresPromoRedemptionRepository.redeem_in_connection(conn, record)
            if inserted and promo is not None:
                await PostgresPromoCodeRepository.increment_redemptions_in_connection(conn, code, max_redemptions)
            return inserted

    # in-memory путь (unit-тесты бота): instance-метод redeem вместо *_in_connection staticmethod.
    inserted = await promo_redemption_repo.redeem(record)
    if inserted and promo is not None:
        await promo_code_repo.increment_redemptions(code, max_redemptions)  # type: ignore[attr-defined]
    return inserted


__all__ = [
    "ActivePromo",
    "compute_is_new_user",
    "redeem_promo_on_success",
    "resolve_active_promo",
]
