"""Операторский CLI для безопасного сброса internal/test аккаунта.

Основной интерфейс операции находится в админке. Скрипт оставлен для аварийного
операторского доступа и использует тот же application service с теми же защитами.

Запуск внутри production-контейнера:
  python delete_test_user.py --telegram-id 123 --confirm-telegram-id 123 --reason "Повторный тест"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, "/app/src")


async def main(*, telegram_id: int, confirmation: int, reason: str) -> None:
    import asyncpg

    from app.application.admin_test_user_reset import AdminTestUserResetError, reset_test_user
    from app.issuance.xui_vless_provider import XuiVlessProvider

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    provider = XuiVlessProvider(pool)
    try:
        internal_user_id = await pool.fetchval(
            "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
            telegram_id,
        )
        if internal_user_id is None:
            print(f"Telegram ID {telegram_id}: пользователь не найден")
            return
        try:
            result = await reset_test_user(
                pool=pool,
                vless_provider=provider,
                internal_user_id=str(internal_user_id),
                confirmation_telegram_user_id=confirmation,
                reason=reason.strip(),
                actor="delete_test_user.py",
            )
        except AdminTestUserResetError as exc:
            print(f"Сброс отклонён: {type(exc).__name__}")
            raise SystemExit(2) from exc
        total = sum(result.deleted_rows.values())
        print(
            f"Сброс выполнен: telegram_id={result.telegram_user_id}, "
            f"vpn={result.vpn_outcome}, удалено строк={total}, correlation_id={result.correlation_id}"
        )
    finally:
        await provider.aclose()
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--telegram-id", type=int, required=True)
    parser.add_argument("--confirm-telegram-id", type=int, required=True)
    parser.add_argument("--reason", required=True)
    arguments = parser.parse_args()
    asyncio.run(
        main(
            telegram_id=arguments.telegram_id,
            confirmation=arguments.confirm_telegram_id,
            reason=arguments.reason,
        )
    )
