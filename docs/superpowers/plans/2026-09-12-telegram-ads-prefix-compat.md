# Plan — совместимость рекламных start-меток

- **Дата:** 2026-09-12
- **Spec:** `docs/superpowers/specs/2026-09-12-telegram-ads-prefix-compat-design.md`
- **Ветка:** `fix/telegram-ads-prefix-compat`

## Сводка

Малый совместимый фикс parser → tests → docs без миграции и изменения отчёта.

## Фаза 1 — контракт парсера

### Задача 1.1 — принять оба префикса
- **Файлы:** `backend/src/app/application/telegram_ads_attribution.py`.
- **Acceptance:** `ads_x` и `tgads_x` канонизируются в `x`; чужие и malformed значения
  отклоняются прежним образом.

### Задача 1.2 — закрепить совместимость тестами
- **Файлы:** `backend/tests/test_telegram_ads_attribution.py`.
- **Acceptance:** parser, normalized transport и first-touch покрывают оба префикса.

## Фаза 2 — документация и delivery

### Задача 2.1 — обновить runbook
- **Файлы:** `backend/docs/telegram_ads_attribution.md`.
- **Acceptance:** описаны активный и исторический форматы, единый campaign key и отсутствие
  ретроактивного восстановления.

### Задача 2.2 — проверить и открыть MR
- **Acceptance:** полный pytest, Ruff, health-check и diff-check зелёные; отдельный GitLab MR
  создан без автослияния.

## Риски и откат

- Совпадающий suffix у `ads_` и `tgads_` намеренно агрегируется; это требуемая канонизация.
- Откат приложения вернёт распознавание только `tgads_`; миграций для отката нет.
