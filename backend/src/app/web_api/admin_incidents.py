"""Журнал инцидентов: отдельные транзакции, без операций с VPN и оплатой."""

import hashlib
import json
from datetime import datetime
from uuid import UUID

from starlette.responses import JSONResponse

from app.web_api.admin_attention import attention_snapshot
from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error

_AUDIT = """
WITH events AS (
 SELECT 'subscription:'||id id,created_at,actor,internal_user_id,'add_days' action,
 'Добавлено дней: '||days::text detail FROM admin_subscription_actions
 UNION ALL SELECT 'reset:'||id,created_at,actor,internal_user_id,'reset',reason FROM admin_user_reset_actions
 UNION ALL SELECT 'internal:'||id,created_at,actor,internal_user_id,'internal',
 CASE WHEN new_value THEN 'Установлена тестовая метка' ELSE 'Снята тестовая метка' END FROM analytics_internal_audit
 UNION ALL SELECT 'pricing:'||id,created_at,actor,NULL,action,COALESCE(plan_id,'Все тарифы') FROM admin_pricing_actions
 UNION ALL SELECT 'adm02:'||audit_event_id,created_at,principal_marker,NULL,'ensure_access',outcome_bucket FROM adm02_ensure_access_audit_events
) SELECT * FROM events WHERE ($1='' OR POSITION($1 IN COALESCE(internal_user_id,''))>0)
ORDER BY created_at DESC,id DESC LIMIT 100
"""


def _json_row(row):
    return {
        key: value.isoformat() if isinstance(value, datetime) else str(value) if isinstance(value, UUID) else value
        for key, value in dict(row).items()
    }


@_with_admin_auth
async def handle_admin_incidents(request):
    pool = request.app.state.pool
    snapshot = await attention_snapshot(pool)
    rows = await pool.fetch(
        "SELECT id,source_key,title,status,version,created_at,updated_at,created_by FROM admin_incidents ORDER BY (status='resolved'),updated_at DESC,id LIMIT 100"
    )
    events = await pool.fetch(
        "SELECT e.* FROM unnest($1::uuid[]) AS target(id) CROSS JOIN LATERAL (SELECT id,incident_id,actor,action,note,created_at FROM admin_incident_events WHERE incident_id=target.id ORDER BY id DESC LIMIT 50) e ORDER BY e.id DESC",
        [r["id"] for r in rows],
    )
    audit = await pool.fetch(_AUDIT, request.query_params.get("user", "")[:200])
    return JSONResponse(
        {
            "ok": True,
            **snapshot,
            "issues": snapshot["issues"][:200],
            "incidents": [_json_row(r) for r in rows],
            "events": [_json_row(r) for r in events],
            "audit": [_json_row(r) for r in audit],
        }
    )


def _valid_text(value, maximum, *, required=False):
    return (
        isinstance(value, str)
        and len(value) <= maximum
        and (bool(value.strip()) or not required)
        and not any(ord(c) < 32 and c not in "\n\t" for c in value)
    )


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_incident_create(request):
    try:
        body = await request.json()
        if not isinstance(body, dict):
            raise ValueError
        incident_id = UUID(body.get("request_id", ""))
    except (ValueError, TypeError, AttributeError):
        return safe_json_error(400, "invalid_request")
    title, note = body.get("title", ""), body.get("note", "")
    if not _valid_text(title, 160, required=True) or not _valid_text(note, 1000):
        return safe_json_error(400, "invalid_text")
    pool = request.app.state.pool
    source = body.get("source_key")
    if source is not None and (not isinstance(source, str) or len(source) != 64):
        return safe_json_error(400, "invalid_source")
    actor = _admin_actor(request)
    fingerprint = hashlib.sha256(
        json.dumps([actor, source, title.strip(), note.strip()], ensure_ascii=False).encode()
    ).hexdigest()
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1,0))", f"admin-incident:{incident_id}")
            existing = await conn.fetchrow(
                "SELECT id,request_fingerprint FROM admin_incidents WHERE id=$1", incident_id
            )
            if existing is not None:
                if existing["request_fingerprint"] != fingerprint:
                    return safe_json_error(409, "request_conflict")
                return JSONResponse({"ok": True, "id": str(existing["id"]), "duplicate": True})
            if source is not None:
                issues = (await attention_snapshot(conn))["issues"]
                issue = next((i for i in issues if i["key"] == source), None)
                if issue is None:
                    return safe_json_error(409, "source_no_longer_present")
                title = issue["title"][:160]
            row = await conn.fetchrow(
                "INSERT INTO admin_incidents(id,source_key,title,created_by,request_fingerprint) VALUES($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING RETURNING id",
                incident_id,
                source,
                title.strip(),
                actor,
                fingerprint,
            )
            if row is None:
                existing = await conn.fetchrow(
                    "SELECT id FROM admin_incidents WHERE source_key=$1 AND status<>'resolved'", source
                )
                if existing is None:
                    return safe_json_error(409, "incident_conflict")
                return JSONResponse({"ok": True, "id": str(existing["id"]), "duplicate": True})
            await conn.execute(
                "INSERT INTO admin_incident_events(incident_id,actor,action,note) VALUES($1,$2,'create',$3)",
                incident_id,
                actor,
                note.strip(),
            )
    return JSONResponse({"ok": True, "id": str(incident_id)})


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_incident_update(request):
    try:
        body = await request.json()
        incident_id = UUID(request.path_params["incident_id"])
        if not isinstance(body, dict) or type(body.get("version")) is not int:
            raise ValueError
    except (ValueError, TypeError, AttributeError):
        return safe_json_error(400, "invalid_request")
    action, note = body.get("action"), body.get("note", "")
    if (
        not isinstance(action, str)
        or action not in {"ack", "resolve", "note"}
        or not _valid_text(note, 1000, required=action in {"resolve", "note"})
    ):
        return safe_json_error(400, "invalid_action")
    pool = request.app.state.pool
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT status,version,source_key FROM admin_incidents WHERE id=$1 FOR UPDATE", incident_id
            )
            if row is None:
                return safe_json_error(404, "incident_not_found")
            if row["version"] != body["version"]:
                return safe_json_error(409, "version_conflict")
            if action == "resolve" and row["source_key"] is not None:
                active_sources = {i["key"] for i in (await attention_snapshot(conn))["issues"]}
                if row["source_key"] in active_sources:
                    return safe_json_error(409, "condition_still_present")
            if (action == "ack" and row["status"] != "open") or (action == "resolve" and row["status"] == "resolved"):
                return safe_json_error(409, "invalid_transition")
            status = "ack" if action == "ack" else "resolved" if action == "resolve" else row["status"]
            await conn.execute(
                "UPDATE admin_incidents SET status=$2,version=version+1,updated_at=NOW() WHERE id=$1",
                incident_id,
                status,
            )
            await conn.execute(
                "INSERT INTO admin_incident_events(incident_id,actor,action,note) VALUES($1,$2,$3,$4)",
                incident_id,
                _admin_actor(request),
                action,
                note.strip(),
            )
    return JSONResponse({"ok": True})
