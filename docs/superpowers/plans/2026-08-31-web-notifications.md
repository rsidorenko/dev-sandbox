# Plan — Веб-уведомления на сайте (feat/web-notifications)

- **Спека:** `docs/superpowers/specs/2026-08-31-web-notifications-design.md`
- **Ветка:** `feat/web-notifications` (от main @ a50a3f8)
- **Дата:** 2026-08-31

## Задачи (коммиты)

1. **migration: 077 web_notifications** — таблица `web_notifications` (BIGSERIAL PK,
   `internal_user_id`, `kind` CHECK 7 значений, `title`, `body`, `payload JSONB`,
   `created_at`, `read_at`) + индексы `(internal_user_id, id DESC)` и partial unread +
   счётчики `broadcast_jobs.web_recipient_count/web_notified_count`; дополнить реестр
   `tests/test_postgres_migrations.py`.
2. **persistence: web_notifications_store** — тонкий static-SQL стор (`insert_one`,
   `insert_many_broadcast` INSERT...SELECT, `list_for_user`, `count_unread`, `mark_read`,
   `get_for_user`); юнит-тесты SQL-строителей.
3. **refactor: общий promo apply core** — `application/promo_apply_service.py::
   apply_promo_intent(pool, репо-параметры, internal_user_id, raw_code, now)`; делегаты в
   `bot_transport/runtime_facade.py::_apply_promo_to_user` и `web_api/promocode.py::
   handle_apply_promocode`; юнит-тесты на in-memory репо; поведение неизменно.
4. **web_api: эндпоинты уведомлений** — `GET /api/v1/user/notifications` (list + unread +
   has_more + claim_status-обогащение), `POST .../read` ({ids}|{all}), `POST .../{id}/claim`
   (days: atomic UPDATE + `grant_subscription_days` + `trial_used`; promo: gate + pre-check +
   `apply_promo_intent`); in-process rate-limit 10/мин fail-open; интеграционные тесты
   (opt-in `DATABASE_URL`).
5. **broadcast: web fan-out** — `_CATEGORY_PREDICATE_CORE` (без `> 0`) + web-билдеры;
   `_resolve_explicit_targets` без фильтра знака для email; в `run()` web-этап ДО раннего
   return: аудитория (verified email) → сид claims web-only → INSERT...SELECT → счётчики;
   try/except — ошибки веб-канала не роняют TG-цикл; расширение юнит/интеграционных тестов.
6. **scheduler: веб-канал** — `_send_notification`: `has_email` → web-вставка по маппингу
   `_WEB_KINDS` (kind ≡ CHECK 077), `tg_id > 0` → TG; лог при `web_ok or tg_ok`; снять
   `> 0` с referral-запросов; тесты (в т.ч. «web-only не зовёт TG»).
7. **frontend: колокольчик** — `features/notifications/{api,useNotifications,sanitizeHtml,
   NotificationsBell}` + Header `useAuth()` + виттесты; polling 45с; кнопка «Получить» со
   стейтами (eligible/claimed/expired).
8. (опц.) **admin: web-счётчики** в списке рассылок.

## Валидация

- `cd backend && python -m pytest -q` — юниты;
- интеграционные с локальным PG: `DATABASE_URL=postgres://…@localhost:5433/… pytest -q
  tests/test_web_api_notifications.py tests/test_admin_broadcast_integration.py
  tests/test_notification_scheduler_web.py tests/test_postgres_migrations.py`;
- `cd frontend && npx vitest run`; ruff для новых backend-модулей.

## Риски/готчи

- Web fan-out строго ДО раннего `if not recipients: return` (веб-only рассылка).
- `test_admin_broadcast_unit.py:114` ассертит `> 0` в TG-SQL — TG-билдеры не менять.
- JSONB у asyncpg — `json.loads` + fallback `{}`.
- `_WEB_KINDS` и CHECK миграции 077 обязаны совпадать (тест).
- Pool без weakref — rate-limiter по internal_user_id, не по pool.
