"""Админ-эндпоинты вкладки «Пользователи»: список (пагинация + поиск) и карточка."""

from __future__ import annotations

import logging
import math
import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.plans import DEFAULT_DEVICE_LIMIT
from app.domain.status_view import remaining_days
from app.issuance.vless_provider import VlessProviderOutcome
from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

_DEFAULT_LIMIT = 50
_MAX_LIMIT = 1000

# Разумные границы админ-гранта дней (без смены тарифа).
_ADD_DAYS_MIN = 1
_ADD_DAYS_MAX = 3650

# Полные SQL-строки как неявная конкатенация литералов (без '+' и интерполяции) —
# как в postgres_referral; в fetch передаётся по имени, поэтому S608 не срабатывает.

_LIST_SEARCH = (
    "SELECT i.internal_user_id, i.telegram_user_id, COALESCE(i.trial_used, FALSE) AS trial_used, "
    "s.state_label, s.active_until_utc, s.plan_id, s.device_count, "
    "COALESCE(rb.balance_kopecks, 0) AS balance_kopecks, ue.email "
    "FROM user_identities i "
    "LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
    "LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "WHERE (ue.email ILIKE '%' || $1 || '%' OR CAST(i.telegram_user_id AS TEXT) LIKE $1 || '%') "
    "ORDER BY i.telegram_user_id DESC LIMIT $2 OFFSET $3"
)

_LIST_NO_SEARCH = (
    "SELECT i.internal_user_id, i.telegram_user_id, COALESCE(i.trial_used, FALSE) AS trial_used, "
    "s.state_label, s.active_until_utc, s.plan_id, s.device_count, "
    "COALESCE(rb.balance_kopecks, 0) AS balance_kopecks, ue.email "
    "FROM user_identities i "
    "LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
    "LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "ORDER BY i.telegram_user_id DESC LIMIT $1 OFFSET $2"
)

_COUNT_SEARCH = (
    "SELECT COUNT(*) FROM user_identities i "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "WHERE (ue.email ILIKE '%' || $1 || '%' OR CAST(i.telegram_user_id AS TEXT) LIKE $1 || '%')"
)

_GET_BY_ID = (
    "SELECT i.internal_user_id, i.telegram_user_id, COALESCE(i.trial_used, FALSE) AS trial_used, "
    "s.state_label, s.active_until_utc, s.plan_id, s.device_count, "
    "COALESCE(rb.balance_kopecks, 0) AS balance_kopecks, ue.email "
    "FROM user_identities i "
    "LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
    "LEFT JOIN referral_balances rb ON rb.internal_user_id = i.internal_user_id "
    "LEFT JOIN LATERAL ("
    "SELECT email FROM user_emails WHERE telegram_user_id = i.telegram_user_id "
    "ORDER BY verified_at DESC NULLS LAST LIMIT 1"
    ") ue ON TRUE "
    "WHERE i.internal_user_id = $1"
)


def _row_to_user(row: asyncpg.Record) -> dict:
    active_until = row["active_until_utc"]
    return {
        "internal_user_id": row["internal_user_id"],
        "telegram_user_id": row["telegram_user_id"],
        "email": row["email"],
        "trial_used": bool(row["trial_used"]),
        "subscription_state": row["state_label"],
        "is_active": bool(
            row["state_label"] == "active" and active_until is not None and active_until > datetime.now(UTC)
        ),
        "active_until": active_until.isoformat() if active_until else None,
        "remaining_days": remaining_days(active_until) if active_until else None,
        "plan_id": row["plan_id"],
        "device_count": row["device_count"],
        "balance_rubles": round((row["balance_kopecks"] or 0) / 100, 2),
    }


def _parse_limit_offset(request: Request) -> tuple[int, int]:
    try:
        limit = max(1, min(_MAX_LIMIT, int(request.query_params.get("limit", _DEFAULT_LIMIT))))
    except ValueError:
        limit = _DEFAULT_LIMIT
    try:
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError:
        offset = 0
    return limit, offset


