# Design Spec — Рефералка: бесплатный месяц за друга (C1)

- **Дата:** 2026-08-08
- **Статус:** approved
- **Roadmap:** [`2026-08-08-bravada-growth-roadmap-design.md`](./2026-08-08-bravada-growth-roadmap-design.md) (пункт C1)
- **Plan:** [`docs/superpowers/plans/2026-08-08-referral-free-month.md`](../plans/2026-08-08-referral-free-month.md)
- **Ветка:** `feat/referral-free-month`

## Контекст и проблема

Рефералка даёт только кешбек (% от оплат друга на баланс: L1 10–35%, L2 1–5%). «~87 ₽» слабо мотивирует
делиться ссылкой. По лучшему паттерну привлечения добавляем **награду днями**: друг впервые оплачивает
тариф ≥ 1 месяца → прямой пригласивший (L1) получает **+30 дней** подписки. Кешбек L1/L2 **сохраняется**;
две награды сосуществуют. Награду активно обозначаем на сайте и в боте.

## Цели

- +30 дней подписки прямому рефереру, когда приглашённый **впервые** оплачивает тариф ≥ 1 месяца.
- Идемпотентность: ровно один раз на пару (referrer, payer).
- Кешбек L1/L2 не трогать.
- Активно промотировать на лендинге, в кабинете и в боте; нотифицировать реферера при награде.

## Non-goals

- Награда L2 днями, двусторонняя награда (другу тоже) — бэклог.
- Автовозврат бонусных дней при чарджбэке — ручной ревёрс оператором.
- Не меняем триггеры/ставки кешбека.

## Текущее состояние

- `domain/referral.py` — `build_commissions_for_payment` (L1/L2 % → копейки).
- `runtime/fulfillment_processor.py` — `_process_referral_commissions_best_effort` (кредитует комиссии
  идемпотентно), `_notify_referrers_of_commission_best_effort`, `process_fulfillment(pool, vless_provider)`;
  `should_process_referral`-блок (стр. ~498) — точка хука.
- `persistence/referral_contracts.py` + `postgres_referral.py` + `in_memory.py`.
- `web_api/admin_users.py:handle_admin_add_days` — готовая логика выдачи дней (продлевает
  `subscription_snapshots.active_until_utc` / создаёт `custom:{days}`, реактивирует, VLESS, аудит в
  `admin_subscription_actions` без искажения выручки).
- `application/referral_handler.py:ReferralInfo`; `web_api/referral.py:handle_get_referral_stats`.
- `bot_transport/storefront_ui.py:text_referral_program`; фронтенд `ReferralSection`, `ReferralCard`, `entities/referral`.

## Предлагаемое решение

- **Награда днями (отдельный ledger):** таблица `referral_bonus_awards` с `UNIQUE(referrer_user_id,
  payer_user_id)` — идемпотентность «один раз за друга».
- **Триггер/гейт:** только `plan_id ∈ {1m,3m,6m,365d}` (домен-константа `REFERRAL_BONUS_QUALIFYING_PLANS`).
- **Выдача:** переиспользуемая `grant_subscription_days()` (извлекается из `handle_admin_add_days`);
  бонус-путь вызывает с `actor="referral_bonus"`. Дни — не выручка, аудит в `admin_subscription_actions`.
- **Хук:** `award_referral_bonus_month_best_effort()` в fulfillment — резолвит L1, гейт, идемпотентный
  insert, grant 30 дней, возвращает `BonusAward` для нотификации. Best-effort.
- **Видимость:** `ReferralInfo.bonus_days_awarded` → `web_api` stats → кабинет (плитка) + бот (счётчик);
  лендинг — ведущий оффер «Приведи друга — получи месяц бесплатно».

## Влияние

- **Файлы (бэкенд):** миграция `071`; `domain/referral.py`; `persistence/referral_contracts.py`,
  `postgres_referral.py`, `in_memory.py`; новое `application/subscription_grant.py` (+ рефактор
  `web_api/admin_users.py`); `runtime/fulfillment_processor.py`; `application/referral_handler.py`;
  `web_api/referral.py`.
- **Файлы (бот):** `bot_transport/storefront_ui.py`.
- **Файлы (фронтенд):** `entities/referral/{types,api}.ts`, `features/referral/ReferralCard.tsx`,
  `widgets/landing/ReferralSection.tsx`.
- **Миграции:** +1 (`071_referral_bonus_awards`).
- **Безопасность/биллинг:** бонусные дни — не выручка (не в `billing_events_ledger`); идемпотентность через
  UNIQUE; money-полей нет (дни). VLESS-провижининг переиспользует готовый путь.

## Критерии приёмки

- [ ] Друг впервые платит `1m` → L1-реферер `active_until_utc += 30 дней`, VLESS перепровижен, бот-нотификация.
- [ ] Тот же друг платит снова → награды НЕТ (UNIQUE).
- [ ] Друг платит `1d` → награды НЕТ (не qualifying).
- [ ] Кешбек L1/L2 начисляется как прежде.
- [ ] На лендинге/в кабинете/в боте — промо «месяц за друга» и счётчик подаренных дней.
- [ ] `pytest -q`, postgres-smoke, `npm test/lint/build`, ruff — зелёные.

## Открытые вопросы

- Нет (решения согласованы: триггер = первая оплата ≥1 мес, охват = только L1 +30 дней).
