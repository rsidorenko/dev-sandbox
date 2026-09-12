# Plan — полная рекламная воронка в админке

- **Дата:** 2026-09-12
- **Spec:** `docs/superpowers/specs/2026-09-12-full-funnel-metrics-design.md`
- **Ветка:** `feat/full-funnel-metrics`

## Сводка

Расширяем read-only агрегацию Telegram Ads реальным трафиком и всеми уже собираемыми
событиями, затем показываем суммарную цепочку и полный campaign breakdown в админке.

## Фаза 1 — backend-агрегация

### Задача 1.1 — собрать все сигналы когорты
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`_TELEGRAM_ADS_CAMPAIGNS`).
- **Что сделать:** добавить флаги событий, platform breakdown и наличие положительного
  bare_xray-трафика после триала.
- **Acceptance:** одна рекламная когорта считается distinct users; данные до триала не влияют.

### Задача 1.2 — вернуть общую воронку
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`_telegram_ads_campaigns`).
- **Что сделать:** добавить агрегаты всех этапов и платформ в `telegram_ads.funnel`, не зависящие
  от лимита campaign-таблицы.
- **Acceptance:** API возвращает нули для пустой выборки и не раскрывает пользовательские поля.

## Фаза 2 — интерфейс админки

### Задача 2.1 — обновить API-типы
- **Файлы:** `frontend/src/entities/admin/types.ts`.
- **Что сделать:** типизировать traffic/problem/platform metrics и суммарную воронку.
- **Acceptance:** TypeScript не использует `any`, backend/frontend contracts совпадают.

### Задача 2.2 — показать цепочку и полный breakdown
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`.
- **Что сделать:** добавить визуальную последовательность этапов и конверсий, новые колонки,
  платформы и точные названия старых колонок.
- **Acceptance:** все метрики доступны при выбранном периоде; таблица остаётся прокручиваемой.

## Фаза 3 — тесты и доставка

### Задача 3.1 — расширить тесты агрегатов
- **Файлы:** `backend/tests/test_telegram_ads_attribution.py`,
  `backend/tests/test_funnel_economics.py`.
- **Что сделать:** добавить положительный трафик, платформу, проблему, общий funnel и нулевой кейс.
- **Acceptance:** PostgreSQL-тест доказывает связь campaign user с обоими email-контрактами;
  unit-тест защищает пустые агрегаты.

### Задача 3.2 — выполнить delivery
- **Acceptance:** backend pytest, frontend lint/typecheck/unit/build, Ruff, health-check и
  `git diff --check` зелёные; создан GitLab MR без автослияния.

## Стратегия тестирования

- PostgreSQL integration test создаёт рекламные когорты, события, трафик и оплаты и сверяет
  точный JSON campaign/funnel.
- Pure unit test проверяет нулевые знаменатели и пустую воронку.
- Frontend проверяется TypeScript, ESLint, Vitest и production build.
- Полный backend pytest и release health-check защищают оплату и VPN lifecycle.

## Риски и откат

- Риск: дневная точность трафика — явно описываем как first observed traffic; точный timestamp
  остаётся отдельной фичей.
- Риск: SQL станет тяжелее — поиск трафика использует существующий `(email, stat_date)` индекс,
  campaign output ограничен 200 строками.
- Откат: один кодовый коммит; миграций и изменения данных нет.

## Чек-лист перед MR

- [x] Backend unit/integration tests зелёные.
- [x] Frontend lint/typecheck/unit/build зелёные.
- [x] Ruff и release health-check зелёные.
- [x] `git diff --check` зелёный.
- [x] Нет изменений оплаты, миграций, VPN CRUD и секретов.
- [ ] Commit/push/MR только из `feat/full-funnel-metrics`.
