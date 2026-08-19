# Design Spec — лимит активаций промокода на одного пользователя

- **Автор / ответственный:** rsidorenko / Claude Code
- **Дата:** 2026-08-19
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-08-19-promo-per-user-limit.md`

## Контекст и проблема

В админ-панели на вкладке «Промокоды» при создании/редактировании промокода нет способа задать,
сколько раз **один пользователь** может активировать один и тот же код. Нужно два режима:
«единожды» и «неограничено».

При проверке текущего кода выявлена лазейка: пути применения (`_apply_promo_to_user`,
`handle_apply_promocode`) и канонический пересчёт при оплате (`resolve_active_promo`) никогда
не читают журнал `promo_redemptions`. После оплаты «пин» (`promo_applied`) очищается →
пользователь применяет тот же код снова → **скидка выдаётся повторно**. Жёсткое правило
«один раз на юзера» существует только как UNIQUE(code, internal_user_id) в журнале погашений
(`backend/migrations/062_promo_redemptions.sql:18`) — оно подавляет лишь запись в журнал
и прирост счётчика, но не саму скидку.

## Цели

- В админ-форме создания/редактирования промокода — выбор режима активаций на пользователя:
  «Единожды» / «Неограниченно».
- Режим «Единожды» жёстко инфорсится на всех поверхностях: применение (бот/web/рассылка),
  пересчёт при оплате, погашение.
- Режим «Неограниченно» позволяет одному пользователю получать скидку этим кодом при каждой
  оплате; каждое погашение попадает в журнал и считается в `redemptions_count`.
- Существующим кодам при миграции назначается «Единожды» (решение оператора) — лазейка
  повторной скидки закрывается сразу для всех старых кодов.

## Non-goals

- N-на-юзера в UI (в БД колонка INTEGER это допускает в будущем, но форма показывает два варианта).
- Изменение глобального лимита `max_redemptions` (остаётся суммарным потолком погашений).
- Изменение TTL «пина» `promo_applied` (24 ч) и механики broadcast claims (073).

## Текущее состояние

- `promo_codes.max_redemptions` — глобальный лимит (NULL = ∞); per-user колонки нет.
- `promo_redemptions` — журнал погашений; UNIQUE(code, internal_user_id) — единственный
  per-user механизм (срабатывает при погашении, `ON CONFLICT DO NOTHING`).
- `promo_applied` — «намерение», PK = internal_user_id, TTL 24 ч (`domain/promo.py:27`).
- Погашение: `application/promo_service.py` `_redeem_atomic` — advisory-лок на пользователя
  (`pg_try_advisory_xact_lock(hashtext(user))`, строка 234) + `redeem_in_connection` +
  guarded-инкремент `redemptions_count`.
- Админ CRUD: `web_api/admin_promocodes.py` + `frontend/src/features/admin/PromocodeFormModal.tsx`
  + `persistence/postgres_promo.py` (`_UPDATABLE_COLUMNS`, `_PROMO_SELECT_COLUMNS`).

## Предлагаемое решение

- Новая колонка `promo_codes.max_redemptions_per_user INTEGER NULL` (NULL = неограничено,
  1 = единожды; CHECK >= 1). Backfill `= 1` для существующих строк.
- UNIQUE(code, internal_user_id) в журнале заменяется кодовым enforced-лимитом под существующим
  advisory-локом: COUNT→INSERT→INCREMENT критическая секция сериализована на пользователя —
  race-safe. Индекс (code, internal_user_id) для COUNT-проверок.
- Доменная функция `per_user_exhausted(promo, used_count)` — единое определение для всех
  поверхностей: `_apply_promo_to_user` (новый статус `already_used`), web apply (409),
  `resolve_active_promo` (→ None, fail-closed), `_active_applied_promo` (бейдж), `_redeem_atomic`.
- Админ API и форма принимают/показывают поле; режим «Неограниченно» → NULL, «Единожды» → 1.

## Альтернативы

- Оставить UNIQUE и отдельную таблицу для повторных погашений — отвергнуто: сложнее, ноль выгоды.
- Сентинел `0 = неограничено` вместо NULL — отвергнуто: нарушает конвенцию `max_redemptions`.

## Влияние

- **Файлы:** миграция 074; `domain/promo.py`; `persistence/promo_contracts.py`,
  `postgres_promo.py`, `in_memory.py`; `application/promo_service.py`;
  `bot_transport/runtime_facade.py`, `storefront_ui.py`; `web_api/promocode.py`,
  `payment.py`, `admin_promocodes.py`; фронтенд (types, форма, список, ошибки).
- **Миграции БД:** 074 — additive ADD COLUMN + backfill + DROP CONSTRAINT + INDEX.
- **Безопасность:** fail-closed везде (ошибка проверки → полная цена); идемпотентность
  вебхуков не меняется (`ON CONFLICT (idempotency_key)`).
- **Биллинг:** деньги не трогаем; `redemptions_count` для unlimited-кодов начинает считать
  каждое погашение (осознанно).
- **Retention / VPN-провайдер:** не затрагивается.

## Открытые вопросы

- Нет (режим для существующих кодов решён оператором: «Единожды»).

## Критерии приёмки

- [ ] В админ-панели при создании/редактировании можно выбрать «Единожды»/«Неограниченно»;
      значение сохраняется и отображается в списке.
- [ ] Once-код: повторное применение после погашения → отказ `already_used` (бот-текст,
      web 409); скидка при оплате блокируется; второе погашение — no-op.
- [ ] Unlimited-код: повторные применения и оплаты со скидкой; каждая — строка журнала и +1
      счётчика; глобальный `max_redemptions` по-прежнему ограничивает.
- [ ] Тесты покрывают кейсы: домен, репозитории, погашение (once/unlimited/idempotency),
      web 409, broadcast, миграция (реестр+контракт), админ CRUD round-trip.
- [ ] `pytest -q` зелёный.
