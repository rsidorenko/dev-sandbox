# Plan — Бот-оффер рефералки после активации (C2)

- **Дата:** 2026-08-08
- **Spec:** [`docs/superpowers/specs/2026-08-08-referral-bot-offer-design.md`](../specs/2026-08-08-referral-bot-offer-design.md)
- **Ветка:** `feat/referral-bot-offer`

## Сводка

Два разных once-ever пуша (trial + paid) поверх существующего `notification_scheduler`. 3 фазы: тексты →
проверки планировщика → тесты. Миграций нет.

## Фаза 1 — Тексты (`bot_transport/notification_texts.py`)

### 1.1 `text_referral_offer_trial()` / `text_referral_offer_paid()`
- Разный Markdown-текст; оба: «1 друг = +30 дней», кнопка `🎁 Получить реферальную ссылку` → `CB_REFERRAL`.
- Импорт `CB_REFERRAL` из `storefront_ui`.

## Фаза 2 — Планировщик (`runtime/notification_scheduler.py`)

### 2.1 Feature flag
- `_referral_offer_enabled()` читает `REFERRAL_OFFER_ENABLE` (default true).

### 2.2 `_check_referral_offer_trial(now)`
- Кандидаты: `state='active' AND plan_id IS NULL AND trial_expires_at IS NOT NULL AND updated_at > NOW()-25h
  AND telegram_user_id > 0 AND NOT EXISTS notification_log(type='referral_offer_trial')`.
- `_send_notification(type='referral_offer_trial', text=text_referral_offer_trial())`.

### 2.3 `_check_referral_offer_paid(now)`
- То же, но `plan_id IS NOT NULL`, тип `referral_offer_paid`, текст `text_referral_offer_paid()`.

### 2.4 Wire
- Оба в `_run_checks_inner` внутри `if _referral_offer_enabled()`.

## Фаза 3 — Тесты (`tests/test_notification_referral_offer.py`)

- `text_referral_offer_trial()` / `text_referral_offer_paid()` → разный текст, оба с «+30 дней», keyboard
  содержит `callback_data=CB_REFERRAL`.

## Стратегия тестирования

- Юнит текстов (чистые функции).
- SQL-проверки — postgres-smoke/CI (существующие таблицы).

## Риски и откат

- Риск: burst на деплое → kill-switch `REFERRAL_OFFER_ENABLE=false`.
- Откат: revert-коммит (новых таблиц/колонок нет).

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] ruff check + format — чисто
- [ ] postgres-smoke — проходит
- [ ] коммиты маленькие, на русском, без `Co-Authored-By`
