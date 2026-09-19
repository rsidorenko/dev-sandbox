# Plan — Эксперимент: длительность триала 3 vs 7 дней

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-experiment-trial-duration-design.md`
- **Ветка:** `feat/experiment-trial-duration`

## Сводка

A/B `trial_duration_v1` (control=3д, 7d=7д) в трёх точках применения длительности
(бот, web, shift первого трафика) с kill-switch и честными текстами дней.
3 фазы: ядро → применение в трёх точках + тексты → валидация/MR.

## Фаза 1 — Ядро

### Задача 1.1 — Реестр и модуль
- **Файлы:** `backend/src/app/domain/experiments.py`,
  `backend/src/app/application/trial_duration_experiment.py` (новый),
  `backend/src/app/domain/trial.py`
- **Что сделать:** эксперимент `trial_duration_v1` (`control`, `7d`);
  `TRIAL_DURATION_VARIANTS_DAYS`; `experiment_trial_duration_enabled()`;
  `resolve_trial_duration_days()` (fail-open 3); `trial_expires_at(started_at, days=3)`.
- **Acceptance:** unit: резолвер возвращает 3/7, идемпотентный INSERT, fail-open;
  `trial_expires_at` с days=7 даёт +7 дней.

## Фаза 2 — Применение

### Задача 2.1 — Бот и web активация
- **Файлы:** `backend/src/app/bot_transport/runtime_facade.py`,
  `backend/src/app/web_api/profile.py`
- **Что сделать:** days = await resolve_trial_duration_days(...); expires =
  trial_expires_at(now, days=days).
- **Acceptance:** monkeypatch-тесты: 7d → expires +7д в обеих поверхностях.

### Задача 2.2 — Shift первого трафика
- **Файлы:** `backend/src/app/persistence/postgres_traffic_stats.py`
- **Что сделать:** scalar-подзапрос experiment_assignments в SET
  trial_expires_at/active_until_utc (CASE '7d'→7 ELSE 3, отсутствие → 3); комментарий
  о синхронности с реестром; тест-контракт синхронности констант.
- **Acceptance:** интеграционно: юзер с variant '7d' при первом трафике получает
  окно 7 дней; без назначения — 3.

### Задача 2.3 — Тексты дней
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`
  (`text_trial_connect_fastpath(days)`), `frontend/src/app/(site)/trial/page.tsx`
- **Что сделать:** бот-текст успеха параметризован днями (плюрализм); web success
  считает дни из `trial.started_at/expires_at` ответа.
- **Acceptance:** тесты текста (3/7); web-рендер с 7-дневным ответом.

## Фаза 3 — Валидация и MR

- compose: `EXPERIMENT_TRIAL_DURATION_ENABLE`; полный pytest, ruff, release-checks,
  frontend; diff-review (уведомления/биллинг не тронуты); MR, CI, merge.

## Стратегия тестирования

- Unit: резолвер, domain, тексты, SQL-строка содержит CASE-подзапрос.
- Интеграция (DATABASE_URL): shift первого трафика с назначением '7d'.

## Риски и откат

- Риск: расхождение SQL-CASE с реестром → тест-контракт синхронности.
- Риск: маркетинг-тексты «3 дня» при 7d — допущение эксперимента (undersell).
- Откат: `EXPERIMENT_TRIAL_DURATION_ENABLE=0`; уже выданные 7-дневные окна
  остаются (lifecycle от дат, корректно гасится reconcile).

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] release-checks / ruff / frontend — зелёные
- [ ] Коммиты на русском, без Co-Authored-By
- [ ] Spec/plan соответствуют реализации
