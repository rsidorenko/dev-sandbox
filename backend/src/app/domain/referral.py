"""Two-level referral system: codes, commission rates, balance calculations."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.plans import PlanId

# Level 1 (direct referral) commission rates by plan
_LEVEL1_RATES: dict[str, float] = {
    PlanId.ONE_MONTH: 0.35,
    PlanId.THREE_MONTHS: 0.30,
    PlanId.SIX_MONTHS: 0.25,
}

# Level 2 (referral of referral) commission rates by plan
_LEVEL2_RATES: dict[str, float] = {
    PlanId.ONE_MONTH: 0.05,
    PlanId.THREE_MONTHS: 0.03,
    PlanId.SIX_MONTHS: 0.02,
}


@dataclass(frozen=True, slots=True)
class ReferralCommission:
    referrer_user_id: str
    amount_kopecks: int
    level: int
    plan_id: str
    payer_user_id: str


def level1_commission_rate(plan_id: str) -> float:
    return _LEVEL1_RATES.get(plan_id, 0.0)


def level2_commission_rate(plan_id: str) -> float:
    return _LEVEL2_RATES.get(plan_id, 0.0)


def calculate_commission_kopecks(
    payment_amount_kopecks: int,
    rate: float,
) -> int:
    return int(payment_amount_kopecks * rate)


def build_commissions_for_payment(
    *,
    payer_user_id: str,
    direct_referrer_user_id: str | None,
    indirect_referrer_user_id: str | None,
    plan_id: str,
    payment_amount_kopecks: int,
) -> tuple[ReferralCommission, ...]:
    results: list[ReferralCommission] = []
    if direct_referrer_user_id is not None:
        rate = level1_commission_rate(plan_id)
        amount = calculate_commission_kopecks(payment_amount_kopecks, rate)
        if amount > 0:
            results.append(
                ReferralCommission(
                    referrer_user_id=direct_referrer_user_id,
                    amount_kopecks=amount,
                    level=1,
                    plan_id=plan_id,
                    payer_user_id=payer_user_id,
                )
            )
    if indirect_referrer_user_id is not None:
        rate = level2_commission_rate(plan_id)
        amount = calculate_commission_kopecks(payment_amount_kopecks, rate)
        if amount > 0:
            results.append(
                ReferralCommission(
                    referrer_user_id=indirect_referrer_user_id,
                    amount_kopecks=amount,
                    level=2,
                    plan_id=plan_id,
                    payer_user_id=payer_user_id,
                )
            )
    return tuple(results)


def rubles_from_kopecks(kopecks: int) -> float:
    return kopecks / 100
