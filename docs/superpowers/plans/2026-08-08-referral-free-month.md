# Plan — Рефералка: бесплатный месяц за друга (C1)

- **Дата:** 2026-08-08
- **Spec:** [`docs/superpowers/specs/2026-08-08-referral-free-month-design.md`](../specs/2026-08-08-referral-free-month-design.md)
- **Ветка:** `feat/referral-free-month`

## Сводка

+30 дней подписки прямому рефереру за первую оплату друга (тариф ≥1 мес), идемпотентно, кешбек L1/L2
сохраняется. 6 фаз: миграция → домен → репо → grant_days (DRY) → хук fulfillment → API/UI (бот+фронт).

## Фаза 1 — Миграция

### 1.1 `migrations/071_referral_bonus_awards.sql`
- Таблица `referral_bonus_awards(award_id, referrer_user_id, payer_user_id, level, days_awarded,
  related_plan_id, correlation_id, created_at)`; `UNIQUE(referrer_user_id, payer_user_id)`.

## Фаза 2 — Домен

### 2.1 `domain/referral.py`
- `REFERRAL_BONUS_DAYS = 30`; `REFERRAL_BONUS_QUALIFYING_PLANS = {ONE_MONTH, THREE_MONTHS, SIX_MONTHS, ONE_YEAR}`;
  `is_qualifying_plan_for_bonus(plan_id) -> bool`; `REFERRAL_BONUS_LEVEL = 1`.

## Фаза 3 — Контракты + репозитории

### 3.1 `persistence/referral_contracts.py`
- `ReferralBonusAwardRecord`; `ReferralBonusAwardRepository` (`award_if_absent(...)` -> bool,
  `count_awarded(user)` -> int).

### 3.2 `persistence/postgres_referral.py` + `in_memory.py`
- `PostgresReferralBonusAwardRepository` (с `..._in_connection`); `InMemoryReferralBonusAwardRepository`.

## Фаза 4 — grant_subscription_days (DRY)

### 4.1 `application/subscription_grant.py` (новый)
- Извлечь логику из `handle_admin_add_days`: `grant_subscription_days(pool, vless_provider,
  internal_user_id, days, *, actor, correlation_id) -> GrantResult` (UPSERT snapshot + VLESS + аудит
  `admin_subscription_actions`).
### 4.2 `web_api/admin_users.py`
- Рефактор `handle_admin_add_days` вызывать `grant_subscription_days(actor=...)`.

## Фаза 5 — Хук fulfillment + нотификация

### 5.1 `runtime/fulfillment_processor.py`
- `award_referral_bonus_month_best_effort(pool, vless_provider, payer_internal_user_id, plan_id,
  correlation_id) -> BonusAward | None`: resolve L1 → qualifying gate →
  `PostgresReferralBonusAwardRepository.award_if_absent` → при True `grant_subscription_days(referrer, 30,
  actor="referral_bonus")` → `BonusAward`.
- В `process_fulfillment` блок `should_process_referral` после комиссий.
- `_notify_referrer_of_bonus_best_effort` — бот-сообщение «🎉 Друг оплатил — вам +30 дней (бесплатный месяц)».

## Фаза 6 — Видимость (ReferralInfo + web_api + бот + фронтенд)

### 6.1 `application/referral_handler.py`
- `ReferralInfo.bonus_days_awarded: int`; считать в `get_referral_info` (`bonus_repo.count_awarded`); прокинуть репо.
### 6.2 `web_api/referral.py`
- `handle_get_referral_stats` отдаёт `bonus_days_awarded`.
### 6.3 `bot_transport/storefront_ui.py`
- `text_referral_program`: блок «🎁 Месяц за друга» + строка «🎁 Подарено дней: N».
### 6.4 Фронтенд
- `entities/referral/{types,api}.ts` — `bonus_days_awarded`; `ReferralCard.tsx` — плитка;
  `ReferralSection.tsx` — ведущий оффер «месяц за друга».

## Стратегия тестирования

- `tests/test_domain_referral.py` — qualifying gate, константы.
- Новый тест: idempotent award (`InMemory`/postgres) + `grant_subscription_days` (StubVlessProvider);
  fulfillment: первая оплата 1m → award+grant+notify; повторная → нет; 1d → нет.
- Фронтенд: `ReferralCard` рендерит плитку «Подарено дней».

## Риски и откат

- Риск: `grant_subscription_days` для реферера без активной подписки создаёт `custom:30` — это корректно
  («бесплатный месяц»). Если реферер на триале — продлевается/активируется.
- Откат: миграция additive (+таблица); отключить хук одним revert-коммитом; данные наград сохраняются.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_postgres_mvp_smoke_local.py` (миграция/UNIQUE) — зелёный
- [ ] `cd frontend && npm test && npm run lint && npm run build` — зелёные
- [ ] ruff чист; коммиты маленькие, на русском, без `Co-Authored-By`
