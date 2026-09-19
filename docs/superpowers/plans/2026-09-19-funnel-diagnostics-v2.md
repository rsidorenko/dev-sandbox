# Plan — Диагностика воронки v2

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-funnel-diagnostics-v2-design.md`
- **Ветка:** `feat/funnel-diagnostics-v2`

## Сводка

Добавляем безопасную route-инструментацию, read-only диагностику meaningful/D1-D3 и минимальное
представление новых метрик в существующей админке.

## Фаза 1 — Схема и сбор маршрута

### Задача 1.1 — Расширить interaction schema
- **Файлы:** `backend/migrations/091_funnel_diagnostics_v2.sql`,
  `backend/tests/test_postgres_migrations.py`
- **Что сделать:** добавить `connection_route` и ограничение допустимых значений.
- **Acceptance:** миграции применяются повторно, существующие строки остаются валидными.

### Задача 1.2 — Записывать direct/guided
- **Файлы:** `backend/src/app/application/funnel_events.py`,
  `backend/src/app/bot_transport/runtime_facade.py`, `backend/tests/test_funnel_foundation.py`
- **Что сделать:** валидировать route и присваивать его платформенным callback.
- **Acceptance:** fast path пишет direct, подробная инструкция — guided, неизвестное значение
  отбрасывается fail-open для основного bot flow.

## Фаза 2 — Диагностические агрегаты

### Задача 2.1 — Добавить meaningful и retention API
- **Файлы:** `backend/src/app/web_api/admin_stats.py`, `backend/tests/test_admin_stats.py`
- **Что сделать:** построить eligible cohort, накопительный meaningful threshold и зрелые D1-D3.
- **Acceptance:** API возвращает порог, traffic conversion, retention и route metrics; excluded
  users не считаются.

### Задача 2.2 — Расширить рекламную воронку
- **Файлы:** `backend/src/app/web_api/admin_stats.py`,
  `backend/tests/test_telegram_ads_attribution.py`
- **Что сделать:** добавить meaningful traffic per campaign и total.
- **Acceptance:** технический и meaningful трафик доступны одновременно и имеют разные значения
  на данных ниже порога.

## Фаза 3 — Представление и контракты

### Задача 3.1 — Показать диагностику в админке
- **Файлы:** `frontend/src/entities/admin/types.ts`, `frontend/src/app/admin/stats/page.tsx`
- **Что сделать:** добавить компактные карточки meaningful/D1-D3 и таблицу direct/guided.
- **Acceptance:** пустые знаменатели отображаются без деления на ноль; существующая статистика
  остаётся доступной.

### Задача 3.2 — Зафиксировать frontend-контракт
- **Файлы:** frontend-тесты типов/рендера при необходимости.
- **Что сделать:** проверить production typecheck/build и формат новых полей.
- **Acceptance:** frontend unit/build зелёные.

## Стратегия тестирования

- Unit: callback route mapping, recorder validation, SQL helper serialization.
- PostgreSQL integration: threshold 1 МиБ, below-threshold traffic, mature/immature D1-D3,
  eligible filter и повторное применение migration.
- Regression: полный backend pytest, frontend test/lint/build, release readiness.

## Риски и откат

- Риск тяжёлого SQL на длинном периоде → окно уже ограничено 366 днями, агрегация начинается с
  cohort и indexed traffic dates.
- Риск неверной трактовки route → API документирует пересечение exposures и отдельно возвращает
  переход direct→guided.
- Откат: revert кода; additive column остаётся безопасной и не влияет на старые readers.

## Чек-лист перед MR

- [x] `cd backend && python -m pytest -q`
- [x] Изолированные PostgreSQL integration-тесты зелёные
- [x] `python scripts/run_mvp_repo_release_health_check.py`
- [x] ruff check/format чисты
- [x] `cd frontend && npm test && npm run lint && npm run build`
- [x] `git diff --check`, только целевые файлы в коммите
- [x] Spec и plan соответствуют реализации
