# Plan — целостность обратной связи

- **Дата:** 2026-09-20
- **Spec:** `docs/superpowers/specs/2026-09-20-feedback-integrity-design.md`
- **Ветка:** `fix/feedback-integrity`

## Сводка

Разделить feedback по surface, сделать frontend-честным при ошибках и поправить два UX-дефекта.

## Фаза 1 — хранение

### Задача 1.1 — добавить surface
- **Файлы:** migration `097`, `application/trial_feedback.py`, web handler.
- **Что сделать:** колонка, backfill, новая уникальность, строгая валидация и upsert.
- **Acceptance:** Telegram/web строки не перезаписывают друг друга.

### Задача 1.2 — вывести surface в статистику
- **Файлы:** backend admin stats, frontend admin types/page.
- **Что сделать:** surface breakdown и подписи.
- **Acceptance:** распределение доступно в админке.

## Фаза 2 — UX

### Задача 2.1 — обработать ошибку feedback API
- **Файлы:** `ConnectionCheckCard.tsx` и тест.
- **Что сделать:** показывать ошибку и сохранять возможность повторить.
- **Acceptance:** экран благодарности появляется только после `ok=true`.

### Задача 2.2 — исправить skip и keys
- **Файлы:** storefront UI, QualitySection и тесты.
- **Что сделать:** вернуть skip к тарифам, сделать заголовки holdout уникальными.
- **Acceptance:** нужный callback и отсутствие React warning.

## Стратегия тестирования

- Unit и PostgreSQL integration для recorder/migration.
- ASGI test web surface.
- Frontend component/API/admin tests.
- Полные backend/frontend проверки.

## Риски и откат

- Риск: изменение unique key → migration сначала backfill-ит surface, затем создаёт новый индекс.
- Откат: код можно откатить; additive колонку сохраняем, данные совместимы.

## Чек-лист перед PR

- [ ] Полный pytest зелёный
- [ ] PostgreSQL integration зелёный
- [ ] Frontend lint/typecheck/unit/build зелёные
- [ ] Release health check проходит
