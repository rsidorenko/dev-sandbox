# Plan — Собственная аналитика и эксперименты

- **Дата:** 2026-09-17
- **Spec:** `docs/superpowers/specs/2026-09-17-analytics-experiments-design.md`
- **Ветка:** `feat/analytics-experiments`

## Сводка

Добавляем серверные A/B-назначения, первый checkout-эксперимент и два first-party отчёта
в существующую админ-статистику. Работа разделена на schema/domain, API/UI и измерение.

## Фаза 1 — Назначения эксперимента

### Задача 1.1 — Добавить безопасное хранилище
- **Файлы:** `backend/migrations/089_experiment_assignments.sql`.
- **Что сделать:** append-only таблица назначений с allowlist-friendly constraints и индексом времени.
- **Acceptance:** уникальность пользователя/эксперимента и отсутствие destructive DDL.

### Задача 1.2 — Реализовать детерминированный resolver
- **Файлы:** `backend/src/app/domain/experiments.py`, `backend/src/app/web_api/experiments.py`.
- **Что сделать:** allowlist, стабильный SHA-256 bucket, идемпотентный insert/select, auth.
- **Acceptance:** повтор всегда возвращает тот же вариант, неизвестный ключ даёт 400.

## Фаза 2 — Реальный эксперимент

### Задача 2.1 — Подключить checkout
- **Файлы:** frontend experiment API/hook и payment page.
- **Что сделать:** запросить `checkout_reassurance_v1`, безопасно fallback на control,
  показать reassurance-copy только варианту `reassurance`.
- **Acceptance:** платёжные параметры и обработчики одинаковы в обоих вариантах.

## Фаза 3 — First-party отчётность

### Задача 3.1 — Агрегировать product funnel и experiment results
- **Файлы:** `backend/src/app/web_api/admin_stats.py`.
- **Что сделать:** distinct-user шаги воронки; назначения, payment-created, paid, revenue и conversion.
- **Acceptance:** фильтры периода/внутренних аккаунтов/грантов корректны.

### Задача 3.2 — Показать отчёты в админке
- **Файлы:** admin types и stats page.
- **Что сделать:** компактная общая воронка и таблица вариантов с явной пометкой о выборке.
- **Acceptance:** пустые данные отображаются без ошибок, проценты не делят на ноль.

## Стратегия тестирования

- Domain: стабильность и распределение bucket, reject неизвестного ключа.
- API: auth, идемпотентность назначения, существующий вариант не перезаписывается.
- PostgreSQL stats: события после экспозиции и исключение внутренних фактов.
- Frontend: оба варианта checkout, fallback при ошибке API.
- Полные pytest, frontend unit/build/E2E и release scripts.

## Риски и откат

- Риск: перекос вариантов → детерминированный bucket тестируется на большой фиксированной выборке.
- Риск: аналитический SQL замедлит stats → агрегации выполняются параллельно и используют индексы.
- Риск: experiment API недоступен → UI остаётся control и checkout работает.
- Откат: убрать вызов/отчёт; таблица безопасно остаётся для аудита прошлых назначений.

## Чек-лист перед PR

- [x] Backend pytest, ruff и release readiness зелёные.
- [x] Frontend test/build/E2E зелёные.
- [x] Миграция additive и покрыта миграционными тестами; PostgreSQL smoke выполняется в CI.
- [x] Spec/plan соответствуют фактической реализации.
- [x] Git-facing prose на русском, без co-author trailers.
