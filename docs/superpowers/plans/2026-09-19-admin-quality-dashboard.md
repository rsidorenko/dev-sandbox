# Plan — Админка: качество данных, когорты и качество VPN

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-admin-quality-dashboard-design.md`
- **Ветка:** `feat/admin-quality-dashboard`

## Сводка

Три read-only агрегата в `/api/v1/admin/stats` (data_health, trial_cohorts, vpn_quality)
и секция «Качество и данные» на `/admin/stats`. 3 фазы: backend SQL → frontend → валидация/MR.

## Фаза 1 — Backend

### Задача 1.1 — SQL-агрегаты
- **Файлы:** `backend/src/app/web_api/admin_stats.py`
- **Что сделать:** три SQL-константы + функции-обёртки:
  - `_DATA_HEALTH_SOURCES`: UNION источников (funnel_events по event_name;
    funnel_interactions по event_name+surface; trial_feedback; connection_checks;
    attributions) → first_occurred_at/rows; отдельные запросы attribution_sources
    (top-20 за период, с nakrut), campaigns_missing_spend (LEFT JOIN spend),
    chain_anomalies (переиспользовать логику v2: meaningful без route; first_traffic
    раньше min(setup/platform); триалы без interactions).
  - `_TRIAL_COHORTS`: недельные когорты (date_trunc('week', trial_started_at AT TIME ZONE
    MSK)), с eligible-знаменателями D1/D2/D3, checkout_opened (funnel_interactions после
    старта триала), paid_by_day10 (payment_succeeded из funnel_events VIEW).
  - `_VPN_QUALITY_SERVERS`: GROUP BY detected_server_label (connected=true) за период:
    checks/users/connected_rate/latency p50/p95/throughput p50; web_verdicts из
    funnel_interactions surface='web' + trial_feedback route='' причины.
- **Acceptance:** контракты на smoke-БД; суммы когорт = v2.trials на том же окне.

### Задача 1.2 — Включение в ответ
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`handle_admin_stats`)
- **Что сделать:** добавить три gather-задачи и три ключа ответа.
- **Acceptance:** `/api/v1/admin/stats?period=30d` содержит новые блоки; авторизация
  и валидация period не изменились.

## Фаза 2 — Frontend

### Задача 2.1 — Типы
- **Файлы:** `frontend/src/features/admin/api.ts`
- **Что сделать:** интерфейсы `DataHealth`, `TrialCohort`, `VpnQuality` (+ подтипы),
  добавить в тип ответа stats.
- **Acceptance:** typecheck зелёный.

### Задача 2.2 — Секция «Качество и данные»
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`
- **Что сделать:** после «Экспериментов» секция из трёх карточек:
  - Качество данных: таблица источников (событие, собирается с, строк); источники
    атрибуции (nakrut подсвечен); кампании без расходов (предупреждение); аномалии цепочек.
  - Когорты триалов: таблица недель (триалы → meaningful → D1/D2/D3 % → checkout → оплата).
  - Качество VPN: таблица серверов (проверки, connected %, p50/p95 latency, скорость);
    веб-вердикты и топ причин проблем.
- **Acceptance:** рендер на пустых данных без ошибок; vitest на парсинг/рендер ключевых
  значений.

## Фаза 3 — Валидация и MR

- pytest (полный), smoke-БД контракт-тесты, ruff, release-checks; frontend lint/build/tests.
- Diff-review: только read-only; биллинг/issuance не затронуты.
- MR (рус.), CI зелёный, squash-merge, удалить ветку.

## Стратегия тестирования

- Backend: расширить `tests/test_admin_stats.py` — синтетические пользователи в smoke-БД
  (DATABASE_URL opt-in): connection_checks строки, атрибуция без расходов, nakrut-кампания,
  когорта из 2 недель → проверка чисел.
- Frontend: тест рендера секции на фикстуре ответа.

## Риски и откат

- Риск: тяжёлые SQL на больших таблицах → все запросы за период с параллельным gather,
  индексы по occurred_at/created_at уже есть (проверить connection_checks created_at — есть).
- Откат: MR revert, миграций нет.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] release-checks проходят, ruff чист
- [ ] frontend lint/build/tests зелёные
- [ ] Коммиты на русском, без Co-Authored-By
- [ ] Spec/plan соответствуют реализации
