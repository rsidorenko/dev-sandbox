"""Прощальная рассылка ссылок-подписок владельцам верифицированных email-аккаунтов.

Link-auth (2026-09-20): ``subscription_token`` — единственный креденшл, вход по
почте уходит, таблицы ``user_emails``/``email_verification_codes`` дропаются
(миграция 098). До дропа каждый владелец верифицированной почты обязан получить
свою вечную ссылку входа — после дропа канала доставки не будет, восстановления
по почте нет by design.

Аудитория — все ``is_verified`` строки ``user_emails``:

- ``telegram_user_id < 0`` (web-only): access-critical письмо со ссылкой
  ``{site}/auth/<token>?next=/dashboard`` — шаблон ``send_subscription_link``
  уже содержит предупреждение «без ссылки доступ не восстановить, вход по почте
  больше не используется». Токен не ротируется: ``ensure_subscription_token``
  идемпотентно возвращает существующий (тот же, что в ``/sub/``).
- ``telegram_user_id > 0`` (старая TG-привязка): SKIP без отправки — вход у этих
  аккаунтов через бота («Моя подписка»), ссылка им доступна и так. Появление
  таких строк в dry-run = повод остановиться и решить вручную.

Запуск внутри production-контейнера (env: ``DATABASE_URL``, SMTP-переменные):

  python scripts/send_farewell_subscription_links.py         # dry-run: CSV, ничего не отправляет
  python scripts/send_farewell_subscription_links.py --yes   # боевая отправка
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys

sys.path.insert(0, "/app/src")


async def main(*, send: bool) -> int:
    import asyncpg

    from app.email.sender import load_smtp_config, send_subscription_link
    from app.issuance.subscription_token import ensure_subscription_token
    from app.shared.site_url import get_site_base_url

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        rows = await pool.fetch(
            """
            SELECT ue.email,
                   ue.telegram_user_id,
                   ui.internal_user_id,
                   ui.subscription_token
            FROM user_emails ue
            LEFT JOIN user_identities ui
                   ON ui.telegram_user_id = ue.telegram_user_id
            WHERE ue.is_verified = TRUE
            ORDER BY ue.telegram_user_id
            """
        )
        if not rows:
            print("user_emails: верифицированных строк нет — рассылка не требуется.")
            return 0

        if send and load_smtp_config() is None:
            print("SMTP не настроен (SMTP_HOST/SMTP_USER/SMTP_PASSWORD) — отправка невозможна.")
            return 2

        site = get_site_base_url()
        writer = csv.writer(sys.stdout)
        writer.writerow(["kind", "telegram_user_id", "email", "internal_user_id", "has_token", "result"])

        sent = failed = skipped = 0
        for row in rows:
            tg_id = int(row["telegram_user_id"])
            email = str(row["email"])
            internal_user_id = row["internal_user_id"]
            if tg_id > 0:
                # TG-привязка: вход через бота, письмо не отправляем (см. докстринг).
                skipped += 1
                writer.writerow(["tg_linked", tg_id, email, internal_user_id or "", "", "skip"])
                continue
            if not internal_user_id:
                # Орфанная почта без identity — аномалия, руками решать оператору.
                skipped += 1
                writer.writerow(["web_only_orphan", tg_id, email, "", bool(row["subscription_token"]), "skip"])
                continue
            if not send:
                writer.writerow(
                    [
                        "web_only",
                        tg_id,
                        email,
                        internal_user_id,
                        bool(row["subscription_token"]),
                        "dry_run",
                    ]
                )
                continue
            token = await ensure_subscription_token(pool, str(internal_user_id))
            link_url = f"{site}/auth/{token}?next=/dashboard"
            if await send_subscription_link(email, link_url):
                sent += 1
                writer.writerow(["web_only", tg_id, email, internal_user_id, True, "sent"])
            else:
                failed += 1
                writer.writerow(["web_only", tg_id, email, internal_user_id, True, "FAILED"])

        suffix = "отправлено" if send else "dry-run (ничего не отправлено)"
        print(f"\nИтог ({suffix}): sent={sent}, failed={failed}, skipped={skipped}")
        return 1 if failed else 0
    finally:
        await pool.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--yes",
        action="store_true",
        help="боевая отправка (без флага — dry-run: только CSV-список получателей)",
    )
    arguments = parser.parse_args()
    raise SystemExit(asyncio.run(main(send=arguments.yes)))
