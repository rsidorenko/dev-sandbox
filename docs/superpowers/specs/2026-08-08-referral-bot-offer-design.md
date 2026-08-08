# Design Spec — Бот-оффер рефералки после активации (C2)

- **Дата:** 2026-08-08
- **Статус:** approved
- **Roadmap:** [`2026-08-08-bravada-growth-roadmap-design.md`](./2026-08-08-bravada-growth-roadmap-design.md) (пункт C2)
- **Plan:** [`docs/superpowers/plans/2026-08-08-referral-bot-offer.md`](../plans/2026-08-08-referral-bot-offer.md)
- **Ветка:** `feat/referral-bot-offer`

## Контекст и проблема

Реферальную ссылку пользователь видит, только если сам зайдёт в раздел рефералки. В моменты конверсии
(оформление триала, первая оплата) бот молчит — упускается сильнейший момент для вовлечения в адвокати.
Решение: бот **сам** предлагает реферальную ссылку сразу после активации.

UX-требование: нельзя слать «тот же пуш» дважды (сначала триал, потом оплата). Поэтому **два разных
сообщения**, каждый once-ever, текст под этап.

## Цели

- После оформления **триала** → бот шлёт оффер «пока пробуете — месяц за друга».
- После **первой оплаты** → бот шлёт ДРУГОЙ оффер «спасибо за подписку — месяц за друга».
- Each once-ever (NOT EXISTS в `notification_log` по своему типу) — без повторов при продлениях.
- Kill-switch через feature flag.

## Non-goals

- Follow-up через 3 дня — бэклог (не в этом слайсе; два пуша уже достаточное покрытие).
- Не трогать lifecycle-нотификации (trial/subscription expiry, keys) и реферальный кешбек/бонус C1.
- Не вшивать реферальную ссылку в текст — кнопка ведёт в раздел рефералки.

## Текущее состояние

- `runtime/notification_scheduler.py` — hourly + advisory-lock; `_send_notification(user, type, text,
  keyboard)` (dedup `(user,type,CURRENT_DATE)`, лог в `notification_log`, Bot API Markdown); `_run_checks_inner`.
- `bot_transport/notification_texts.py` — `text_*() -> (text, keyboard)`, `_inline_kb`.
- `bot_transport/storefront_ui.py` — `CB_REFERRAL="referral_program"`, `_inline_kb`.
- `migrations/026_notification_state.sql` — `notification_log(internal_user_id, notification_type, sent_at, sent_date)`.
- `subscription_snapshots.updated_at` бампится и на триале (`_UPSERT_STATE`), и на оплате (fulfillment/grant).

## Предлагаемое решение

- **Два notification-типа:** `referral_offer_trial` и `referral_offer_paid`, разные тексты, кнопка → `CB_REFERRAL`.
- **Триггер:** «недавняя активация» = `updated_at > NOW() - 25h` AND `state_label='active'`; маршрутизация по
  `plan_id` (триал = `plan_id IS NULL AND trial_expires_at IS NOT NULL`; оплата = `plan_id IS NOT NULL`).
- **Once-ever:** `NOT EXISTS` по своему типу в `notification_log`. Фильтр `telegram_user_id > 0` (web-only без чата).
- **Feature flag:** `REFERRAL_OFFER_ENABLE` (default true).

## Влияние

- **Файлы:** `bot_transport/notification_texts.py`, `runtime/notification_scheduler.py`, новый
  `tests/test_notification_referral_offer.py`.
- **Миграции:** нет (существующие таблицы).
- **Безопасность/биллинг:** не затрагиваются (только исходящее сообщение).

## Критерии приёмки

- [ ] Новый триал → в течение часа бот шлёт trial-оффер (once-ever).
- [ ] Первая оплата → paid-оффер (другой текст, once-ever).
- [ ] Trial→paid получает ОБА разных пуша, не повтор; продление — без повтора.
- [ ] `REFERRAL_OFFER_ENABLE=false` отключает оба.
- [ ] `pytest -q` зелёный, ruff чист, postgres-smoke проходит.

## Открытые вопросы

- Нет (стратегия «два разных пуша» согласована).
