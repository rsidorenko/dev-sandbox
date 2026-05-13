"""Pay for subscription from referral balance."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from app.application.referral_handler import get_referral_balance
from app.domain.plans import get_plan
from app.persistence.referral_contracts import (
    ReferralBalanceRepository,
    ReferralTransactionRepository,
)


@dataclass(frozen=True, slots=True)
class ReferralPaymentResult:
    success: bool
    message: str
    remaining_balance_kopecks: int = 0


async def pay_subscription_from_balance(
    *,
    internal_user_id: str,
    plan_id: str,
    balance_repo: ReferralBalanceRepository,
    tx_repo: ReferralTransactionRepository,
) -> ReferralPaymentResult:
    plan = get_plan(plan_id)
    if plan is None:
        return ReferralPaymentResult(success=False, message="Тариф не найден")

    required_kopecks = plan.price_rubles * 100
    balance_info = await get_referral_balance(internal_user_id=internal_user_id, balance_repo=balance_repo)

    if balance_info.balance_kopecks < required_kopecks:
        return ReferralPaymentResult(
            success=False,
            message=f"Недостаточно средств. Баланс: {balance_info.balance_rubles:.2f} ₽, нужно: {plan.price_rubles} ₽",
        )

    debited = await balance_repo.debit(internal_user_id, required_kopecks)
    if debited is None:
        return ReferralPaymentResult(success=False, message="Не удалось списать средства")

    from app.persistence.referral_contracts import ReferralTransactionRecord

    tx = ReferralTransactionRecord(
        transaction_id=f"pay-{uuid.uuid4()}",
        internal_user_id=internal_user_id,
        amount_kopecks=-required_kopecks,
        transaction_type="subscription_payment",
        related_user_id=None,
        related_plan_id=plan_id,
        description=f"Оплата тарифа {plan.duration_months} мес из реферального баланса",
        created_at=datetime.now(UTC),
    )
    await tx_repo.append_transaction(tx)

    return ReferralPaymentResult(
        success=True,
        message=f"Подписка оплачена! Тариф: {plan.duration_months} мес.",
        remaining_balance_kopecks=debited.balance_kopecks,
    )
