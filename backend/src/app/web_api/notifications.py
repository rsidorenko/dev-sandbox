"""Web API notifications — лента веб-уведомлений + клейм бонусов рассылок.

Тонкая точка ввода поверх:
- ``persistence/postgres_web_notifications.py`` (web_notifications, миграция 077);
- claims-таблиц рассылок (broadcast_bonus_claims / broadcast_promo_claims) — клейм
  зеркалит семантику бота (``runtime_facade._handle_broadcast_days_claim`` /
  ``_handle_broadcast_promo_claim``), но различает already_claimed/expired;
- ``application/promo_apply_service.apply_promo_intent`` и
  ``application/subscription_grant.grant_subscription_days`` (shared-ядра).

Веб-сессия (кука ``session``) — ``require_auth``; мутации (read/claim) под CSRF.
tg_id для claims-таблиц берём из JWT claims (у web-only отрицательный — ключ
PK ``(job_id, telegram_user_id)``, сидируется web fan-out'ом рассылки).
"""

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import UTC, datetime
from typing import Any

import asyncpg
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.domain.web_notifications import compose_claim_status
from app.persistence.postgres_web_notifications import (
    PostgresWebNotificationRepository,
    decode_payload,
)
from app.web_api.channel_subscription import require_channel_subscription
from app.web_api.helpers import safe_json_error
from app.web_api.middleware import require_auth

_LOGGER = logging.getLogger(__name__)

_DEFAULT_LIMIT = 20
_MAX_LIMIT = 50
_MAX_READ_IDS = 200

# In-process sliding window для клейма (анти-молотка; real-защита от двойного гранта —
# атомарный UPDATE/идемпотентный apply в БД). Ключ — internal_user_id (НЕ pool).
_CLAIM_RATE_LIMITS: dict[str, deque[float]] = {}
_CLAIM_RATE_MAX = 10
_CLAIM_RATE_WINDOW_SEC = 60.0


def _claim_rate_allow(internal_user_id: str) -> bool:
    try:
        now = time.monotonic()
        queue = _CLAIM_RATE_LIMITS.setdefault(internal_user_id, deque())
        while queue and now - queue[0] > _CLAIM_RATE_WINDOW_SEC:
            queue.popleft()
        if len(queue) >= _CLAIM_RATE_MAX:
            return False
        queue.append(now)
        if len(_CLAIM_RATE_LIMITS) > 10_000:
            stale = [
                key
                for key, values in _CLAIM_RATE_LIMITS.items()
                if not values or now - values[-1] > _CLAIM_RATE_WINDOW_SEC
            ]
            for key in stale:
                _CLAIM_RATE_LIMITS.pop(key, None)
        return True
    except Exception:  # fail-open: сбой лимитера не блокирует клейм
        return True


async def _require_identity(request: Request) -> tuple[asyncpg.Pool, str, int] | JSONResponse:
    """Auth + identity: (pool, internal_user_id, telegram_user_id)."""
    auth_result = await require_auth(request)
    if isinstance(auth_result, JSONResponse):
        return auth_result
    telegram_user_id = auth_result.get("telegram_user_id")
    if telegram_user_id is None:
        return safe_json_error(403, "no_telegram_identity")
    pool: asyncpg.Pool = request.app.state.pool
    identity = await pool.fetchrow(
        "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
        telegram_user_id,
    )
    if identity is None:
        return safe_json_error(404, "identity_not_found")
    return pool, identity["internal_user_id"], int(telegram_user_id)


def _parse_int(value: str | None, default: int, *, minimum: int, maximum: int | None = None) -> int:
    try:
        result = int(value) if value is not None else default
    except (TypeError, ValueError):
        result = default
    result = max(result, minimum)
    if maximum is not None:
        result = min(result, maximum)
    return result


async def _enrich_claim_status(
    pool: asyncpg.Pool,
    repo: PostgresWebNotificationRepository,
    *,
    telegram_user_id: int,
    internal_user_id: str,
    items: list[dict[str, Any]],
) -> None:
    """Проставить payload (decoded) и claim_status бонусным item'ам.

    Источник истины — claims-таблицы и promo_applied; статус нужен фронту, чтобы
    рендерить кнопку «Получить»/чип «Получено»/«Срок истёк» до клика.
    """
    days_job_ids: list[str] = []
    promo_job_ids: list[str] = []
    for item in items:
        payload = decode_payload(item.get("payload"))
        item["payload"] = payload
        bonus = payload.get("bonus")
        job_id = payload.get("job_id")
        if not bonus or not job_id:
            continue
        if bonus.get("type") == "promo":
            promo_job_ids.append(str(job_id))
        else:
            days_job_ids.append(str(job_id))
    if not days_job_ids and not promo_job_ids:
        return

    now = datetime.now(UTC)
    days_claims = await repo.broadcast_days_claims(telegram_user_id, days_job_ids) if days_job_ids else {}
    promo_claims = await repo.broadcast_promo_claims(telegram_user_id, promo_job_ids) if promo_job_ids else {}
    applied_code: str | None = None
    if promo_claims:
        from app.persistence.postgres_promo import PostgresPromoAppliedRepository

        applied = await PostgresPromoAppliedRepository(pool).get(internal_user_id)
        if applied is not None and not applied.is_expired(now):
            applied_code = applied.code

    for item in items:
        payload = item.get("payload") or {}
        bonus = payload.get("bonus")
        job_id = str(payload.get("job_id") or "")
        if not bonus or not job_id:
            continue
        if bonus.get("type") == "promo":
            status = compose_claim_status(
                bonus=bonus,
                promo_claim=promo_claims.get(job_id),
                applied_code=applied_code,
                now=now,
            )
        else:
            status = compose_claim_status(
                bonus=bonus,
                days_claim=days_claims.get(job_id),
                now=now,
            )
        if status is not None:
            item["claim_status"] = status


