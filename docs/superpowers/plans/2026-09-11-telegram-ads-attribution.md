# Plan — атрибуция Telegram Ads

- **Дата:** 2026-09-11
- **Spec:** `docs/superpowers/specs/2026-09-11-telegram-ads-attribution-design.md`
- **Ветка:** `feat/telegram-ads-attribution`

## Сводка

Аддитивная схема first-touch, безопасный захват `tgads_*`, когортный отчёт и таблица
в админке. Один короткоживущий MR без изменения пользовательского сценария.

## Фаза 1 — захват

### Задача 1.1 — добавить схему и репозитории
- **Файлы:** `backend/migrations/082_telegram_ads_attribution.sql`, application/persistence.
- **Acceptance:** first-touch атомарен и идемпотентен; миграция повторяемо проверяется.

### Задача 1.2 — разобрать start-параметр
- **Файлы:** `bot_transport/normalized.py`, `dispatcher.py`, postgres wiring.
- **Acceptance:** `tgads_*` отделён от `ref_*`; аналитическая ошибка не влияет на bootstrap.

## Фаза 2 — отчёт

### Задача 2.1 — добавить кампании в admin stats
- **Файлы:** `web_api/admin_stats.py`.
- **Acceptance:** выбранная когорта, internal-фильтр, этапы, зрелость, первая/вторая оплата.

### Задача 2.2 — показать таблицу кампаний
- **Файлы:** `frontend/src/entities/admin/types.ts`, `frontend/src/app/admin/stats/page.tsx`.
- **Acceptance:** читаемая таблица и явные определения без ложных CTR/регистраций.

## Фаза 3 — проверка и передача

- [x] Тесты parser/dispatcher/failure/first-touch и PostgreSQL-агрегации.
- [x] Runbook со схемой ссылок, ограничениями истории и откатом.
- [x] Полный pytest, ruff, health-check и frontend unit/typecheck.
- [x] Commit, push и GitLab MR подготовлены; CI проверяется после отправки.

## Риски и откат

- Поддельные campaign key → строгая грамматика и максимум 200 строк отчёта.
- Потеря события при БД-сбое → safe failure без поломки `/start`; повтор ссылки может дозаписать.
- Откат → предыдущая версия приложения; аддитивную таблицу оставить.
