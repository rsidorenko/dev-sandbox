"""Выдача subscription-токена — единая точка для провайдера VLESS и web-авторизации.

Токен — единственный креденшл пользователя: и конфиги ``/sub/<token>``, и (с миграцией
на link-auth, 2026-09-20) вход в веб-кабинет. 128 бит энтропии, бессрочный.
"""

from __future__ import annotations

import secrets

import asyncpg


def generate_subscription_token() -> str:
    return secrets.token_urlsafe(16)


async def ensure_subscription_token(pool: asyncpg.Pool, internal_user_id: str) -> str:
    # URL подписки постоянный (f620f73): существующий токен возвращаем как есть,
    # без TTL-ротации. 90-дневный TTL из #74, молча возвращённый #94 (7bfd690),
    # ломал Karing-автообновление на 90-й день (404/410 на /sub/). Новые токены
    # выдаём без expires (NULL = бессрочный); миграция 075 обнулила expires у всех.
    row = await pool.fetchrow(
        "SELECT subscription_token FROM user_identities WHERE internal_user_id = $1",
        internal_user_id,
    )
    if row and row["subscription_token"]:
        return row["subscription_token"]
    token = generate_subscription_token()
    await pool.execute(
        "UPDATE user_identities SET subscription_token = $1 WHERE internal_user_id = $2",
        token,
        internal_user_id,
    )
    return token
