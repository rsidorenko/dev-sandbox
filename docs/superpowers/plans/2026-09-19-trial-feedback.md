# Plan — Обратная связь по триалу

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-trial-feedback-design.md`
- **Ветка:** `feat/trial-feedback`

## Фаза 1 — Хранение и безопасная запись

### Задача 1.1 — Добавить feedback schema
- Создать миграцию `092_trial_feedback.sql` с enum constraints, уникальностью и индексом.
- Добавить проверку порядка и повторного применения миграций.

### Задача 1.2 — Добавить fail-open recorder
- Валидировать тип, reason, platform и route до обращения к БД.
- Использовать upsert одной актуальной записи без влияния на основной flow.

## Фаза 2 — Telegram UX

### Задача 2.1 — Собрать причины проблем подключения
- Показывать короткий survey для direct/guided после «Не получилось».
- После ответа или пропуска возвращать соответствующий прежний recovery screen.

### Задача 2.2 — Собрать причины неоплаты
- Добавить survey CTA в follow-up завершённого триала.
- После ответа показать благодарность с CTA тарифа и помощи.

## Фаза 3 — Аналитика

### Задача 3.1 — Расширить admin stats API
- Вернуть totals и reason breakdown для двух типов feedback за выбранный период.
- Применить общий eligible filter.

### Задача 3.2 — Показать feedback в админке
- Добавить компактные таблицы причин и разбивку connection route/platform.
- Корректно отображать пустой результат.

## Проверки

- [x] Unit: parser/validation/keyboard routing.
- [x] PostgreSQL: migration twice, upsert и eligible aggregate.
- [x] Regression: полный backend pytest.
- [x] Frontend: unit/lint/build.
- [x] Release health/readiness и `git diff --check`.