@_with_admin_auth
async def handle_admin_list_users(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    limit, offset = _parse_limit_offset(request)
    search = (request.query_params.get("search") or "").strip()

    if search:
        rows = await pool.fetch(_LIST_SEARCH, search, limit, offset)
        total = await pool.fetchval(_COUNT_SEARCH, search)
    else:
        rows = await pool.fetch(_LIST_NO_SEARCH, limit, offset)
        total = await pool.fetchval("SELECT COUNT(*) FROM user_identities i")

    return JSONResponse(
        {
            "ok": True,
            "users": [_row_to_user(r) for r in rows],
            "limit": limit,
            "offset": offset,
            "total": int(total or 0),
        }
    )


@_with_admin_auth
async def handle_admin_get_user(request: Request) -> JSONResponse:
    pool: asyncpg.Pool = request.app.state.pool
    internal_user_id = request.path_params["internal_user_id"]

    row = await pool.fetchrow(_GET_BY_ID, internal_user_id)
    if row is None:
        return safe_json_error(404, "user_not_found")

    redemptions = await pool.fetch(
        "SELECT code, base_rubles, discounted_rubles, discount_kopecks, payment_ref, created_at "
        "FROM promo_redemptions WHERE internal_user_id = $1 ORDER BY created_at DESC LIMIT 50",
        internal_user_id,
    )

    return JSONResponse(
        {
            "ok": True,
            "user": _row_to_user(row),
            "promo_redemptions": [
                {
                    "code": r["code"],
                    "base_rubles": r["base_rubles"],
                    "discounted_rubles": r["discounted_rubles"],
                    "discount_kopecks": r["discount_kopecks"],
                    "payment_ref": r["payment_ref"],
                    "created_at": r["created_at"].isoformat() if r["created_at"] else None,
                }
                for r in redemptions
            ],
        }
    )


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_add_days(request: Request) -> JSONResponse:
    """Операторский грант дней подписки: продление ИЛИ создание «с нуля».

    Работает для ЛЮБОГО статуса — главное требование: «ввёл дни → грант, без разницы
    какой статус, подписка активируется, ключи появляются на всех серверах».

    Два режима:
      * extend (есть снапшот) — продлевает active_until_utc (от текущего срока, либо
        от now если истёкла/отменена), реактивирует (state='active', keys_*_at=NULL).
        ИНВАРИАНТ: plan_id и device_count НЕ меняются.
      * create (нет снапшота — никогда не активировал) — создаёт подписку с нуля:
        plan_id = 'custom:{days}' (метка «N дней»), device_count = DEFAULT_DEVICE_LIMIT,
        state='active', active_until = now + days. Аналог триала /start, но платный срок.

    Оба режима перепровижинят VLESS-клиента на всех серверах (через app.state.vless_provider;
    create_user идемпотентен и использует детерминированный per-transport UUID —
    существующие ключи не сбрасываются, отсутствующие создаются). Аудит — в
    admin_subscription_actions (бесплатный грант оператора, НЕ в billing_events_ledger,
    чтобы не искажать выручку/сверку).
    """
    pool: asyncpg.Pool = request.app.state.pool
    internal_user_id = request.path_params["internal_user_id"]

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")

    try:
        days = int(data.get("days"))
    except (TypeError, ValueError):
        return safe_json_error(400, "invalid_days", "days must be an integer")
    if not (_ADD_DAYS_MIN <= days <= _ADD_DAYS_MAX):
        return safe_json_error(400, "invalid_days", f"days must be between {_ADD_DAYS_MIN} and {_ADD_DAYS_MAX}")

    # Цель должна существовать (снапшот НЕ обязателен — см. режим create ниже).
    ident = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    if ident is None:
        return safe_json_error(404, "user_not_found")

    snap = await pool.fetchrow(
        "SELECT state_label, plan_id, device_count, active_until_utc, "
        "keys_deactivated_at, keys_deleted_at FROM subscription_snapshots "
        "WHERE internal_user_id = $1",
        internal_user_id,
    )

    now = datetime.now(UTC)
    created = snap is None
    if created:
        # Создание «с нуля»: зарегистрирован, но никогда не активировал подписку/триал.
        # Даём тариф «N дней» (custom:N) на DEFAULT_DEVICE_LIMIT устройств — аналог
        # триала /start, но с произвольным сроком.
        base = None
        plan_id_for_write = f"custom:{days}"
        device_count_for_write = DEFAULT_DEVICE_LIMIT
    else:
        base = snap["active_until_utc"]
        # ИНВАРИАНТ: plan_id / device_count существующего НЕ меняем.
        plan_id_for_write = snap["plan_id"]
        device_count_for_write = snap["device_count"] or DEFAULT_DEVICE_LIMIT

    # Активную — продлеваем от текущего срока; истёкшую/отменённую/новую — от now.
    start = base if (base is not None and base > now) else now
    new_until = start + timedelta(days=days)

    # UPSERT: на INSERT создаёт (custom:N / DEFAULT_DEVICE_LIMIT для нового), на
    # конфликт — продлевает/реактивирует, СОХРАНЯЯ plan_id/device_count существующего
    # (ссылки на subscription_snapshots.<col> в DO UPDATE — как в
    # postgres_subscription_snapshot._UPSERT_STATE).
    await pool.execute(
        """INSERT INTO subscription_snapshots
               (internal_user_id, state_label, active_until_utc, plan_id, device_count)
           VALUES ($1, 'active', $2, $3, $4)
           ON CONFLICT (internal_user_id) DO UPDATE
           SET state_label = 'active',
               active_until_utc = EXCLUDED.active_until_utc,
               keys_deactivated_at = NULL,
               keys_deleted_at = NULL,
               plan_id = subscription_snapshots.plan_id,
               device_count = subscription_snapshots.device_count,
               updated_at = NOW()""",
        internal_user_id,
        new_until,
        plan_id_for_write,
        device_count_for_write,
    )

    # Перепровижининг VLESS-клиента на всех серверах с новым сроком — идентично
    # платёжной активации: create_user если ключей нет/удалены/активны, activate_user
    # если деактивированы. create_user идемпотентен (add-or-update, детерминированный
    # per-transport UUID) — существующие ключи не сбрасываются, отсутствующие создаются.
    # Best-effort: сбой панели не откатывает изменение в БД — логируем и возвращаем счётчик.
    expiry_days = max(1, math.ceil((new_until - now).total_seconds() / 86_400))
    provisioning = "skipped"
    servers_ok = 0
    vless_provider = getattr(request.app.state, "vless_provider", None)
    try:
        if vless_provider is None:
            provisioning = "skipped_no_provider"
        else:
            if created or snap["keys_deleted_at"] is not None or snap["keys_deactivated_at"] is None:
                pres = await vless_provider.create_user(
                    internal_user_id=internal_user_id,
                    device_count=device_count_for_write,
                    expiry_days=expiry_days,
                )
            else:
                pres = await vless_provider.activate_user(
                    internal_user_id=internal_user_id,
                    device_count=device_count_for_write,
                    expiry_days=expiry_days,
                )
            if pres.outcome == VlessProviderOutcome.SUCCESS:
                servers_ok = len(pres.config.servers) if pres.config is not None else 0
                provisioning = f"success servers={servers_ok}"
            else:
                provisioning = f"outcome={pres.outcome}"
    except Exception:
        _LOGGER.exception("admin_add_days provisioning failed user=%s", internal_user_id[:8])
        provisioning = "failed"

    # Аудит бесплатного гранта оператора (отдельно от billing — не искажает выручку).
    # old_active_until=NULL для created; plan_id/device_count — эффективные значения.
    correlation_id = f"add-days-{uuid.uuid4().hex[:12]}"
    try:
        await pool.execute(
            """INSERT INTO admin_subscription_actions
               (actor, internal_user_id, action, days, old_active_until, new_active_until,
                plan_id, device_count, provisioning, correlation_id)
               VALUES ($1,$2,'add_days',$3,$4,$5,$6,$7,$8,$9)""",
            _admin_actor(request),
            internal_user_id,
            days,
            base,
            new_until,
            plan_id_for_write,
            device_count_for_write,
            provisioning,
            correlation_id,
        )
    except Exception:
        _LOGGER.exception("admin_add_days audit insert failed user=%s", internal_user_id[:8])

    # Свежее состояние для ответа (remaining_days / is_active пересчитаны).
    row = await pool.fetchrow(_GET_BY_ID, internal_user_id)
    user = _row_to_user(row) if row else None

    return JSONResponse(
        {
            "ok": True,
            "user": user,
            "created": created,
            "days_added": days,
            "old_active_until": base.isoformat() if base is not None else None,
            "new_active_until": new_until.isoformat(),
            "plan_unchanged": plan_id_for_write,
            "device_count_unchanged": device_count_for_write,
            "provisioning": provisioning,
            "servers_ok": servers_ok,
            "correlation_id": correlation_id,
        }
    )
