# Plan — Очистка и произвольный период статистики

- **Дата:** 2026-09-17
- **Spec:** `docs/superpowers/specs/2026-09-17-admin-stats-custom-range-design.md`
- **Ветка:** `feat/admin-stats-custom-range`

## Сводка

Реализация состоит из единого фильтра eligible-пользователей, симметричных временных границ
backend API, нового выбора дат в админке и регрессионных тестов.

## Фаза 1 — Источник правды аналитической выборки

### Задача 1.1 — Добавить eligibility-view
- **Файлы:** `backend/migrations/090_analytics_eligible_users.sql`
- **Что сделать:** создать read-only view внешних пользователей без кампании `nakrut`.
- **Acceptance:** повторное применение миграций безопасно; исходные строки не изменяются.

### Задача 1.2 — Перевести агрегаты на eligibility-view
- **Файлы:** `backend/src/app/web_api/admin_stats.py`
- **Что сделать:** применить view ко всем пользовательским и финансовым агрегатам, исключить
  `nakrut` из рекламных ключей и расхода.
- **Acceptance:** ни один раздел ответа статистики не содержит вклад `nakrut`.

## Фаза 2 — Произвольное окно

### Задача 2.1 — Расширить API периода
- **Файлы:** `backend/src/app/web_api/admin_stats.py`
- **Что сделать:** добавить `period=custom`, локальные даты, верхнюю границу и валидацию 366 дней.
- **Acceptance:** SQL использует `[from_ts, to_ts)`, traffic — включительные DATE-границы.

### Задача 2.2 — Покрыть API интеграционными тестами
- **Файлы:** `backend/tests/test_admin_stats.py`
- **Что сделать:** добавить `nakrut`-пользователя со всеми сигналами и проверки custom range.
- **Acceptance:** eligible-данные считаются, `nakrut` полностью исключён, ошибки диапазона дают 400.

## Фаза 3 — Интерфейс администратора

### Задача 3.1 — Расширить контракт и клиент
- **Файлы:** `frontend/src/entities/admin/types.ts`, `frontend/src/features/admin/api.ts`
- **Что сделать:** добавить `custom`, локальные даты и query-параметры диапазона.
- **Acceptance:** URL формируется с датами только для custom range.

### Задача 3.2 — Добавить выбор дат
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`
- **Что сделать:** добавить раскрываемую форму дат, клиентскую проверку и точную подпись периода.
- **Acceptance:** быстрые кнопки и пользовательский диапазон переключаются без лишних запросов.

### Задача 3.3 — Проверить frontend-контракт
- **Файлы:** `frontend/src/features/admin/api.test.ts`
- **Что сделать:** проверить параметры fixed/custom запросов.
- **Acceptance:** Vitest подтверждает отсутствие дат у preset и наличие обеих дат у custom.

## Стратегия тестирования

- PostgreSQL integration: `backend/tests/test_admin_stats.py`.
- Полный backend pytest и PostgreSQL smoke.
- Frontend Vitest, lint и production build.
- Release readiness/health scripts.

## Риски и откат

- Риск: тяжёлые запросы на длинном окне → лимит 366 дней и существующий timeout эндпоинта.
- Риск: трафик без сопоставимой identity перестанет попадать в пользовательскую статистику → это
  ожидаемо, потому что такой трафик нельзя классифицировать как eligible.
- Откат: revert кода и view-миграции; view не меняет исходные данные и не требует down-миграции.

## Чек-лист перед MR

- [x] `cd backend && python -m pytest -q`
- [x] Целевые PostgreSQL integration-тесты проходят; общий smoke блокируется внешним 3x-ui/opt-in gate
- [x] `python scripts/run_mvp_repo_release_health_check.py`
- [x] `ruff check` и `ruff format --check`
- [x] `cd frontend && npm test && npm run lint && npm run build`
- [x] `git diff --check`, только целевые файлы в коммите
- [x] Spec и plan соответствуют реализации
