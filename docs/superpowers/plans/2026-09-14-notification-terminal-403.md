# Plan — Терминальная ошибка Telegram 403

- **Дата:** 2026-09-14
- **Spec:** `docs/superpowers/specs/2026-09-14-notification-terminal-403-design.md`
- **Ветка:** `fix/notification-terminal-403`

## Фаза 1 — Схема результата доставки

### Задача 1.1 — Добавить delivery status
- **Файлы:** `backend/migrations/085_notification_log_delivery_status.sql`
- **Что сделать:** добавить `delivery_status` с default `delivered` и идемпотентным CHECK.
- **Acceptance:** существующие INSERT без колонки остаются рабочими; повтор миграции безопасен.

## Фаза 2 — Канальная семантика

### Задача 2.1 — Закрывать Telegram 403
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py` (`_send_notification`)
- **Что сделать:** различать delivered, terminal и retryable outcomes по каналам.
- **Acceptance:** 403 без доступного retryable-канала фиксируется один раз; остальные ошибки ретраятся.

## Фаза 3 — Проверки

### Задача 3.1 — Покрыть матрицу доставки
- **Файлы:** `backend/tests/test_notification_scheduler_web.py`,
  `backend/tests/test_postgres_migrations.py`
- **Acceptance:** unit-тесты покрывают Telegram-only, web+Telegram и временную ошибку web.

### Задача 3.2 — Запустить preflight
- **Acceptance:** pytest, PostgreSQL smoke, ruff и release health зелёные.

## Риски и откат

- Риск: потерять веб-ретрай при одновременном 403 → отдельный флаг retryable failure.
- Откат: revert кода; additive-колонка не мешает старой версии.

## Чек-лист перед PR

- [x] Полный pytest зелёный
- [x] PostgreSQL smoke зелёный
- [x] Ruff и release health зелёные
- [x] Миграция не содержит destructive SQL
