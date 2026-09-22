"""Денежные комиссии в транзакции принятия балансовой оплаты."""

import uuid
from datetime import UTC, datetime

from app.domain.referral import (
    build_commissions_for_payment,
    resolve_direct_and_indirect_referrers,
    resolve_reward_policy,
)
from app.persistence.postgres_referral import (
    PostgresReferralBalanceRepository,
    PostgresReferralRelationshipRepository,
    PostgresReferralTransactionRepository,
)
from app.persistence.referral_contracts import ReferralTransactionRecord
from app.runtime.fulfillment_processor import CreditedCommission


async def credit_commissions_in_connection(
    conn, payer_internal_user_id: str, plan_id: str, payment_amount_kopecks: int, correlation_id: str, *, prefix: str
) -> tuple[CreditedCommission, ...]:
    credited: list[CreditedCommission] = []
    referrers = await PostgresReferralRelationshipRepository.find_referrers_in_connection(conn, payer_internal_user_id)
    if not referrers:
        return ()
    direct, indirect = resolve_direct_and_indirect_referrers(referrers)
    reward_policy = resolve_reward_policy(referrers)
    commissions = build_commissions_for_payment(
        payer_user_id=payer_internal_user_id,
        direct_referrer_user_id=direct,
        indirect_referrer_user_id=indirect,
        plan_id=plan_id,
        payment_amount_kopecks=payment_amount_kopecks,
        reward_policy=reward_policy,
    )
    now = datetime.now(UTC)
    for comm in commissions:
        idempotency_key = (
            f"{prefix}:l{comm.level}:{comm.payer_user_id}:{comm.plan_id}:{payment_amount_kopecks}:{correlation_id}"
        )
        inserted = await PostgresReferralTransactionRepository.append_transaction_if_idempotency_absent_in_connection(
            conn,
            ReferralTransactionRecord(
                transaction_id=f"ref-{uuid.uuid4()}",
                internal_user_id=comm.referrer_user_id,
                amount_kopecks=comm.amount_kopecks,
                transaction_type="referral_credit",
                related_user_id=comm.payer_user_id,
                related_plan_id=comm.plan_id,
                description=f"Реферальное начисление · уровень {comm.level}",
                idempotency_key=idempotency_key,
                created_at=now,
            ),
        )
        if inserted:
            await PostgresReferralBalanceRepository.credit_in_connection(
                conn, comm.referrer_user_id, comm.amount_kopecks
            )
            credited.append(
                CreditedCommission(
                    referrer_internal_user_id=comm.referrer_user_id,
                    amount_kopecks=comm.amount_kopecks,
                    level=comm.level,
                )
            )
    return tuple(credited)
