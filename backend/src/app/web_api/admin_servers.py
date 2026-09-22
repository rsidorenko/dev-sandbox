"""Читающий мониторинг серверов: наблюдения коллектора, не обещание работы туннеля."""

import os
from datetime import UTC, datetime

from starlette.responses import JSONResponse

from app.web_api.admin_auth import _with_admin_auth


def observation_status(status, checked_at, *, now, stale_after):
    if checked_at is None:
        return "unknown"
    if (now - checked_at).total_seconds() > stale_after:
        return "stale"
    return "healthy" if status == "success" else "error"


async def server_snapshot(pool):
    now = datetime.now(UTC)
    try:
        stale_after = max(900, int(os.environ.get("TRAFFIC_STATS_INTERVAL_SECONDS", "300")) * 3)
    except ValueError:
        stale_after = 900
    rows = await pool.fetch(
        """SELECT v.id,v.label,v.country_code,v.server_host,v.server_port,v.transport_type,
        v.provider_kind,v.client_surface,v.is_active,o.status,o.checked_at,o.last_success_at,
        (SELECT MAX(t.collected_at) FROM bare_xray_traffic_state t WHERE t.server_id=v.id) traffic_collected_at,
        COALESCE(d.bytes,0)::bigint traffic_bytes,COALESCE(d.keys,0)::int traffic_keys
        FROM vpn_servers v LEFT JOIN vpn_server_observations o ON o.server_id=v.id
        LEFT JOIN (
          SELECT server_id,SUM(uplink_bytes+downlink_bytes) bytes,
          COUNT(DISTINCT email) FILTER (WHERE uplink_bytes+downlink_bytes>0) keys
          FROM bare_xray_traffic_daily WHERE stat_date=(NOW() AT TIME ZONE 'Europe/Moscow')::date
          GROUP BY server_id
        ) d ON d.server_id=v.id ORDER BY v.server_host,v.id"""
    )
    servers = []
    for row in rows:
        item = dict(row)
        item["health"] = observation_status(row["status"], row["checked_at"], now=now, stale_after=stale_after)
        for key in ("checked_at", "last_success_at", "traffic_collected_at"):
            item[key] = item[key].isoformat() if item[key] else None
        servers.append(item)
    return {
        "ok": True,
        "observed_at": now.isoformat(),
        "source": "StatsService / БД",
        "stale_after_seconds": stale_after,
        "servers": servers,
    }


@_with_admin_auth
async def handle_admin_servers(request):
    return JSONResponse(await server_snapshot(request.app.state.pool))
