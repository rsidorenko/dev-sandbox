# Plan — лимит активаций промокода на одного пользователя

- **Дата:** 2026-08-19
- **Spec:** `docs/superpowers/specs/2026-08-19-promo-per-user-limit-design.md`
- **Ветка:** `feat/promo-per-user-limit` (TBD: одна задача = одна ветка = один PR, жизнь ≤ 24 ч)

## Сводка

Per-user лимит активаций промокода («Единожды»/«Неограниченно») через колонку
`promo_codes.max_redemptions_per_user` (NULL = ∞): миграция 074 + enforcement на всех
поверхностях (применение/оплата/погашение) + админ-форма/API + фронтенд. Одна фаза,
11 задач.

## Фаза 1 — реализация

### Задача 1.1 — миграция 074 + реестр
- **Файлы:** `backend/migrations/074_promo_per_user_limit.sql`,
  `backend/tests/test_postgres_migrations.py`
- **Что сделать:** ADD COLUMN `max_redemptions_per_user INTEGER NULL CHECK (>=1)`;
  backfill `= 1`; DROP CONSTRAINT `promo_redemptions_user_code_uniq`; INDEX
  `(code, internal_user_id)`. Аппенд в реестр + контракт-тест.
- **Acceptance:** контракт-тест 074 зелёный; DDL не попадает под запрещённые фразы.

### Задача 1.2 — домен
- **Файлы:** `backend/src/app/domain/promo.py` (`PromoCode`, `per_user_exhausted`)
- **Что сделать:** поле `max_redemptions_per_user: int | None = None` в конец dataclass;
  чистая функция `per_user_exhausted(promo, used_count)`; обновить docstring
  `redeem_block_reason`.
- **Acceptance:** старые конструции `PromoCode(...)` без нового поля валидны.

### Задача 1.3 — контракты + Postgres
- **Файлы:** `persistence/promo_contracts.py`, `persistence/postgres_promo.py`
- **Что сделать:** Protocol `count_for_user`; `record_from_row` tolerant-чтение;
  `_UPDATABLE_COLUMNS`/`_PROMO_SELECT_COLUMNS`/`create` INSERT; instance
  `count_for_user` + static `count_for_user_in_connection`; `redeem_in_connection` →
  `ON CONFLICT (idempotency_key) DO NOTHING`.
- **Acceptance:** SELECT/INSERT/UPDATE симметричны по колонкам.

### Задача 1.4 — in-memory двойник
- **Файлы:** `persistence/in_memory.py`
- **Что сделать:** `_PROMO_UPDATABLE` + `count_for_user`; в `redeem` убрать дедуп по
  (code, user), оставить idempotency_key.
- **Acceptance:** зеркалит Postgres-семантику.

### Задача 1.5 — сервис погашения
- **Файлы:** `application/promo_service.py`; call-site'ы в `web_api/payment.py` (2),
  `application/balance_payment_service.py` (3), `bot_transport/runtime_facade.py` (4)
- **Что сделать:** `resolve_active_promo` — обязательный kw-only `promo_redemption_repo` +
  проверка `per_user_exhausted` → None; `_redeem_atomic` — count-gate под advisory-локом;
  обновить устаревшие комментарии про UNIQUE.
- **Acceptance:** race-safe (лок сериализует COUNT→INSERT→INCREMENT); все 9 call-site'ов
  передают repo.

### Задача 1.6 — бот
- **Файлы:** `bot_transport/runtime_facade.py`, `bot_transport/storefront_ui.py`
- **Что сделать:** `_apply_promo_to_user` → статус `already_used`; маппинги в
  `_handle_promo_code_input` и `_handle_broadcast_promo_claim`; `_active_applied_promo`
  → None; новый текст `text_promo_already_used`.
- **Acceptance:** повторное применение once-кода даёт понятный текст; бейдж скрывается.

### Задача 1.7 — web API
- **Файлы:** `web_api/promocode.py`
- **Что сделать:** apply → 409 `already_used`; GET → `valid: false` при исчерпании.
- **Acceptance:** fail-closed, форма ответа не меняется.

### Задача 1.8 — админ API
- **Файлы:** `web_api/admin_promocodes.py`
- **Что сделать:** `_promo_to_dict`; create/update парсинг `max_redemptions_per_user`
  (int >= 1 или null) → 400 `invalid_max_redemptions_per_user`.
- **Acceptance:** round-trip 1 ↔ null работает.

### Задача 1.9 — фронтенд
- **Файлы:** `frontend/src/entities/admin/types.ts`,
  `frontend/src/features/admin/PromocodeFormModal.tsx`,
  `frontend/src/app/admin/promocodes/page.tsx`,
  `frontend/src/features/promocode/api.ts`
- **Что сделать:** типы; zod `per_user_mode` (once/unlimited) + переключатель в форме;
  payload `1 | null`; строка в списке `1 / юзера | ∞ / юзера`; ошибка `already_used`.
- **Acceptance:** `npm run build` проходит; create/edit отображают режим корректно.

### Задача 1.10 — тесты
- **Файлы:** `backend/tests/test_promo_domain.py`, `test_promo_repositories.py`,
  `test_promo_redemption.py`, `test_web_api_promocode.py`, `test_broadcast_promo_claim.py`,
  `test_postgres_migrations.py`, `test_admin_promocodes_integration.py`
- **Что сделать:** по списку из spec (истинность `per_user_exhausted`; repo-семантика;
  once/unlimited погашение; idempotency сохранена; 409; бейдж; контракт миграции; админ CRUD).
- **Acceptance:** все новые/обновлённые тесты зелёные.

### Задача 1.11 — валидация и доставка
- **Что сделать:** `python -m pytest -q`; `run_mvp_repo_release_health_check.py`;
  `npm run build`; коммиты на русском (без Co-Authored-By), пуш, MR в GitLab.
- **Acceptance:** suite зелёный, MR создан.

## Стратегия тестирования

- Юнит: домен/репозитории/сервис — в перечисленных файлах (in-memory композиции).
- Интеграционные (opt-in, локальный PG OSPanel `:5433`): `test_admin_promocodes_integration.py`,
  `test_web_api_promocode.py`; миграция проверяется реестром+контрактом.
- E2e после деплоя: once-код применить→оплатить→повторно применить (ожидание отказа);
  unlimited-код — две оплаты со скидкой, счётчик +2.

## Риски и откат

- Риск: DROP UNIQUE переносит per-user enforcement в код → митигация: единая точка
  `_redeem_atomic` под advisory-локом; `redeem_in_connection` имеет ровно одного caller'а.
- Риск: деплой мгновенно закрывает лазейку для старых кодов (backfill =1) → запланировать
  на спокойное окно, предупредить поддержку.
- Откат: revert MR; миграция 074 обратно не откатывается автоматически (ADD COLUMN/DROP
  CONSTRAINT безобратимы в рамках policy) — при необходимости ручной DOWN-скрипт оператором.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист (`ruff check` + `ruff format --check`)
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [ ] Миграция 074 безопасна (additive + DROP CONSTRAINT без потери данных)
- [ ] Spec и план обновлены при расхождении
