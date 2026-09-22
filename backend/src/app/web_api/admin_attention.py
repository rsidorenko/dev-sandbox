"""Сигналы внимания по сохранённым фактам, без исправляющих действий."""

import hashlib
from datetime import UTC, datetime

from app.web_api.admin_servers import server_snapshot


def _issue(kind, ref, title, user=None, severity="warning"):
    return {
        "key": hashlib.sha256(f"{kind}:{ref}".encode()).hexdigest(),
        "kind": kind,
        "title": title,
        "internal_user_id": user,
        "severity": severity,
    }


async def attention_snapshot(pool):
    issues = []
    for row in await pool.fetch(
        "SELECT internal_user_id,COUNT(*) count FROM balance_access_jobs WHERE status='pending' AND created_at<NOW()-INTERVAL '10 minutes' GROUP BY internal_user_id"
    ):
        uid = row["internal_user_id"]
        issues.append(_issue("balance", uid, f"Выдача после балансоплаты задерживается: {row['count']} задач", uid))
    for row in await pool.fetch("SELECT internal_user_id FROM user_identities WHERE vless_rotation_pending"):
        uid = row["internal_user_id"]
        issues.append(_issue("rotation", uid, "Ротация ключей не завершена", uid))
    review_rows = await pool.fetch(
        "SELECT internal_fact_ref,internal_user_id FROM billing_subscription_apply_records WHERE apply_outcome='needs_review'"
    )
    issues.extend(
        _issue("payment", row["internal_fact_ref"], "Применение оплаты требует проверки", row["internal_user_id"])
        for row in review_rows
    )
    servers = await server_snapshot(pool)
    for server in servers["servers"]:
        if server["is_active"] and server["provider_kind"] == "bare_xray" and server["health"] in {"error", "stale"}:
            label = "Ошибка опроса статистики" if server["health"] == "error" else "Статистика устарела"
            issues.append(_issue("server", server["id"], f"{server['label']}: {label}"))
    now = datetime.now(UTC)
    for row in await pool.fetch("SELECT name,not_after FROM certificate_expiry_state WHERE not_after IS NOT NULL"):
        days = (row["not_after"] - now).days
        if days <= 14:
            issues.append(
                _issue(
                    "certificate",
                    row["name"],
                    f"{row['name']}: последний известный сертификат — осталось {days} дн.",
                    severity="critical" if days <= 0 else "warning",
                )
            )
    return {"observed_at": now.isoformat(), "issues": issues, "total": len(issues)}
