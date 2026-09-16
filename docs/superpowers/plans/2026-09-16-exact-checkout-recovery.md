# Plan — Возврат в точный незавершённый заказ

- **Дата:** 2026-09-16
- **Spec:** `docs/superpowers/specs/2026-09-16-exact-checkout-recovery-design.md`
- **Ветка:** `feat/exact-checkout-recovery`

## Фаза 1 — Контекст намерения

### Задача 1.1 — Расширить funnel interaction
- **Файлы:** миграция 087, `application/funnel_events.py`, тесты миграции/funnel.
- **Что сделать:** добавить nullable plan/device/method, строгую валидацию и parser callback.
- **Acceptance:** секреты не сохраняются, некорректный контекст отвергается до SQL.

### Задача 1.2 — Записывать контекст на поверхностях
- **Файлы:** Telegram runtime, web payment, их тесты.
- **Что сделать:** передать контекст в checkout_opened/payment_created.
- **Acceptance:** bot и web используют один контракт события.

## Фаза 2 — Точное продолжение

### Задача 2.1 — Сформировать персональный reminder
- **Файлы:** notification scheduler/texts, conversion tests.
- **Что сделать:** выбрать последнее событие, создать точный Telegram callback и безопасный web path.
- **Acceptance:** fallback остаётся для legacy-строк.

### Задача 2.2 — Показать CTA в web-уведомлении
- **Файлы:** NotificationsBell, API types и unit tests.
- **Что сделать:** отображать allowlisted relative action из payload.
- **Acceptance:** внешние и protocol-relative URL не рендерятся.

## Фаза 3 — Проверка

- [x] Backend targeted — 124 passed, 6 skipped; полный pytest — 2361 passed, 133 skipped.
- [x] Миграционный health check проходит.
- [x] Frontend unit — 153 passed; lint/typecheck/build проходят.
- [x] Playwright checkout — 7 passed.
- [x] `git diff --check` проходит.
