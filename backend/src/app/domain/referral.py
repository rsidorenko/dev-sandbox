"""Two-level referral system: codes, commission rates, balance calculations."""

from __future__ import annotations

from dataclasses import dataclass

from app.domain.plans import PlanId, get_plan

LEGACY_STACK_POLICY = "legacy_stack"
BALANCED_V1_POLICY = "balanced_v1"

# Level 1 (direct referral) commission rates by plan
_LEVEL1_RATES: dict[str, float] = {
    PlanId.ONE_DAY: 0.10,
    PlanId.SEVEN_DAYS: 0.15,
    PlanId.TWO_WEEKS: 0.20,
    PlanId.ONE_MONTH: 0.35,
    PlanId.THREE_MONTHS: 0.30,
    PlanId.SIX_MONTHS: 0.20,
    PlanId.ONE_YEAR: 0.17,
}

# Level 2 (referral of referral) commission rates by plan
_LEVEL2_RATES: dict[str, float] = {
    PlanId.ONE_DAY: 0.01,
    PlanId.SEVEN_DAYS: 0.02,
    PlanId.TWO_WEEKS: 0.03,
    PlanId.ONE_MONTH: 0.05,
    PlanId.THREE_MONTHS: 0.03,
    PlanId.SIX_MONTHS: 0.02,
    PlanId.ONE_YEAR: 0.02,
}

_BALANCED_LEVEL1_RATES: dict[str, float] = {
    PlanId.ONE_DAY: 0.10,
    PlanId.SEVEN_DAYS: 0.12,
    PlanId.TWO_WEEKS: 0.15,
    PlanId.ONE_MONTH: 0.20,
    PlanId.THREE_MONTHS: 0.18,
    PlanId.SIX_MONTHS: 0.16,
    PlanId.ONE_YEAR: 0.15,
}

_BALANCED_LEVEL2_RATES: dict[str, float] = {
    PlanId.ONE_DAY: 0.01,
    PlanId.SEVEN_DAYS: 0.01,
    PlanId.TWO_WEEKS: 0.01,
    PlanId.ONE_MONTH: 0.02,
    PlanId.THREE_MONTHS: 0.02,
    PlanId.SIX_MONTHS: 0.015,
    PlanId.ONE_YEAR: 0.01,
}


# Legacy-бонус сохраняется только для связей, созданных до balanced_v1: после первой
# квалифицирующей оплаты прямой реферер получает 30 дней поверх прежнего кешбэка.
REFERRAL_BONUS_DAYS: int = 30
REFERRAL_BONUS_LEVEL: int = 1
REFERRAL_BONUS_QUALIFYING_PLANS: frozenset[str] = frozenset(
    {
        PlanId.ONE_MONTH,
        PlanId.THREE_MONTHS,
        PlanId.SIX_MONTHS,
        PlanId.ONE_YEAR,
    }
)

REFERRAL_INVITEE_BONUS_DAYS: int = 3
BALANCED_MAX_REWARD_RATE: float = 0.32


def is_qualifying_plan_for_bonus(plan_id: str) -> bool:
    """Тариф от месяца, на первой оплате которого срабатывает policy-specific бонус."""
    return plan_id in REFERRAL_BONUS_QUALIFYING_PLANS


@dataclass(frozen=True, slots=True)
class ReferralCommission:
    referrer_user_id: str
    amount_kopecks: int
    level: int
    plan_id: str
    payer_user_id: str


def level1_commission_rate(plan_id: str, reward_policy: str = LEGACY_STACK_POLICY) -> float:
    rates = _BALANCED_LEVEL1_RATES if reward_policy == BALANCED_V1_POLICY else _LEVEL1_RATES
    return rates.get(plan_id, 0.0)


def level2_commission_rate(plan_id: str, reward_policy: str = LEGACY_STACK_POLICY) -> float:
    rates = _BALANCED_LEVEL2_RATES if reward_policy == BALANCED_V1_POLICY else _LEVEL2_RATES
    return rates.get(plan_id, 0.0)


def calculate_commission_kopecks(
    payment_amount_kopecks: int,
    rate: float,
) -> int:
    return round(payment_amount_kopecks * rate)


def build_commissions_for_payment(
    *,
    payer_user_id: str,
    direct_referrer_user_id: str | None,
    indirect_referrer_user_id: str | None,
    plan_id: str,
    payment_amount_kopecks: int,
    reward_policy: str = LEGACY_STACK_POLICY,
) -> tuple[ReferralCommission, ...]:
    results: list[ReferralCommission] = []
    if direct_referrer_user_id is not None:
        rate = level1_commission_rate(plan_id, reward_policy)
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
        rate = level2_commission_rate(plan_id, reward_policy)
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


def resolve_direct_and_indirect_referrers(
    relationships: tuple,
) -> tuple[str | None, str | None]:
    """Extract level-1 (direct) and level-2 (indirect) referrer user IDs from relationships."""
    direct_referrer = None
    indirect_referrer = None
    for r in relationships:
        if r.level == 1:
            direct_referrer = r.referrer_user_id
        if r.level == 2:
            indirect_referrer = r.referrer_user_id
    return direct_referrer, indirect_referrer


def resolve_reward_policy(relationships: tuple) -> str:
    """Политика прямой связи управляет всей цепочкой. Старые/fake записи — legacy."""
    for relationship in relationships:
        if relationship.level == 1:
            return getattr(relationship, "reward_policy", LEGACY_STACK_POLICY)
    return LEGACY_STACK_POLICY


@dataclass(frozen=True, slots=True)
class ReferralEconomics:
    plan_id: str
    cash_reward_rate: float
    invitee_bonus_rate: float
    total_reward_rate: float


def balanced_referral_economics(plan_id: str) -> ReferralEconomics:
    """Верхняя номинальная доля наград от выручки без учёта фактической себестоимости трафика."""
    plan = get_plan(plan_id)
    invitee_rate = 0.0
    if plan is not None and is_qualifying_plan_for_bonus(plan_id):
        invitee_rate = REFERRAL_INVITEE_BONUS_DAYS / plan.duration_days
    cash_rate = level1_commission_rate(plan_id, BALANCED_V1_POLICY) + level2_commission_rate(
        plan_id, BALANCED_V1_POLICY
    )
    return ReferralEconomics(
        plan_id=plan_id,
        cash_reward_rate=cash_rate,
        invitee_bonus_rate=invitee_rate,
        total_reward_rate=cash_rate + invitee_rate,
    )
