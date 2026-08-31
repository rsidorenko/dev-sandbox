# Design Spec — Веб-уведомления на сайте: рассылки + сервисные события + клейм бонусов

- **Автор / ответственный:** rsidorenko / Claude Code
- **Дата:** 2026-08-31
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-08-31-web-notifications.md`

## Контекст и проблема

Рассылки из админ-панели и сервисные уведомления (истечение подписки/триала, удаление ключей,
реферальный оффер) доставляются только в Telegram. Пользователи сайта полностью исключены:

- `web_api/admin_broadcast.py`: фильтр `telegram_user_id > 0` на трёх уровнях (предикаты
  категорий `_CATEGORY_PREDICATES`, `_count_sql`/`_fetch_sql`, `_resolve_explicit_targets`);
- claims-таблицы бонусов (`broadcast_bonus_claims` / `broadcast_promo_claims`) сидируются
  только по фактическим TG-получателям — web-пользователь не может получить бонус;
- `runtime/notification_scheduler.py`: web-only отфильтрованы, а в `_send_notification`
  при tg_id < 0 TG-отправка фейлится каждый час (400 от Bot API, ретраи бесконечно).

**Цель:** система уведомлений на сайте — колокольчик с бейджем в хедере, dropdown, polling
~45 сек. Уведомления создаются для всех, кто может залогиниться (verified email в
`user_emails`): web-only (отрицательные tg id) и линкнутые TG-пользователи (дублирование
TG+web осознанно допустимо). Бонусы рассылок (дни/промокод) полноценно клеймятся с сайта
теми же ядрами, что в боте (`grant_subscription_days`, общий промо-apply-core).

## Решения (зафиксированы с владельцем продукта)

| Вопрос | Решение |
|---|---|
| Объём | Рассылки из админки + все сервисные события scheduler'а |
| Доставка | Polling ~45 сек (без SSE/WebSocket) |
| UI | Колокольчик в хедере + dropdown |
| Аудитория | Все авторизованные (verified email); TG+web дубли допустимы |
| Бонусы | Полноценный клейм с сайта |

## Цели

- Новая таблица `web_notifications` (миграция 077) + веб-канал доставки.
- Web fan-out рассылки одним INSERT...SELECT; claims для web-only (отрицательные tg_id).
- Эндпоинты `GET/POST /api/v1/user/notifications*` (list / read / claim) c CSRF.
- Scheduler: двухканальная доставка, единый дедуп `notification_log`.
- Фронт: `features/notifications/` (api / hook / sanitize / колокольчик), Header auth-aware.
- Устранение существующего дублирования промо-apply-core (бот ↔ web_api).

## Non-goals

- Без real-time (SSE/WebSocket) — polling.
- Без новых env-флагов (аддитивная фича; docker-compose `environment[]` не трогаем).
- Без retention/очистки уведомлений в MVP (лимит выдачи 50 + пагинация) — follow-up.
- Без новой защищённой страницы — только dropdown в хедере.
- Рассылка остаётся one-shot: перезапуск веб-канала после падения не делается (ошибки
  веб-этапа не роняют TG-цикл, счётчики best-effort).

## Ключевые решения дизайна

- **Идентификация**: notification → `internal_user_id`; клейм — `telegram_user_id` из JWT
  claims (ключ claims-таблиц `(job_id, telegram_user_id)`; для web-only отрицательный).
- **Claims на оба канала**: PK общий — линкнутый пользователь сидируется один раз; web-only
  сидируются fan-out'ом с отрицательным id (`ON CONFLICT DO NOTHING`).
- **Дедуп scheduler'а**: одна запись `notification_log` на оба канала (`web_ok or tg_ok`).
- **Идемпотентность клейма**: дни — атомарный `UPDATE ... WHERE status='eligible' AND
  expires_at > NOW() RETURNING days` (единственный победитель); промокод — `promo_applied`
  PK=internal_user_id (как в боте).
- **Shared промо-ядро**: `application/promo_apply_service.py::apply_promo_intent` — перенос
  тела `_apply_promo_to_user`; бот и web_api становятся делегатами (поведение неизменно).
- **Web fan-out до раннего return**: в `BroadcastRunner.run()` web-этап выполняется ДО
  проверки «TG-получателей нет» — иначе рассылка только на веб-аудиторию тихо пропадает.
