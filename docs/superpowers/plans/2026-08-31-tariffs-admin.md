# Plan — Тарифы в админке: цены + глобальная скидка

- **Дата:** 2026-08-31
- **Spec:** `docs/superpowers/specs/2026-08-31-tariffs-admin-design.md`
- **Ветка:** `feat/admin-plans-pricing` (TBD: одна задача = одна ветка = один PR, жизнь ≤ 24 ч)

## Сводка

Runtime-цены 7 тарифов (таблица оверрайдов + провайдер с кэшем) и глобальная скидка
(best-of с промокодом), витрина: админ-вкладка «Тарифы», публичный `/api/v1/plans`,
отображение в боте и на фронте. 7 фаз, ~10 коммитов.

## Фаза 1 — Домен, БД, провайдер

### Задача 1.1 — глобальная скидка и best-of (домен)
- **Файлы:** `backend/src/app/domain/pricing.py` (новый), `backend/tests/test_domain_pricing.py` (новый)
- **Что сделать:** `GlobalDiscount`, `EffectivePrice`, `apply_global_discount` (клэмпы/округление
  как у промо, только FIXED_PLAN_IDS, не is_addon, не custom), `effective_plan_price` (best-of,
  tie → promo), `global_discount_label`, `validate_global_discount` (делегирует `promo.validate_discount`).
- **Acceptance:** юнит-тесты best-of/tie/клэмпов/охвата зелёные; модуль pure (без I/O).

### Задача 1.2 — миграция 076 + репозиторий
- **Файлы:** `backend/migrations/076_pricing_admin.sql` (новый),
  `backend/src/app/persistence/postgres_pricing.py` (новый),
  `backend/tests/test_postgres_migrations.py` (регистрация)
- **Что сделать:** таблицы `plan_price_overrides`, `pricing_global_discount` (singleton, засеян
  неактивным), `admin_pricing_actions` (064-паттерн); репозиторий `load_state`/`upsert_plan_price`/
  `delete_plan_price`/`set_global_discount`/`record_action` (паттерн `postgres_promo.py`).
- **Acceptance:** миграция в реестре; контракт-тесты реестра зелёные.

### Задача 1.3 — PlanPricingProvider
- **Файлы:** `backend/src/app/application/plan_pricing.py` (новый),
  `backend/tests/test_plan_pricing_provider.py` (новый),
  `backend/src/app/domain/plans.py` (комментарий-хедер про fallback-роль `_PLANS`)
- **Что сделать:** `PricingState`, `PlanPricingProvider` (TTL 30 с, asyncio.Lock, fail-open на
  код-дефолты), `get_plan_pricing(pool)` (per-pool singleton), `invalidate_all_plan_pricing()`,
  общий хелпер `resolve_effective_charge(...)`.
- **Acceptance:** тесты дефолт/оверрайд/custom-пасsthrough/TTL/инвалидация/pool=None/fallback.

## Фаза 2 — Чекаут и вебхук

### Задача 2.1 — web/bot/balance чекаут
- **Файлы:** `backend/src/app/web_api/payment.py`, `backend/src/app/bot_transport/runtime_facade.py`,
  `backend/src/app/application/balance_payment_service.py`, `backend/src/app/application/purchase_handler.py`,
  `backend/src/app/bot_transport/storefront_ui.py`
- **Что сделать:** ценовые call-site'ы → `await pricing.get_plan()/get_fixed_plans()`; best-of через
  `resolve_effective_charge`; metadata `base_amount_kopecks` всегда + `promo_code`/`discount_kopecks`
  (winner==promo) или `discount_kopecks`+`global_discount` (winner==global); бот-UI принимает
  `global_discount=None`; balance: списание от discounted, gate погашения `winner == "promo"`.
- **Acceptance:** existing-тесты компилируются (дефолтные параметры), новые кейсы оплаты зелёные.

### Задача 2.2 — вебхук + промо-preview
- **Файлы:** `backend/src/app/runtime/payment_webhook_helpers.py`, `backend/src/app/web_api/promocode.py`,
  `backend/tests/test_yookassa_webhook.py`
- **Что сделать:** amount gate предпочитает `base_amount_kopecks` из metadata (fallback re-derive);
  план — через provider (нужен только duration_days); база промо-redemption — metadata-preferred;
  preview промокода — provider + `effective_plan_price`.
- **Acceptance:** регресс-тест гонки (платёж до смены цены проходит гейт после смены); legacy без поля проходит.