def _public_payload(item: dict[str, Any]) -> dict[str, Any]:
    """payload для клиента: без bonus.code — код приватен (клейм идёт по claims-таблице
    сервером, пользователю код не нужен; утечка = расшаривание промокода)."""
    payload = item.get("payload") or {}
    bonus = payload.get("bonus")
    if isinstance(bonus, dict) and "code" in bonus:
        payload = dict(payload)
        payload["bonus"] = {k: v for k, v in bonus.items() if k != "code"}
    return payload


def _item_public(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": int(item["id"]),
        "kind": item["kind"],
        "title": item["title"],
        "body": item["body"],
        "payload": _public_payload(item),
        "created_at": item["created_at"].isoformat(),
        "read_at": item["read_at"].isoformat() if item["read_at"] is not None else None,
        **({"claim_status": item["claim_status"]} if "claim_status" in item else {}),
    }


async def handle_list_notifications(request: Request) -> JSONResponse:
    """Лента уведомлений авторизованного пользователя: items + unread_count + has_more."""
    try:
        resolved = await _require_identity(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        pool, internal_user_id, telegram_user_id = resolved

        limit = _parse_int(request.query_params.get("limit"), _DEFAULT_LIMIT, minimum=1, maximum=_MAX_LIMIT)
        offset = _parse_int(request.query_params.get("offset"), 0, minimum=0)

        repo = PostgresWebNotificationRepository(pool)
        # limit+1 — дешёвый способ узнать has_more без отдельного COUNT.
        rows = await repo.list_for_user(internal_user_id, limit=limit + 1, offset=offset)
        has_more = len(rows) > limit
        items = rows[:limit]
        unread_count = await repo.count_unread(internal_user_id)
        await _enrich_claim_status(
            pool,
            repo,
            telegram_user_id=telegram_user_id,
            internal_user_id=internal_user_id,
            items=items,
        )
        return JSONResponse(
            {
                "ok": True,
                "items": [_item_public(item) for item in items],
                "unread_count": unread_count,
                "has_more": has_more,
            }
        )
    except Exception:
        _LOGGER.exception("notifications_list_error")
        return safe_json_error(500, "internal_error")


async def handle_notifications_read(request: Request) -> JSONResponse:
    """Отметить прочитанными: {ids:[…]} или {all:true} → {ok, updated}."""
    try:
        resolved = await _require_identity(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        pool, internal_user_id, _telegram_user_id = resolved

        try:
            data = await request.json()
        except Exception:
            return safe_json_error(400, "invalid_request")

        repo = PostgresWebNotificationRepository(pool)
        if data.get("all") is True:
            updated = await repo.mark_read(internal_user_id, None)
            return JSONResponse({"ok": True, "updated": updated})

        raw_ids = data.get("ids")
        if not isinstance(raw_ids, list) or not raw_ids:
            return safe_json_error(400, "invalid_request")
        try:
            ids = [int(value) for value in raw_ids[:_MAX_READ_IDS]]
        except (TypeError, ValueError):
            return safe_json_error(400, "invalid_request")
        updated = await repo.mark_read(internal_user_id, ids)
        return JSONResponse({"ok": True, "updated": updated})
    except Exception:
        _LOGGER.exception("notifications_read_error")
        return safe_json_error(500, "internal_error")


async def handle_notification_claim(request: Request) -> JSONResponse:
    """Клейм бонуса рассылки (дни/промокод) из веб-уведомления.

    Дни: atomic UPDATE eligible→claimed (единственный победитель при гонке) →
    grant_subscription_days → закрытие trial_used. Промокод: claims-gate +
    apply_promo_intent (идемпотентен через promo_applied PK).
    """
    try:
        resolved = await _require_identity(request)
        if isinstance(resolved, JSONResponse):
            return resolved
        pool, internal_user_id, telegram_user_id = resolved

        try:
            notification_id = int(request.path_params["notification_id"])
        except (KeyError, TypeError, ValueError):
            return safe_json_error(400, "invalid_request")

        if not _claim_rate_allow(internal_user_id):
            return safe_json_error(429, "rate_limited")

        repo = PostgresWebNotificationRepository(pool)
        notification = await repo.get_for_user(notification_id, internal_user_id)
        if notification is None:
            return safe_json_error(404, "not_found")

        payload = decode_payload(notification.get("payload"))
        bonus = payload.get("bonus")
        job_id = str(payload.get("job_id") or "")
        if not isinstance(bonus, dict) or not job_id:
            return safe_json_error(400, "no_bonus")

        subscription_error = await require_channel_subscription(request, telegram_user_id)
        if subscription_error is not None:
            return subscription_error

        if bonus.get("type") == "promo":
            return await _claim_promo(pool, internal_user_id, telegram_user_id, job_id)

        return await _claim_days(request, pool, internal_user_id, telegram_user_id, job_id)
    except Exception:
        _LOGGER.exception("notification_claim_error")
        return safe_json_error(500, "internal_error")


async def _claim_days(
    request: Request,
    pool: asyncpg.Pool,
    internal_user_id: str,
    telegram_user_id: int,
    job_id: str,
) -> JSONResponse:
    """Бонус-дни: зеркало _handle_broadcast_days_claim (бот), с различением отказов."""
    row = await pool.fetchrow(
        "UPDATE broadcast_bonus_claims SET status='claimed', claimed_at=NOW() "
        "WHERE job_id=$1 AND telegram_user_id=$2 AND status='eligible' AND expires_at > NOW() "
        "RETURNING days",
        job_id,
        telegram_user_id,
    )
    if row is None:
        state = await pool.fetchrow(
            "SELECT status FROM broadcast_bonus_claims WHERE job_id=$1 AND telegram_user_id=$2",
            job_id,
            telegram_user_id,
        )
        if state is not None and state["status"] == "claimed":
            return safe_json_error(409, "already_claimed")
        # истёк или не получатель — единый отказ, не раскрываем деталей.
        return safe_json_error(410, "expired")

    days = int(row["days"])
    from app.application.subscription_grant import grant_subscription_days

    result = await grant_subscription_days(
        pool=pool,
        vless_provider=getattr(request.app.state, "vless_provider", None),
        internal_user_id=internal_user_id,
        days=days,
        actor=f"broadcast_claim_web:{job_id}",
        correlation_id=f"bcw:{job_id[:8]}:{telegram_user_id}",
    )

    # Закрыть trial-квоту (бонус ≥ 3 дней ≥ триала — см. комментарий в bot-клейме).
    try:
        await pool.execute(
            "UPDATE user_identities SET trial_used = TRUE "
            "WHERE internal_user_id = $1 AND (trial_used IS NULL OR trial_used = FALSE)",
            internal_user_id,
        )
    except Exception:
        _LOGGER.exception("web claim: failed to close trial_used user=%s job=%s", internal_user_id[:8], job_id[:8])

    return JSONResponse(
        {
            "ok": True,
            "status": "ok",
            "days": days,
            "new_active_until": result.new_active_until.date().isoformat(),
            "provisioning": result.provisioning,
        }
    )


async def _claim_promo(
    pool: asyncpg.Pool,
    internal_user_id: str,
    telegram_user_id: int,
    job_id: str,
) -> JSONResponse:
    """Промокод-бонус: зеркало _handle_broadcast_promo_claim (бот)."""
    from app.application.promo_apply_service import apply_promo_intent
    from app.persistence.postgres_promo import (
        PostgresPromoAppliedRepository,
        PostgresPromoCodeRepository,
        PostgresPromoRedemptionRepository,
    )

    # Gate: строка claims доказывает «получатель»; срок проверяем после pre-check'а,
    # чтобы повторный клик после успешного применения оставался «уже получено».
    gate = await pool.fetchrow(
        "SELECT code, expires_at FROM broadcast_promo_claims WHERE job_id=$1 AND telegram_user_id=$2",
        job_id,
        telegram_user_id,
    )
    if gate is None:
        return safe_json_error(410, "expired")
    code = str(gate["code"])

    now = datetime.now(UTC)
    applied_repo = PostgresPromoAppliedRepository(pool)
    applied = await applied_repo.get(internal_user_id)
    if applied is not None and not applied.is_expired(now) and applied.code == code:
        return JSONResponse({"ok": True, "status": "already_claimed", "code": code})

    if gate["expires_at"] is not None and gate["expires_at"] <= now:
        return safe_json_error(410, "expired")

    outcome = await apply_promo_intent(
        pool=pool,
        promo_code_repo=PostgresPromoCodeRepository(pool),
        promo_redemption_repo=PostgresPromoRedemptionRepository(pool),
        promo_applied_repo=applied_repo,
        snapshots=None,
        internal_user_id=internal_user_id,
        raw_code=code,
        now=now,
    )
    if outcome.status == "ok":
        return JSONResponse({"ok": True, "status": "ok", "code": outcome.code})
    if outcome.status == "already_used":
        # once-код уже погашен этим пользователем (успел применить до рассылки).
        return safe_json_error(409, "already_claimed")
    if outcome.status == "ineligible":
        return safe_json_error(409, "ineligible")
    # invalid/not_found/inactive — не раскрываем существование кода.
    return safe_json_error(404, "not_found")
