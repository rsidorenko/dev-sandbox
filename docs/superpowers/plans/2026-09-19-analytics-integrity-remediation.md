# Plan — целостность аналитики воронки

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-analytics-integrity-remediation-design.md`
- **Ветка:** `fix/analytics-integrity`

## Сводка

Исправить временные окна аналитики на backend, отразить новые состояния в admin UI и закрепить контракт тестами.

## Фаза 1 — корректные выборки

### Задача 1.1 — выровнять окно экспериментов
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`_EXPERIMENT_RESULTS`, `_experiment_results`)
- **Что сделать:** выделить зрелые экспозиции и считать outcomes только за первые 10 суток.
- **Acceptance:** свежие экспозиции входят только в `exposures`/`pending_exposures`, а CR использует `mature_exposures`.

### Задача 1.2 — ограничить исторический трафик
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`_DATA_HEALTH_CHAIN_ANOMALIES`, `_TRIAL_COHORTS`)
- **Что сделать:** добавить верхнюю дату отчёта в запросы и параметры вызова.
- **Acceptance:** трафик после `date_to` не меняет исторический ответ.

### Задача 1.3 — отделить неоднозначную атрибуцию маршрута
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`_FUNNEL_DIAGNOSTICS_V2`, `_funnel_diagnostics_v2`)
- **Что сделать:** использовать только строго более раннюю дату маршрута и отдельно считать совпадение дат.
- **Acceptance:** same-day наблюдения не входят в direct/guided meaningful и доступны отдельным счётчиком.

## Фаза 2 — контракт админки

### Задача 2.1 — показать зрелость и неоднозначность
- **Файлы:** `frontend/src/entities/admin/types.ts`, `frontend/src/app/admin/stats/page.tsx`
- **Что сделать:** расширить типы и таблицы новыми полями.
- **Acceptance:** оператор видит общее/зрелое/ожидающее число экспозиций и same-day атрибуцию.

## Фаза 3 — проверка

### Задача 3.1 — обновить тесты статистики
- **Файлы:** `backend/tests/test_experiments.py`, `backend/tests/test_admin_stats.py`
- **Что сделать:** проверить знаменатель зрелых экспозиций, фиксированное окно и новый route-контракт.
- **Acceptance:** целевые и полные backend/frontend проверки зелёные.

## Стратегия тестирования

- Юнит-тест helper результата эксперимента с разными `exposures` и `mature_exposures`.
- PostgreSQL-интеграция админской статистики для свежих и зрелых назначений.
- Frontend lint, typecheck, unit и production build.

## Риски и откат

- Риск: метрики experiments визуально снизятся из-за исключения незрелых результатов → UI явно показывает знаменатель и ожидающие экспозиции.
- Риск: дневная гранулярность даст много ambiguous → это честное отражение качества исходных данных.
- Откат: один кодовый commit; миграций и изменений production-data нет.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист
- [ ] Коммит и MR на русском, без co-author trailers
- [ ] Spec и plan соответствуют реализации
