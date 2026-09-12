# Plan — Экономика рекламной воронки

- **Дата:** 2026-09-12
- **Spec:** `docs/superpowers/specs/2026-09-12-funnel-economics-dashboard-design.md`
- **Ветка:** `feat/funnel-economics-dashboard`

## Сводка

Четыре фазы: аддитивное хранение дневного расхода, безопасная admin-мутация, когортная экономика в
read-only статистике и UI с проверками.

## Фаза 1 — Схема и персистентность расхода

### Задача 1.1 — Добавить миграцию 083
- **Файлы:** `backend/migrations/083_telegram_ads_spend.sql`
- **Что сделать:** создать daily spend и append-only audit таблицы с CHECK и индексами.
- **Acceptance:** повторное применение безопасно; пустые таблицы не влияют на текущий отчёт.

### Задача 1.2 — Реализовать идемпотентный репозиторий
- **Файлы:** `backend/src/app/persistence/postgres_telegram_ads_spend.py`
- **Что сделать:** транзакционно upsert/delete дневной суммы и писать audit только при изменении.
- **Acceptance:** одинаковый повтор не меняет `updated_at` и не добавляет audit; сумма 0 удаляет запись.

## Фаза 2 — Admin API и расчёт экономики

### Задача 2.1 — Добавить защищённый endpoint расхода
- **Файлы:** `backend/src/app/web_api/admin_ad_spend.py`, `backend/src/app/web_api/app.py`
- **Что сделать:** нормализовать key, разобрать ISO date и Decimal RUB, применить auth+CSRF.
- **Acceptance:** malformed/слишком большая сумма отклоняется до БД; успешный ответ возвращает копейки
  и рубли без float-расчёта записи.

### Задача 2.2 — Расширить когортный SQL
- **Файлы:** `backend/src/app/web_api/admin_stats.py`
- **Что сделать:** объединить attribution и spend-only кампании, вычислить acquisition, revenue и ratios,
  вернуть агрегированный economics summary.
- **Acceptance:** internal/grants/pre-existing payers исключены; существующая mature conversion сохранена.

## Фаза 3 — Интерфейс дашборда

### Задача 3.1 — Расширить типы и API
- **Файлы:** `frontend/src/entities/admin/types.ts`, `frontend/src/features/admin/api.ts`
- **Что сделать:** описать economics поля и PUT дневного расхода.
- **Acceptance:** TypeScript не использует `any`, amount отправляется строкой.

### Задача 3.2 — Добавить ввод и экономические KPI
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`
- **Что сделать:** форма campaign/date/₽, KPI расход/CAC/ROAS/выручка и колонки CPL/CPT/CAC/ROAS.
- **Acceptance:** сохранение перезагружает текущий период; нули и null отображаются однозначно; hint
  объясняет first-touch и ручной расход.

## Фаза 4 — Проверка и передача

### Задача 4.1 — Покрыть критические контракты
- **Файлы:** `backend/tests/test_funnel_economics.py`, существующие attribution/admin tests,
  frontend tests при необходимости.
- **Что сделать:** проверить parser, state-idempotency, audit, spend-only, existing payer, суммы и ratios.
- **Acceptance:** PostgreSQL integration исполняет реальный SQL и применяет миграцию дважды.

### Задача 4.2 — Обновить runbook
- **Файлы:** `backend/docs/telegram_ads_attribution.md`
- **Что сделать:** описать ввод, определения, ограничения и откат.
- **Acceptance:** оператор понимает, откуда берутся расход, CAC, revenue и ROAS.

## Стратегия тестирования

- Unit: нормализация campaign/date/amount, сериализация ratios и edge cases деления на ноль.
- PostgreSQL: миграция, идемпотентный PUT/repository, audit и реальная cohort-агрегация.
- Frontend: lint, typecheck, Vitest и production build.
- Полный backend pytest и release readiness перед push.

## Риски и откат

- Ошибка ручного ввода → дневной ключ, обнуление и audit old/new.
- Смешивание когорт → единое окно attribution + spend и явная семантика acquisition.
- Ложная выручка старых плательщиков → глобальная первая оплата должна быть после touch/триала.
- Деградация stats SQL → лимит 200 кампаний и существующие индексы user/time + campaign/time.
- Откат: вернуть feature-коммит; аддитивные таблицы оставить пустыми/неиспользуемыми.

## Чек-лист перед MR

- [ ] Полный backend pytest зелёный
- [ ] Release health/static/readiness проходят
- [ ] Ruff check/format чистые
- [ ] Frontend test/lint/build проходят
- [ ] Миграция применена дважды в PostgreSQL integration
- [ ] Коммит и MR на русском, без `Co-Authored-By`
- [ ] Spec/plan соответствуют реализации
