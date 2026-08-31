"""PostgreSQL-хранилище веб-уведомлений (web_notifications, миграция 077).

Шаблон зеркалирует postgres_pricing.py: ctor принимает pool, SQL — статические
литералы, JSONB пишется через ::jsonb-каст (кодек не зарегистрирован) и читается
как текст — разбор через decode_payload на стороне вызова.

Потребители: web fan-out рассылок (admin_broadcast), веб-канал scheduler'а,
пользовательские эндпоинты web_api/notifications.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg

_LIST_COLUMNS = "id, kind, title, body, payload, created_at, read_at"


def decode_payload(raw: Any) -> dict[str, Any]:
    """JSONB-строка от asyncpg → dict (без кодека jsonb приходит как text)."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        decoded = json.loads(str(raw))
    except (TypeError, ValueError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _insert_count(status: str) -> int:
    """'INSERT 0 N' → N (число вставленных строк)."""
    try:
        return int(status.split()[-1])
    except (IndexError, ValueError):
        return 0


class PostgresWebNotificationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def insert_one(
        self,
        *,
        internal_user_id: str,
        kind: str,
        title: str,
        body: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Одиночная вставка (веб-канал scheduler'а). Возвращает id уведомления."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "INSERT INTO web_notifications (internal_user_id, kind, title, body, payload) "
                "VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING id",
                internal_user_id,
                kind,
                title,
                body,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            )
        assert row is not None
        return int(row["id"])

    async def insert_broadcast_for_tg_ids(
        self,
        *,
        telegram_user_ids: list[int],
        title: str,
        body: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> int:
        """Массовая вставка broadcast-уведомлений по списку telegram_user_id
        (web-аудитория рассылки одним запросом). Возвращает число вставленных строк."""
        if not telegram_user_ids:
            return 0
        async with self._pool.acquire() as conn:
            status = await conn.execute(
                "INSERT INTO web_notifications (internal_user_id, kind, title, body, payload) "
                "SELECT i.internal_user_id, 'broadcast', $2, $3, $4::jsonb "
                "FROM user_identities i WHERE i.telegram_user_id = ANY($1::bigint[])",
                telegram_user_ids,
                title,
                body,
                json.dumps(payload, ensure_ascii=False) if payload is not None else None,
            )
        return _insert_count(status)

    async def list_for_user(self, internal_user_id: str, *, limit: int, offset: int) -> list[dict[str, Any]]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"SELECT {_LIST_COLUMNS} FROM web_notifications "  # noqa: S608
                "WHERE internal_user_id = $1 ORDER BY id DESC LIMIT $2 OFFSET $3",
                internal_user_id,
                limit,
                offset,
            )
        return [dict(row) for row in rows]

    async def count_unread(self, internal_user_id: str) -> int:
        async with self._pool.acquire() as conn:
            value = await conn.fetchval(
                "SELECT COUNT(*) FROM web_notifications WHERE internal_user_id = $1 AND read_at IS NULL",
                internal_user_id,
            )
        return int(value or 0)

    async def mark_read(self, internal_user_id: str, ids: list[int] | None = None) -> int:
        """Отметить прочитанными: конкретные id (``ids``) или все (``None``).
        Возвращает число обновлённых строк."""
        async with self._pool.acquire() as conn:
            if ids is None:
                status = await conn.execute(
                    "UPDATE web_notifications SET read_at = NOW() WHERE internal_user_id = $1 AND read_at IS NULL",
                    internal_user_id,
                )
            else:
                status = await conn.execute(
                    "UPDATE web_notifications SET read_at = NOW() "
                    "WHERE internal_user_id = $1 AND read_at IS NULL AND id = ANY($2::bigint[])",
                    internal_user_id,
                    ids,
                )
        try:
            return int(status.split()[-1])
        except (IndexError, ValueError):
            return 0

    async def get_for_user(self, notification_id: int, internal_user_id: str) -> dict[str, Any] | None:
        """Уведомление конкретного пользователя (владение проверяется в SQL —
        чужие id выглядят как отсутствие)."""
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"SELECT {_LIST_COLUMNS} FROM web_notifications "  # noqa: S608
                "WHERE id = $1 AND internal_user_id = $2",
                notification_id,
                internal_user_id,
            )
        return None if row is None else dict(row)

    async def broadcast_days_claims(self, telegram_user_id: int, job_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Статусы bonus-days claims: {job_id: {status, expires_at}}."""
        if not job_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT job_id, status, expires_at FROM broadcast_bonus_claims "
                "WHERE telegram_user_id = $1 AND job_id = ANY($2::uuid[])",
                telegram_user_id,
                job_ids,
            )
        return {str(row["job_id"]): dict(row) for row in rows}

    async def broadcast_promo_claims(self, telegram_user_id: int, job_ids: list[str]) -> dict[str, dict[str, Any]]:
        """Промо claims: {job_id: {code, expires_at}}."""
        if not job_ids:
            return {}
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT job_id, code, expires_at FROM broadcast_promo_claims "
                "WHERE telegram_user_id = $1 AND job_id = ANY($2::uuid[])",
                telegram_user_id,
                job_ids,
            )
        return {str(row["job_id"]): dict(row) for row in rows}
