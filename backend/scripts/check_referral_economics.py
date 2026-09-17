"""Проверка верхней номинальной нагрузки реферальной политики balanced_v1."""

from __future__ import annotations

from app.domain.plans import get_all_plans
from app.domain.referral import BALANCED_MAX_REWARD_RATE, balanced_referral_economics


def main() -> int:
    exceeded = False
    print("plan\tcash\tinvitee_days\ttotal")
    for plan in get_all_plans():
        economics = balanced_referral_economics(str(plan.plan_id))
        print(
            f"{plan.plan_id}\t{economics.cash_reward_rate:.2%}\t"
            f"{economics.invitee_bonus_rate:.2%}\t{economics.total_reward_rate:.2%}"
        )
        exceeded = exceeded or economics.total_reward_rate > BALANCED_MAX_REWARD_RATE + 1e-9
    if exceeded:
        print(f"FAIL: нагрузка выше лимита {BALANCED_MAX_REWARD_RATE:.0%}")
        return 1
    print(f"OK: нагрузка не выше {BALANCED_MAX_REWARD_RATE:.0%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
