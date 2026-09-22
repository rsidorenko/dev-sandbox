# План

Spec: ../specs/2026-09-22-promo-connection-reuse-design.md

1. Воспроизвести зависание на PostgreSQL с одним соединением.
2. Добавить необязательное connection в PostgresPromo*Repository и использовать conn при чтении промокода в _add_device_pg.
3. Проверить покупку с промокодом/без, полный pytest, ruff, PostgreSQL-пакет, независимое ревью и CI.
4. Отдельный MR после зависимости !175, merge только при зелёном CI.
