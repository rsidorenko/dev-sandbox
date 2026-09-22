"""Чтение фактических параметров разрешённой выдачи ключей."""

from dataclasses import asdict
from datetime import UTC, datetime

from app.domain.access_entitlement import entitlement_deadline
from app.domain.devices import DEFAULT_DEVICE_LIMIT


async def access_grant(*, pool, internal_user_id: str, snapshots=None) -> dict | None:
    if pool is not None:
        row = await pool.fetchrow(
            "SELECT state_label, active_until_utc, trial_expires_at, keys_deleted_at, device_count"
            " FROM subscription_snapshots WHERE internal_user_id=$1",
            internal_user_id,
        )
    else:
        snapshot = await snapshots.get_for_user(internal_user_id)
        row = asdict(snapshot) if snapshot is not None else None
    deadline = entitlement_deadline(row, now=datetime.now(UTC))
    if deadline is None:
        return None
    return {"device_count": row.get("device_count") or DEFAULT_DEVICE_LIMIT, "expires_at": deadline}
