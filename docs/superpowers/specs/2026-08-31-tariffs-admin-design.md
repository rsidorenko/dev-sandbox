# Design Spec — Тарифы в админке: цены + глобальная скидка

- **Автор / ответственный:** rsidorenko / Claude Code
- **Дата:** 2026-08-31
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-08-31-tariffs-admin.md`

## Контекст и проблема

Цены на VPN-тарифы зашиты в коде (`backend/src/app/domain/plans.py::_PLANS`: 1d=12, 7d=99,
14d=169, 1m=249, 3m=699, 6m=1259, 365d=2199 ₽) и продублированы вручную ещё в трёх местах:
зеркало в БД (`subscription_plans`, миграция 051 — приложением не читается), хардкод фронта
(`frontend/src/shared/config/site.ts::siteConfig.tariffs`), точные значения в тестах. Любая
смена цены или распродажа = правка кода + деплой. Промо-механика скидок (процент/фикс/
per-tariff, клэмп [1..base]) уже есть: canonical `apply_promo_discount` в
`backend/src/app/domain/promo.py`, отображение «X ₽ вместо Y» умеют бот и сайт. Глобальной
скидки «всем сразу» нет.

**Цель:** вкладка «Тарифы» в админ-панели, где оператор может:
1. менять цены 7 фиксированных тарифов на лету — изменение применяется везде: бот
   (отображение + чекаут), сайт (отображение + чекаут), вебхук-верификация, оплата с баланса;
2. устанавливать глобальную скидку для всех («как будто всем применили один промокод, но без
   промокода»): фиксированная сумма ₽ ИЛИ процент, с красивым отображением «X ₽ вместо Y».

## Цели

- Runtime-редактирование цен 7 тарифов из админки без деплоя.
- Глобальная скидка (percent | fixed_rubles) с публичным лейблом, отображается всем.
- Единая цена между созданием чекаута и вебхук-сверкой (иначе — 409 amount_mismatch).
- Публичный API тарифов для фронта (сегодня цены захардкожены на фронте).

## Non-goals

- Не редактируем цену доп. устройства (80 ₽) и цену дня custom-тарифа (15 ₽) — остаются в коде.
- Глобальная скидка НЕ распространяется на add-device платежи и custom:N тарифы.
- Не трогаем `subscription_plans` (зеркало 051 остаётся справочным).
- Не делаем расписание скидок (старт/финиш по времени) — включение/выключение вручную.
- Без env-флага: пустые оверрайды + неактивная скидка = текущее поведение.

## Текущее состояние

- `domain/plans.py`: `_PLANS` — единственный источник цен; `get_plan()` (sync/pure, включая
  `custom:N`); `calculate_total_price(plan, device_count)` — чистая математика.
- Чекаут: web `web_api/payment.py::handle_create_payment` (~117), bot
  `bot_transport/runtime_facade.py::_process_yookassa_payment` (~1719) /
  `_process_cryptobot_payment` (~1815), баланс `application/balance_payment_service.py::
  pay_plan_from_balance` (~93) — все считают от sync `get_plan`.
- Вебхук: `runtime/payment_webhook_helpers.py::expected_subscription_kopecks` (~58)
  перепроизводит цену из кода и сверяет ±1 коп. Прецедент metadata: add-device уже несёт
  `expected_amount_kopecks`, промо — `{promo_code, discount_kopecks}`; metadata эхом
  возвращается провайдером (не подделывается клиентом).
- Промо: `domain/promo.py::apply_promo_discount` → `PromoPrice`; `application/promo_service.py::
  resolve_active_promo`; отображение: бот `bot_transport/storefront_ui.py` (`buy_vpn_keyboard`,
  `text_device_select`, `text_purchase_summary`), фронт `features/promocode/discount.ts::
  discountedRubles`.
- Админка: паттерн CRUD = `web_api/admin_promocodes.py` + фронт `app/admin/promocodes/page.tsx`,
  `features/admin/api.ts`, вкладки в `app/admin/layout.tsx:101-104`; аудит-прецедент —
  миграция 064 `admin_subscription_actions`.
- Следующая миграция — **076** (075 занята `subscription_token_permanent`).

## Предлагаемое решение

1. **`plan_price_overrides`** (plan_id PK ∈ 7 фиксированных, price_rubles, updated_by/at):
   отсутствие строки = код-дефолт. Эффективный план = `dataclasses.replace(plan, price_rubles=…)`,
   `calculate_total_price` остаётся чистой sync-функцией.
2. **`pricing_global_discount`** (singleton): is_active, discount_kind (percent|fixed_rubles),
   discount_value, public_label, updated_by/at. Засеяна неактивной. Без redemption/учёта.
3. **`PlanPricingProvider`** (`application/plan_pricing.py`): async, кэш in-process TTL 30 с,
   `invalidate_all_plan_pricing()` после админ-записи (бот и web в одном процессе → мгновенно
   на обеих поверхностях; TTL — страховка на будущее). `pool is None` и ошибка БД → код-дефолты
   (fail-open: чекаут не должен падать; warning в лог).
4. **Домен `domain/pricing.py`** (pure): `GlobalDiscount`, `EffectivePrice` (winner:
   none|promo|global), `apply_global_discount` (только FIXED_PLAN_IDS, клэмпы как у промо),
   `effective_plan_price` — **best-of**: промо и глобальная считаются на одной базе, берётся
   большая скидка; **при равенстве выигрывает промо** (сохраняет redemption-семантику).
5. **Защита «летающих» платежей**: при создании платежа в metadata пишем `base_amount_kopecks`
   (пост-оверрайд, до скидки); вебхук предпочитает metadata, fallback — re-derive (legacy).
   `promo_code` в metadata — только когда выиграл промо → global-платежи не погашают промокод
   пользователя.
6. **`GET /api/v1/plans`** (без auth, Cache-Control 30 с): эффективные цены + discounted +
   global_discount + custom_day_price + devices. Фронт: `usePlans()` с fallback на siteConfig,
   best-of зеркало `effectivePrice.ts`.
7. **Admin API + UI**: GET/PATCH/DELETE `/api/v1/admin/plans[/{plan_id}]`,
   `PUT /api/v1/admin/plans/global-discount`; аудит `admin_pricing_actions` (064-паттерн);
   вкладка «Тарифы» в `app/admin/layout.tsx`.

## Альтернативы

- **Повысить `subscription_plans` до authoritative** — отвергнуто: миграция 051 объявляет
  таблицу зеркалом «FROM code»; будущая 051-подобная синка молча затрёт админ-цены; нужна
  различимость «дефолт vs оверрайд».
- **Скидка как автоматический промокод на всех** — отвергнуто: ломает семантику
  redemption-журнала/аудитории/лимитов промокодов.
- **Стак скидок (глобальная + промо подряд)** — отвергнуто владельцем: выбран best-of.

## Влияние

- **Файлы:** новые `domain/pricing.py`, `application/plan_pricing.py`,
  `persistence/postgres_pricing.py`, `web_api/plans.py`, `web_api/admin_plans.py`,
  `app/admin/plans/page.tsx`, `features/plans/*`; правки чекаутов/webhook/бот-UI/фронт-отображения.
- **Миграции БД:** 076_pricing_admin.sql — три новые таблицы (аддитивно, пусто = текущее
  поведение); регистрация в `tests/test_postgres_migrations.py`.
- **Безопасность:** админ-эндпоинты за `_with_admin_auth` + `_with_admin_csrf`; публичный
  `/api/v1/plans` отдаёт только публичные величины (без себестоимости/метаданных).
- **Биллинг:** деньги — целые рубли в домене, `*_kopecks` на границах; вебхук-гейт остаётся
  ±1 коп; идемпотентность промо-redemption не меняется (погашение только при winner==promo).
- **Retention / VPN-провайдер:** не затрагивается.

## Открытые вопросы

- Нет (решения зафиксированы владельцем: best-of, охват 7 тарифов, редактирование только цен).

## Критерии приёмки

- [ ] Смена цены 1m в админке → бот `/plans` и лендинг показывают новую цену; чекаут списывает новую сумму.
- [ ] Глобальная скидка 20% → «X ₽ вместо Y» в боте и на сайте; чекаут списывает discounted.
- [ ] Платёж, созданный до смены цены, проходит вебхук-гейт после смены (base_amount_kopecks).
- [ ] Промокод + глобальная: списывается большая из скидок; промо-погашение только когда промо выиграл.
- [ ] `python -m pytest -q` зелёный; интеграционные тесты admin/plans проходят на локальном PG.
- [ ] Миграция 076 зарегистрирована; ruff чист; фронт `vitest` + `tsc --noEmit` зелёные.
