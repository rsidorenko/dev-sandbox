"""Сохраняемая выдача доступа: повторные попытки не меняют деньги и подписку."""

import logging
import uuid

from app.issuance.vless_provider import VlessProviderOutcome

_LOGGER = logging.getLogger(__name__)


async def enqueue_access_in_connection(conn, internal_user_id: str, operation_key: str) -> None:
    await conn.execute(
        "INSERT INTO balance_access_jobs (internal_user_id, operation_key) VALUES ($1,$2) ON CONFLICT DO NOTHING",
        internal_user_id,
        operation_key,
    )


async def fulfill_access_job(pool, provider, internal_user_id: str, operation_key: str) -> str:
    try:
        return await _fulfill_access_job(pool, provider, internal_user_id, operation_key)
    except Exception:
        _LOGGER.warning("balance_access_storage_failed user=%s", internal_user_id, exc_info=True)
        return "pending"


async def _fulfill_access_job(pool, provider, internal_user_id: str, operation_key: str) -> str:
    """Одна попытка с lease; исторические платежи без задания не переигрываем."""
    status = await pool.fetchval(
        "SELECT status FROM balance_access_jobs WHERE internal_user_id=$1 AND operation_key=$2",
        internal_user_id,
        operation_key,
    )
    if status is None or status != "pending":
        return status or "done"
    if provider is None:
        return "pending"
    token = uuid.uuid4()
    claimed = await pool.fetchrow(
        "UPDATE balance_access_jobs SET lease_token=$3, lease_until=NOW()+INTERVAL '2 minutes',"
        " attempts=attempts+1, updated_at=NOW()"
        " WHERE internal_user_id=$1 AND operation_key=$2 AND status='pending'"
        " AND next_attempt_at<=NOW() AND (lease_until IS NULL OR lease_until<NOW()) RETURNING attempts",
        internal_user_id,
        operation_key,
        token,
    )
    if claimed is None:
        return (
            await pool.fetchval(
                "SELECT status FROM balance_access_jobs WHERE internal_user_id=$1 AND operation_key=$2",
                internal_user_id,
                operation_key,
            )
            or "pending"
        )
    try:
        result = await provider.sync_user_access(internal_user_id=internal_user_id)
        if result.outcome == VlessProviderOutcome.SUCCESS:
            final_status = "done"
        elif result.outcome == VlessProviderOutcome.NOT_FOUND:
            final_status = "superseded"
        else:
            final_status = "pending"
    except Exception:
        _LOGGER.warning("balance_access_attempt_failed user=%s", internal_user_id, exc_info=True)
        final_status = "pending"
    # CancelledError не перехватываем: lease истечёт, новый процесс продолжит попытку.
    delay = min(300, 30 * 2 ** min(claimed["attempts"] - 1, 4))
    updated = await pool.fetchval(
        "UPDATE balance_access_jobs SET status=$4, lease_token=NULL, lease_until=NULL,"
        " next_attempt_at=NOW()+$5*INTERVAL '1 second', updated_at=NOW()"
        " WHERE internal_user_id=$1 AND operation_key=$2 AND lease_token=$3 RETURNING status",
        internal_user_id,
        operation_key,
        token,
        final_status,
        delay,
    )
    return updated or "pending"


async def recover_access_jobs(pool, provider, *, limit: int = 20) -> int:
    """Ограниченный проход очереди; lease защищает несколько процессов worker."""
    jobs = await pool.fetch(
        "SELECT internal_user_id, operation_key FROM balance_access_jobs"
        " WHERE status='pending' AND next_attempt_at<=NOW()"
        " AND (lease_until IS NULL OR lease_until<NOW())"
        " ORDER BY next_attempt_at, created_at LIMIT $1",
        limit,
    )
    completed = 0
    for job in jobs:
        status = await fulfill_access_job(pool, provider, job["internal_user_id"], job["operation_key"])
        completed += status in ("done", "superseded")
    return completed
