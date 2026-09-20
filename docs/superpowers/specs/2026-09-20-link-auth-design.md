# Design Spec — Вход по ссылке-подписке вместо email-авторизации

- **Автор / ответственный:** rsidorenko / Claude Code
- **Дата:** 2026-09-20
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-09-20-link-auth.md`

## Контекст и проблема

Продолжение программы минимизации персональных данных (ПД-аудит 2026-09-11..12, стирание
web-аккаунтов и удаление привязки email — MR !153). Сегодня web-вход на bravada-connect.ru —
email + OTP-код: в БД хранятся таблицы `user_emails` (migrations 022) и `email_verification_codes`
(023), сессия — JWT в httponly-куке `session` + CSRF-кука (`web_api/auth.py`, `web_api/middleware.py`).

Хотим полностью уйти от хранения почт и кук: единственный креденшл пользователя — уже существующий
`user_identities.subscription_token` (128 бит, вечный, миграция 075), по которому без сессий работают
`/sub/<token>` (`web_api/subscription.py:72`) и `/api/v1/check/<token>` (`web_api/connection_check.py`).
Почта остаётся только каналом доставки ссылки при первой покупке — и нигде не сохраняется.

## Цели

- Вход на сайт по ссылке-подписке (`Authorization: Bearer <subscription_token>`), без кук и storage.
- Покупка без подписки: ввод почты → письмом приходит ссылка → по ней вход и оплата; почта не хранится.
- Просмотр срока подписки и данных в «реальном времени» (polling) + оплата — всё, что даёт вход.
- Кнопка в боте «Привязать ссылку-подписку»: email-ссылка привязывается к TG-аккаунту,
  старая ссылка TG отвязывается и удаляется (механика как была с почтой).
- Прощальная рассылка ссылок оставшимся ~21 web-юзерам и TG-юзерам со старой привязкой →
  стирание почт → дроп таблиц `user_emails` / `email_verification_codes`.

## Non-goals

- Восстановление утерянной ссылки (решение: якорь — привязка в боте или саппорт).
- Дедупликация повторных заявок по почте (невозможна без хранения почты; принято осознанно).
- Отдельный веб-токен (решено: один токен на `/sub/` и вход).
- Изменение админки (свои куки `admin_session`, `JWT_ADMIN_SECRET`, общий `jwt_revocation_list` — не трогаем).
- Изменение вебхуков оплат (HMAC, без сессии) и платежной механики (уже web-only-safe, R-14).

## Текущее состояние

- `backend/src/app/web_api/auth.py` — email OTP (`send-code`/`verify`), `_issue_jwt` (HS256,
  claims `telegram_user_id`, `internal_user_id`, `email`), создание web-аккаунтов: отрицательные
  tg-id из `web_user_id_seq` (лениво, `:209-214`), `internal_user_id = web_<sha256(email)[:12]>` (`:217`),
  реферальный хук `apply_referral_on_registration` (`:236-251`).
- `backend/src/app/web_api/middleware.py` — `require_auth`: кука `session` ИЛИ Bearer-JWT (`:54-67`);
  CSRF double-submit (`:92-107`); `check_jwt_not_revoked` и `generate_csrf_token` импортирует админка
  (`admin_auth.py:32`) — их удалять нельзя.
- `subscription_token`: генерация `secrets.token_urlsafe(16)` в `_ensure_subscription_token`
  (`issuance/xui_vless_provider.py:62-79`), лениво при выдаче ключей; ротации при reissue нет.
- Уведомления: `web_notifications` ключуются `internal_user_id`; аудитория web-канала фильтруется
  по наличию verified email — `_WEB_EMAIL_EXISTS` (`web_api/admin_broadcast.py:192-194`),
  `notification_scheduler.py:345-351`; явный таргетинг режет tg-id ≤ 0 (`:236-238`).
- Фронтенд (`frontend/`, Next.js 15): `shared/api/client.ts` — csrf-кука + `credentials:"include"`;
  `src/middleware.ts` — гвард `/dashboard` по куке `session`; AuthGate чекаута
  (`app/(site)/(protected)/payment/[planId]/page.tsx:562-627`); `useNotifications` poll 45с;
  e2e-фикстуры инжектят куку (`e2e/fixtures.ts:222`).
- Модуль web→TG слияния `persistence/account_merge.py` удалён в MR !153 — восстановлен из git
  (`4c048f6^`): одна транзакция, TG выигрывает конфликты, при адоптении web-подписки переносятся
  `subscription_snapshots` / `issuance_state` / billing-таблицы, `experiment_assignments` — ранний
  вариант, реферал: код TG-wins, баланс суммируется, self-referral-loop удаляется.
- Миграции применяются автоматически при старте рантайма
  (`runtime/telegram_httpx_live_configured.py:59`) — порядок релизов с рассылкой/дропом критичен.

## Предлагаемое решение

**Авторизация.** `require_auth` принимает `Authorization: Bearer <subscription_token>` →
`SELECT telegram_user_id, internal_user_id FROM user_identities WHERE subscription_token = $1`
без entitlement-гейта (истёкшая подписка должна уметь продлиться). Rate-limit только неудачных
попыток (per-IP, in-memory, паттерн `subscription.py:48-69`). CSRF исчезает вместе с куками.
Переходный период — dual-mode (JWT-путь жив до срезки).

**Выдача ссылки.** Новый роут `POST /api/v1/auth/link/request {email, referral_code?}`:
валидация + disposable (fail-open) + 503 без SMTP + per-IP rate limit ~5/ч; сразу создаёт
web-аккаунт (отрицательный tg-id, `internal_user_id = web_<random>` — без email-хеша),
токен генерируется немедленно (`ensure_subscription_token` вынесен в отдельный модуль);
письмо со ссылкой `{SITE}/auth/<token>?next=...`; почта не сохраняется нигде; сюда переезжает
реферальная атрибуция.

**Фронтенд.** Токен только в памяти (module-scope store): SPA-навигация живёт, reload = повторный
вход. `/login` — дуал: вставить ссылку (валидация через `GET /api/v1/user/profile`) или запросить
по почте. Роут `(site)/auth/[token]` захватывает токен и вычищает его из URL. Возврат с платежки —
на статичный `/success` (return_url меняется с `/dashboard`). Профиль отдаёт `subscription_url` +
`login_url` — без этого первый выход = лок-аут навсегда.

**Бот.** Кнопка «Привязать ссылку-подписку» → FSM-ввод (паттерн `_awaiting_promo_code`) →
валидация (токен существует, `telegram_user_id <= 0`) → подтверждение → слияние по токену
(адаптированный account_merge) + переприсвоение токена TG-identity (порядок с учётом unique index)
+ удаление web-строки.

**Уведомления/рассылки.** Предикат web-аудитории: `telegram_user_id < 0` вместо email-exists;
email-таргетинг удаляется, явный таргетинг пускает отрицательные id.

**Стирание.** Скрипт прощальной рассылки (dry-run по умолчанию, явный opt-in) → миграция
`DROP TABLE user_emails, email_verification_codes` (последним релизом, после паузы ≥72ч).

## Альтернативы

- **HMAC-хеш почты для переотправки ссылки** — отвергнуто пользователем: хеш = ПД-след для поиска
  (логично после отказа от token-hash на аудите, этап 3).
- **Отдельный веб-токен** (изоляция от `/sub/`) — отвергнуто: пользовательский выбор «один токен»,
  меньше UX-сложности; риск «владение = обладание ссылкой» принят.
- **Переходный период email-входа 2–4 недели** — отвергнуто в пользу прощальной рассылки: ПД-поверхность
  живёт меньше.

## Влияние

- **Файлы (предположительно):** `web_api/auth.py` (удалить), `web_api/middleware.py`,
  `web_api/link_auth.py` (новый), `web_api/profile.py`, `web_api/payment.py`, `web_api/app.py`,
  `web_api/admin_broadcast.py`, `web_api/admin_users.py`, `runtime/notification_scheduler.py`,
  `issuance/subscription_token.py` (новый), `email/sender.py`, `persistence/account_merge.py`
  (восстановить по токену), `bot_transport/runtime_facade.py`, `bot_transport/storefront_ui.py`,
  `nginx/conf.d/default.conf`, `docker-compose.prod.yml`; фронтенд: `shared/api/client.ts`,
  `shared/lib/tokenStore.ts` (новый), `features/auth/*`, `src/middleware.ts`, страницы login/dashboard/checkout.
- **Миграции БД:** новая `098_drop_user_emails.sql` (DROP двух таблиц; реестр
  `tests/test_postgres_migrations.py`). Миграции авто-применяются на старте — дроп только после
  прощальной рассылки.
- **Безопасность:** Bearer-токен вместо кук (CSRF не нужен); rate-limit подбора токенов;
  маскировка `/auth/<token>` в nginx-логах; disposable-домены по-прежнему fail-open; вычистить
  токен из URL (`history.replaceState`). Токен в access-логах/реферерах — принято (владение = доступ).
- **Биллинг:** не меняется; fulfillment по `metadata.internal_user_id` (R-14/R-08 сохранены);
  `return_url` → `/success` (иначе после оплаты разлогин).
- **Retention / lifecycle:** стирание почт = явная операция с dry-run и opt-in (соответствует
  констрейнтам retention); retention ключей не меняется.
- **VPN-провайдер:** `_ensure_subscription_token` выносится без изменения поведения; reissue
  по-прежнему не ротирует токен (осознанно: ротация = касскадная порча ссылок в клиентах).

## Открытые вопросы

- Нет блокирующих. Принятые компромиссы (дубли аккаунтов, reload = разлогин, владение = обладание)
  подтверждены пользователем 2026-09-20.

## Критерии приёмки

- [ ] Вход на сайт по ссылке работает; в devtools ноль кук/localStorage от юзер-сессии.
- [ ] Покупка по email-ссылке сквозная: письмо → вход → чекаут → оплата → `/success` → повторный вход.
- [ ] Токен истёкшей подписки видит дашборд с CTA продления.
- [ ] Привязка в боте: web-only токен → merge; TG-owned токен → отказ; старый TG-токен мёртв.
- [ ] Колокольчик уведомлений работает по токену; broadcast web-категории доходит до web-юзера.
- [ ] Профиль отдаёт `login_url`; на дашборде блок со ссылкой для входа.
- [ ] Почты стёрты, таблицы удалены, `pytest -q` зелёный, миграция в реестре.
- [ ] Тесты покрывают: link/request (создание, отсутствие записи почты, disposable, rate-limit,
      503 без SMTP, реферальный хук), Bearer-аутентификацию (валидный/неизвестный/истёкший токен,
      dual-mode), merge по токену (конфликты, unique-index-порядок, отказ tg-id>0), предикаты
      web-аудитории, фронт (unit + e2e).
- [ ] `cd backend && python -m pytest -q` зелёный, `python scripts/run_mvp_repo_release_health_check.py` проходит.
