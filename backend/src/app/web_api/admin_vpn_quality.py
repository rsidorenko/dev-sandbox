"""Читающая история серверных HTTPS-измерений, отдельно от клиентских /check."""

from datetime import datetime

from starlette.responses import JSONResponse

from app.web_api.admin_auth import _with_admin_auth


def serialize(rows):
    return [
        {key: value.isoformat() if isinstance(value, datetime) else value for key, value in dict(row).items()}
        for row in rows
    ]


@_with_admin_auth
async def handle_admin_vpn_quality(request):
    pool = request.app.state.pool
    locations = await pool.fetch("""SELECT a.location,a.enabled,s.active_until_utc,
        r.started_at,r.finished_at,r.status,r.reason FROM vpn_quality_accounts a
        LEFT JOIN subscription_snapshots s USING(internal_user_id)
        LEFT JOIN LATERAL (SELECT started_at,finished_at,status,reason FROM vpn_quality_runs
          WHERE location=a.location ORDER BY started_at DESC,id DESC LIMIT 1) r ON TRUE
        ORDER BY a.location LIMIT 32""")
    profiles = await pool.fetch("""SELECT r.location,s.server_id,s.route,s.label,
        COUNT(*)::int attempts, COUNT(*) FILTER(WHERE s.result='ok')::int verified,
        COUNT(*) FILTER(WHERE s.result='route_unknown')::int unverified,
        COUNT(*) FILTER(WHERE s.result='measurement_error')::int measurement_errors,
        COUNT(*) FILTER(WHERE s.result NOT IN ('ok','route_unknown','measurement_error'))::int errors,
        COUNT(*) FILTER(WHERE s.ttfb_ms>1000 AND s.result IN ('ok','route_unknown'))::int slow,
        percentile_cont(0.5) WITHIN GROUP(ORDER BY s.ttfb_ms) FILTER(WHERE s.result IN ('ok','route_unknown')) p50_ms,
        percentile_cont(0.95) WITHIN GROUP(ORDER BY s.ttfb_ms) FILTER(WHERE s.result IN ('ok','route_unknown')) p95_ms,
        MAX(r.started_at) last_checked_at
        FROM vpn_quality_samples s JOIN vpn_quality_runs r ON r.id=s.run_id
        WHERE r.started_at>NOW()-INTERVAL '24 hours'
        GROUP BY r.location,s.server_id,s.route,s.label ORDER BY r.location,s.server_id,s.route LIMIT 1024""")
    recent = await pool.fetch("""SELECT r.location,r.started_at,s.server_id,s.route,s.label,s.result,
        s.http_code,s.tls_ms,s.ttfb_ms,s.total_ms FROM vpn_quality_samples s
        JOIN vpn_quality_runs r ON r.id=s.run_id WHERE r.started_at>NOW()-INTERVAL '24 hours'
        ORDER BY r.started_at DESC,r.id DESC,s.server_id,s.route LIMIT 100""")
    return JSONResponse(
        {
            "ok": True,
            "window_hours": 24,
            "stale_after_seconds": 900,
            "locations": serialize(locations),
            "profiles": serialize(profiles),
            "recent": serialize(recent),
        }
    )
