# Plan — Holdout уведомлений

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-notification-holdout-design.md`
- **Ветка:** `feat/notification-holdout`

## Сводка

Детерминированный holdout ~10% nudge-уведомлений с наблюдаемой записью 'holdout',
kill-switch и замером эффекта (оплата ≤7д) в админке. 3 фазы: ядро → планировщик/админка
→ валидация/MR.

## Фаза 1 — Ядро

### Задача 1.1 — Модуль holdout и миграция
- **Файлы:** `backend/src/app/application/notification_holdout.py` (новый),
  `backend/migrations/095_notification_holdout_status.sql`
- **Что сделать:** whitelist типов, флаг/процент из env, детерминированный
  `is_notification_holdout`; CHECK delivery_status + 'holdout'.
- **Acceptance:** unit: детерминизм, не-eligible всегда False, процент-кламп;
  миграция идемпотентна и допускает 'holdout'.

## Фаза 2 — Планировщик и админка

### Задача 2.1 — Holdout в _send_notification
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** до каналов — holdout → INSERT 'holdout' ON CONFLICT DO NOTHING →
  False; сбой вставки → обычная отправка.
- **Acceptance:** тест с фейк-pool: holdout-пользователь не получает TG/web,
  запись со статусом есть; control — отправка как раньше.

### Задача 2.2 — Админ-агрегат
- **Файлы:** `backend/src/app/web_api/admin_stats.py`, `frontend/src/features/admin/QualitySection.tsx`
- **Что сделать:** `_NOTIFICATION_HOLDOUT_STATS` (GROUP BY type+status за период,
  оплата ≤7д после sent_date, без грантов) + карточка в QualitySection.
- **Acceptance:** интеграционный тест на синтетике; фронт-рендер карточки.

## Фаза 3 — Валидация и MR

- compose env; полный pytest, ruff, release-checks, frontend; MR, CI, merge.

## Стратегия тестирования

- Unit: резолвер, планировщик (mock pool/http).
- Интеграция (DATABASE_URL): миграция + агрегат (delivered/holdout/оплата).

## Риски и откат

- Риск: недополученная конверсия у 10% — сознательная цена измерения; откат —
  `NOTIFICATION_HOLDOUT_ENABLE=0` без деплоя.
- Риск: status CHECK на старых строках — новые значения только расширяются.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] release-checks / ruff / frontend — зелёные
- [ ] Коммиты на русском, без Co-Authored-By
- [ ] Spec/plan соответствуют реализации
