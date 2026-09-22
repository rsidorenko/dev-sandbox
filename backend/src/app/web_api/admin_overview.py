"""Операционный обзор: согласованный снимок БД без команд VPN."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from starlette.responses import JSONResponse

from app.web_api.admin_attention import attention_snapshot
from app.web_api.admin_auth import _with_admin_auth
from app.web_api.admin_servers import server_snapshot


@_with_admin_auth
async def handle_admin_overview(request):
    now = datetime.now(UTC)
    start = now.astimezone(ZoneInfo("Europe/Moscow")).replace(hour=0, minute=0, second=0, microsecond=0)
    async with request.app.state.pool.acquire() as conn:
        async with conn.transaction(isolation="repeatable_read", readonly=True):
            fleet = await server_snapshot(conn)
            attention = await attention_snapshot(conn, servers=fleet)
            active = await conn.fetchval(
                "SELECT COUNT(*) FROM subscription_snapshots JOIN user_identities USING(internal_user_id) WHERE state_label='active' AND keys_deleted_at IS NULL "
                "AND GREATEST(active_until_utc,trial_expires_at)>$1",
                now,
            )
            new_users = await conn.fetchval(
                "SELECT COUNT(*) FROM user_identities JOIN analytics_eligible_user_ids USING(internal_user_id) WHERE created_at>=$1 AND created_at<$2",
                start,
                now,
            )
            revenue = await conn.fetchrow(
                "SELECT COALESCE(SUM(b.amount_minor_units),0)::bigint amount,COUNT(*) payments, "
                "COUNT(*) FILTER(WHERE b.amount_minor_units IS NULL) unknown_amount "
                "FROM billing_events_ledger b JOIN analytics_eligible_user_ids USING(internal_user_id) "
                "WHERE b.status='accepted' AND b.currency_code='RUB' AND b.event_effective_at>=$1 AND b.event_effective_at<$2 "
                "AND b.event_type IN ('subscription_activated','device_topup') "
                "AND (b.amount_minor_units IS NULL OR b.amount_minor_units>=0) "
                "AND b.internal_fact_ref NOT LIKE 'grant:%' AND b.billing_provider_key<>'manual_grant'",
                start,
                now,
            )
            pending = await conn.fetchval("SELECT COUNT(*) FROM balance_access_jobs WHERE status='pending'")
            rotations = await conn.fetchval("SELECT COUNT(*) FROM user_identities WHERE vless_rotation_pending")
            incidents = await conn.fetchval("SELECT COUNT(*) FROM admin_incidents WHERE status<>'resolved'")
    active_servers = [s for s in fleet["servers"] if s["is_active"]]
    return JSONResponse(
        {
            "ok": True,
            "observed_at": now.isoformat(),
            "day": start.date().isoformat(),
            "active_users": active,
            "new_users": new_users,
            "revenue_kopecks": revenue["amount"],
            "payments": revenue["payments"],
            "unknown_amount_payments": revenue["unknown_amount"],
            "pending_access": pending,
            "pending_rotations": rotations,
            "open_incidents": incidents,
            "hosts": len({s["server_host"] for s in active_servers}),
            "connections": len(active_servers),
            "observations": {
                state: sum(s["health"] == state for s in active_servers)
                for state in ("healthy", "error", "stale", "unknown")
            },
            "attention_total": attention["total"],
            "issues": sorted(attention["issues"], key=lambda issue: issue["severity"] != "critical")[:10],
        }
    )