## Фаза 3 — Публичный API тарифов

### Задача 3.1 — GET /api/v1/plans
- **Файлы:** `backend/src/app/web_api/plans.py` (новый), `backend/src/app/web_api/app.py`
- **Что сделать:** без auth, `Cache-Control: public, max-age=30`; планы (base/discounted/
  has_discount), global_discount, custom_day_price_rubles, extra_device_price_rubles, devices.
- **Acceptance:** ручной curl на ASGI + интеграционный тест.

## Фаза 4 — Admin API

### Задача 4.1 — CRUD тарифов и глобальной скидки
- **Файлы:** `backend/src/app/web_api/admin_plans.py` (новый), `backend/src/app/web_api/app.py`
- **Что сделать:** GET (default/effective/is_overridden + gd-состояние), PATCH `{price_rubles}`
  (int 1..100000), DELETE (сброс, 404 если нет оверрайда), PUT global-discount (валидация,
  аудит, `invalidate_all_plan_pricing()` после каждой записи).
- **Acceptance:** интеграционный тест (шаблон `test_admin_promocodes_integration.py`): CRUD,
  400-ы, 403 без CSRF.

## Фаза 5 — Админ-вкладка «Тарифы»

### Задача 5.1 — UI
- **Файлы:** `frontend/src/app/admin/plans/page.tsx` (новый), `frontend/src/features/admin/api.ts`,
  `frontend/src/entities/admin/types.ts`, `frontend/src/app/admin/layout.tsx`
- **Что сделать:** таблица 7 тарифов (inline edit + сброс) + карточка скидки (toggle/kind/value/label);
  классы `features/admin/ui.ts`, react-hook-form + zod, ошибки на русском.
- **Acceptance:** вкладка после «Промокодов»; vitest/tsc зелёные.

## Фаза 6 — Фронт-отображение

### Задача 6.1 — динамические цены
- **Файлы:** `frontend/src/entities/plans/types.ts` (новый), `frontend/src/features/plans/PlansProvider.tsx` (новый),
  `frontend/src/features/plans/effectivePrice.ts` (новый), `frontend/src/app/(site)/layout.tsx`,
  `frontend/src/widgets/landing/TariffsSection.tsx`, `frontend/src/app/(site)/(protected)/payment/[planId]/page.tsx`
- **Что сделать:** `usePlans()` (fetch 1 раз, fallback siteConfig); best-of зеркало (промо-нога =
  `discountedRubles`); strikethrough «X ₽ вместо Y», подпись «Акция» при winner=global; custom:N —
  по-прежнему 15 ₽/день из конфига; итоговая сумма — ответ сервера.
- **Acceptance:** `effectivePrice.test.ts`, обновлённый `TariffsSection.test.tsx`; fallback работает.

## Стратегия тестирования

- Юнит: `test_domain_pricing.py`, `test_plan_pricing_provider.py`, расширение `test_balance_payment_service.py`
  (global wins → debit discounted, redemption НЕ происходит).
- Интеграция (локальный PG, OSPanel :5433): `test_admin_plans_integration.py` — CRUD → чекаут по
  оверрайду с `base_amount_kopecks`, global discount → `/api/v1/plans` + best-of чекаут,
  регресс-тест гонки, legacy-metadata, 400/403.
- Фронт: vitest (`effectivePrice.test.ts`, provider, обновлённый TariffsSection), `tsc --noEmit`.
- `test_domain_plans.py`/`test_purchase_handler.py`/`test_critical_paths.py` — без правок (дефолты = fallback).

## Риски и откат

- Риск: гонка «создан до смены цены — вебхук после» → закрыта `base_amount_kopecks` в metadata (прецедент add-device).
- Риск: sync→async рефактор бота → UI-билдеры остаются sync-pure, gd параметром; `domain/plans.py` не async.
- Риск: per-tariff промо с 0 скидки на плане → best-of выберет global, не печатать «−0 ₽» (branch по winner).
- Риск: fail-open провайдера при падении БД → код-дефолты (возможен завышенный прайс относительно
  оверрайда; безопаснее блокировки чекаутов).
- Откат: revert ветки; таблицы 076 инертны (пустые = текущее поведение); миграцию откатывать не нужно.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист (`ruff check` + `ruff format --check`)
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By` (правило `CLAUDE.md`)
- [ ] Затронутые миграции безопасны (076 аддитивна, пусто = no-op)
- [ ] Spec и plan обновлены, если реальность расходится с планом
