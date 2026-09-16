# Plan — Отсчёт пробного периода от первого трафика

- **Дата:** 2026-09-16
- **Spec:** `docs/superpowers/specs/2026-09-16-trial-first-traffic-design.md`
- **Ветка:** `feat/trial-first-traffic`

## Сводка

В три фазы добавляем marker первого трафика, атомарный перенос срока триала и понятные тексты,
после чего проверяем миграцию и lifecycle тестами.

## Фаза 1 — Схема данных

### Задача 1.1 — Добавить marker первого трафика
- **Файлы:** `backend/migrations/086_trial_first_traffic.sql`,
  `backend/tests/test_postgres_migrations.py`
- **Что сделать:** добавить nullable-колонку, безопасно пометить исторически подключавшиеся триалы,
  создать partial index для ожидающих первого трафика.
- **Acceptance:** миграция additive/idempotent; тест контракта фиксирует колонку, backfill и индекс.

## Фаза 2 — Старт триала

### Задача 2.1 — Перенести clock на первый положительный трафик
- **Файлы:** `backend/src/app/persistence/postgres_traffic_stats.py`,
  `backend/tests/test_bare_xray_traffic.py`
- **Что сделать:** после записи батча обновлять активные неистёкшие триалы по положительным email
  в той же транзакции; возвращать число стартовавших триалов для наблюдаемости.
- **Acceptance:** SQL одноразовый, исключает платные/истёкшие записи; нулевые дельты не запускают clock.

### Задача 2.2 — Обновить пользовательские тексты
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`,
  `frontend/src/app/(site)/trial/page.tsx`, соответствующие тесты.
- **Что сделать:** сообщить о полном трёхдневном периоде после первого подключения и резерве на настройку.
- **Acceptance:** Telegram и web не обещают, что таймер уже начал идти в момент выпуска ключей.

## Фаза 3 — Проверка

### Задача 3.1 — Прогнать проверки репозитория
- **Файлы:** без изменений.
- **Что сделать:** targeted tests, полный pytest, frontend lint/test/build, release health check, diff check.
- **Acceptance:** все доступные проверки зелёные до push.

## Стратегия тестирования

- Юнит-тесты репозитория трафика проверяют положительные/нулевые email и SQL-guards.
- Контракт миграции проверяет additive DDL и безопасный backfill.
- Тесты storefront фиксируют новую формулировку.
- Полный backend pytest и frontend test/lint/build ловят регрессии интеграции.

## Риски и откат

- Риск: traffic collector выключен → действует прежний срок от выдачи; это безопасная деградация.
- Риск: повторная дельта → marker и `IS NULL` исключают повторное продление.
- Откат: вернуть код записи clock; nullable-колонку можно оставить без использования.

## Чек-лист перед PR

- [x] `cd backend && .venv/bin/python -m pytest -q` — 2356 passed, 133 skipped
- [x] `.venv/bin/python scripts/run_mvp_repo_release_health_check.py` — проходит
- [x] Targeted ruff для изменённых Python-файлов — чист; полный repo-lint сохраняет 52
  существующие ошибки в операторских scripts вне этого изменения
- [x] Frontend test/lint/build и targeted Playwright `e2e/trial.spec.ts` проходят
- [x] Коммит сфокусирован, сообщение на русском, без `Co-Authored-By`
- [x] Spec и plan соответствуют реализации
