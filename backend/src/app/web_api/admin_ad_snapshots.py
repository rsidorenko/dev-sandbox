"""Неизменяемые накопительные срезы Telegram Ads с исходной валютой."""

from __future__ import annotations

import json
import re
from datetime import date
from decimal import Decimal

from starlette.requests import Request
from starlette.responses import JSONResponse

from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error


def _text(value: object, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("invalid_text")
    return value.strip()


def _money(value: object) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"\d{1,9}(?:\.\d{1,9})?", value):
        raise ValueError("invalid_money")
    return format(Decimal(value), "f")


def validate_snapshot(data: object) -> dict:
    if not isinstance(data, dict):
        raise ValueError("invalid_snapshot")
    source_ref = _text(data.get("source_ref"), 120)
    if not re.fullmatch(r"[a-zA-Z0-9_-]+", source_ref):
        raise ValueError("invalid_source_ref")
    observed = data.get("observed_date")
    if observed is not None:
        observed = date.fromisoformat(_text(observed)).isoformat()
    rate = _money(data.get("usd_per_ton"))
    if Decimal(rate) <= 0:
        raise ValueError("invalid_rate")
    sources = data.get("sources")
    if not isinstance(sources, list) or not 1 <= len(sources) <= 20:
        raise ValueError("invalid_sources")
    normalized_sources = []
    for item in sources:
        if not isinstance(item, dict) or not re.fullmatch(r"[0-9a-f]{64}", str(item.get("sha256"))):
            raise ValueError("invalid_source")
        normalized_sources.append({"name": _text(item.get("name")), "sha256": item["sha256"]})
    placements = data.get("placements")
    if not isinstance(placements, list) or not 1 <= len(placements) <= 200:
        raise ValueError("invalid_placements")
    result = []
    seen = set()
    for row in placements:
        if not isinstance(row, dict):
            raise ValueError("invalid_placement")
        title = _text(row.get("title"))
        if title.casefold() in seen:
            raise ValueError("duplicate_placement")
        seen.add(title.casefold())
        entry = {"title": title}
        for key in ("views", "clicks", "actions"):
            value = row.get(key)
            if type(value) is not int or not 0 <= value <= 10**12:
                raise ValueError("invalid_count")
            entry[key] = value
        entry["cpm_ton"] = _money(row.get("cpm_ton"))
        for key in ("spent_ton_lines", "budget_ton_lines"):
            values = row.get(key)
            if not isinstance(values, list) or not 1 <= len(values) <= 5:
                raise ValueError("invalid_money_lines")
            entry[key] = [_money(v) for v in values]
        for key in ("target", "status", "displayed_date", "note"):
            entry[key] = _text(row.get(key), 500)
        result.append(entry)
    return {
        "source_ref": source_ref,
        "observed_date": observed,
        "usd_per_ton": rate,
        "notes": _text(data.get("notes"), 2000),
        "sources": normalized_sources,
        "placements": result,
    }


def snapshot_json(row) -> dict:
    payload = row["payload"]
    data = json.loads(payload) if isinstance(payload, str) else dict(payload)
    rate = Decimal(data["usd_per_ton"])
    spent = sum((Decimal(v) for r in data["placements"] for v in r["spent_ton_lines"]), Decimal(0))
    return {
        **data,
        "created_at": row["created_at"].isoformat(),
        "totals": {
            **{key: sum(r[key] for r in data["placements"]) for key in ("views", "clicks", "actions")},
            "spent_ton": format(spent, "f"),
            "spent_usd": format(spent * rate, ".2f"),
        },
    }


@_with_admin_auth
async def handle_admin_get_ad_snapshot(request: Request) -> JSONResponse:
    row = await request.app.state.pool.fetchrow(
        "SELECT payload, created_at FROM telegram_ads_snapshots ORDER BY created_at DESC, source_ref DESC LIMIT 1"
    )
    return JSONResponse({"ok": True, "snapshot": snapshot_json(row) if row else None})


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_import_ad_snapshot(request: Request) -> JSONResponse:
    body = await request.body()
    if len(body) > 300_000:
        return safe_json_error(413, "snapshot_too_large")
    try:
        data = validate_snapshot(json.loads(body))
    except (ValueError, TypeError):
        return safe_json_error(400, "invalid_snapshot")
    pool = request.app.state.pool
    async with pool.acquire() as conn, conn.transaction():
        row = await conn.fetchrow(
            "INSERT INTO telegram_ads_snapshots (source_ref, payload, created_by) VALUES ($1, $2::jsonb, $3) "
            "ON CONFLICT (source_ref) DO NOTHING RETURNING payload, created_at",
            data["source_ref"],
            json.dumps(data),
            _admin_actor(request),
        )
        created = row is not None
        if row is None:
            row = await conn.fetchrow(
                "SELECT payload, created_at FROM telegram_ads_snapshots WHERE source_ref = $1", data["source_ref"]
            )
            stored = row["payload"]
            if (json.loads(stored) if isinstance(stored, str) else stored) != data:
                return safe_json_error(409, "snapshot_source_conflict")
    return JSONResponse({"ok": True, "created": created, "snapshot": snapshot_json(row)})
