"""Уменьшение лимита без перезаписи подписки и с сохраняемым повтором выдачи."""

import uuid
from dataclasses import asdict, replace
from datetime import UTC, datetime

from app.application.balance_access_jobs import enqueue_access_in_connection, fulfill_access_job
from app.domain.access_entitlement import entitlement_deadline
from app.domain.devices import DEFAULT_DEVICE_LIMIT, validate_device_count
from app.issuance.vless_provider import VlessProviderOutcome


async def reduce_device_limit(*, pool, snapshots, provider, internal_user_id: str, new_count: int) -> str:
    if validate_device_count(new_count):
        return "invalid"
    if pool is not None:
        operation = f"device-limit:{uuid.uuid4()}"
        async with pool.acquire() as conn, conn.transaction():
            if not await conn.fetchval("SELECT pg_try_advisory_xact_lock(hashtext($1))", internal_user_id):
                return "busy"
            row = await conn.fetchrow(
                "SELECT * FROM subscription_snapshots WHERE internal_user_id=$1 FOR UPDATE",
                internal_user_id,
            )
            if entitlement_deadline(row, now=datetime.now(UTC)) is None:
                return "no_active"
            if new_count >= (row["device_count"] or DEFAULT_DEVICE_LIMIT):
                return "unchanged"
            await conn.execute(
                "UPDATE subscription_snapshots SET device_count=$2, updated_at=NOW() WHERE internal_user_id=$1",
                internal_user_id,
                new_count,
            )
            await enqueue_access_in_connection(conn, internal_user_id, operation)
        status = await fulfill_access_job(pool, provider, internal_user_id, operation)
        return "done" if status == "done" else "pending"

    snapshot = await snapshots.get_for_user(internal_user_id)
    if snapshot is None or entitlement_deadline(asdict(snapshot), now=datetime.now(UTC)) is None:
        return "no_active"
    if new_count >= (snapshot.device_count or DEFAULT_DEVICE_LIMIT):
        return "unchanged"
    await snapshots.upsert_state(replace(snapshot, device_count=new_count))
    if provider is None:
        return "pending"
    try:
        result = await provider.sync_user_access(internal_user_id=internal_user_id)
        return "done" if result.outcome == VlessProviderOutcome.SUCCESS else "pending"
    except Exception:
        return "pending"
