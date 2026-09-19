# Design Spec — Админка: качество данных, когорты и качество VPN

- **Автор / ответственный:** команда Bravada VPN
- **Дата:** 2026-09-19
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-09-19-admin-quality-dashboard.md`

## Контекст и проблема

Диагностика воронки v2, причины отвалов и connection checks уже собираются, но в админке нет
трёх вещей, без которых цифры вводят в заблуждение:

1. **Data health** — срезы смешивают несопоставимые периоды: события подключения собираются
   только с 12.09, расходы по кампаниям не внесены (CAC/ROAS при этом выглядят нулями),
   nakrut-регистрации исключены из eligible, но их объём нигде не виден как аномалия.
2. **Когорты** — воронка v2 даёт агрегат за период, но не показывает, как ведёт себя
   недельная когорта триалов (meaningful → D1/D2/D3 → checkout → оплата).
3. **Качество VPN** — появились `connection_checks` (connected, latency, throughput, сервер),
   но в админке их нет: нельзя увидеть, какой сервер медленный/нестабильный.

## Цели

- Блок «Качество данных»: дата начала сбора и объём по каждому источнику событий; источники
  атрибуции с объёмом (включая nakrut); кампании с атрибуцией, но без внесённых расходов;
  несогласованные цепочки (meaningful traffic без маршрута; трафик раньше setup).
- Недельные когорты триалов: триалы → meaningful → D1/D2/D3 → checkout → оплата ≤10 дней.
- Блок «Качество VPN»: по серверам из `connection_checks` — проверки, доля connected,
  latency p50/p95, медиана скорости; веб-вердикты (работает/проблема) и причины проблем
  с поверхности web.
- Всё — read-only расширение `GET /api/v1/admin/stats` + новая секция в существующей странице
  `/admin/stats`; eligible-фильтрация как в остальных агрегатах.

## Non-goals

- Не делаем synthetic-мониторинг серверов (отдельная инфраструктурная задача).
- Не добавляем графики времени по latency (в первой итерации — таблицы).
- Не трогаем расчёт CAC/ROAS и форму внесения расходов.
- Эксперименты (гипотезы/CI/размер выборки) — отдельной фичей, тут только данные.

## Текущее состояние

- `web_api/admin_stats.py` — агрегатный GET c параллельным `asyncio.gather`; уже есть
  `product_funnel`, `funnel_diagnostics_v2` (D1/D2/D3, direct/guided, meaningful 1 МиБ),
  `trial_feedback`, `experiments`, `telegram_ads`, `traffic_by_server`.
- `analytics_eligible_user_ids` (миграция 090) — исключает internal + nakrut.
- `connection_checks` (миграция 093) — connected, detected_server_label, latency_ms,
  throughput_kbps, platform, created_at.
- `funnel_events` — VIEW (registered/trial_started/…из lifecycle), `funnel_interactions` —
  клики (setup_started/…/payment_created, surface telegram/web, connection_route).
- `telegram_ads_attributions` (campaign_key, включая 'nakrut') и
  `telegram_ads_spend_daily` (campaign_key, spend_date, amount).
- Фронт `/admin/stats` (`frontend/src/app/admin/stats/page.tsx`) — секции карточек;
  типы ответа в `features/admin/api.ts`.

## Предлагаемое решение

Три новых read-only агрегата в ответе `/api/v1/admin/stats`:

### `data_health`

- `sources`: для каждого источника — `{key, events, first_occurred_at, rows}`:
  `funnel_events` (по event_name, из VIEW), `funnel_interactions` (по event_name+surface),
  `trial_feedback`, `connection_checks`, `telegram_ads_attributions`.
- `attribution_sources`: top кампаний по атрибуциям за период (включая `nakrut`),
  с пометкой `is_eligible_excluded`.
- `campaigns_missing_spend`: кампании с атрибуциями в периоде, у которых нет ни одной строки
  расходов ( предупреждение «CAC не посчитаем»).
- `chain_anomalies`: `meaningful_without_route` (meaningful traffic, но нет direct/guided
  события), `traffic_before_setup` (первый трафик раньше любого setup/platform события),
  `trials_without_telemetry` (триалы без единого события взаимодействия).

### `trial_cohorts`

Недельные (ISO, MSK) когорты по `trial_started_at` за период; для каждой:
`cohort_week, trials, meaningful_users, d1_eligible, d1_retained, d2_…, d3_…,
checkout_opened, paid_by_day10` (оплата — payment_succeeded из VIEW funnel_events в окне
10 дней от старта триала; незрелые когорты — с учётом eligible-знаменателей как в v2).

### `vpn_quality`

- `servers`: GROUP BY `detected_server_label` из `connection_checks` за период:
  checks, users, connected_rate, latency p50/p95 (`percentile_cont`), throughput p50.
- `web_verdicts`: из `funnel_interactions` surface='web' за период: confirmed/problem
  (distinct users), из `trial_feedback` route='' — распределение причин.
- `overall`: всего проверок, доля connected, «не удалось определить» (сервер пуст при
  connected=false → distinguish unknown? нет — unknown не пишется как строка; считаем
  только записанные прогоны).

## Альтернативы

- Отдельный эндпоинт `/admin/stats/quality` — дробит фронтенд и авторизацию; все блоки
  лёгкие, оставляем один агрегат.
- Когорты по месяцам — при 97 триалах/мес недельная гранулярность информативнее.
- Latency по перцентилям на клиенте — считаем в SQL (`percentile_cont`), меньше данных
  в сеть.

## Влияние

- **Файлы:** `backend/src/app/web_api/admin_stats.py` (+SQL, +3 функции, +3 ключа ответа),
  `frontend/src/features/admin/api.ts` (типы), `frontend/src/app/admin/stats/page.tsx`
  (секция «Качество и данные»: data health, когорты, качество VPN).
- **Миграции:** нет (только чтение существующих таблиц).
- **Безопасность:** read-only, существующий admin auth; никаких новых PII (IP не хранятся).
- **Биллинг/VPN/lifecycle:** не изменяются.

## Открытые вопросы

- Нет блокирующих; порог «мало данных» для когорт (<5 триалов) — подсветка на фронте.

## Критерии приёмки

- [ ] `/api/v1/admin/stats` отдаёт `data_health`, `trial_cohorts`, `vpn_quality`.
- [ ] Data health показывает nakrut-объём, кампании без расходов, даты старта сбора событий.
- [ ] Когорты сходятся с funnel_diagnostics_v2 на том же периоде (сумма trials = v2.trials).
- [ ] vpn_quality корректно считает p50/p95 latency и connected_rate по серверам.
- [ ] Фронт показывает три новых блока; пустые данные не ломают страницу.
- [ ] Тесты: SQL-контракты на синтетических данных (smoke-база), типы/рендер фронта.
- [ ] pytest / ruff / release-checks / frontend lint+build+tests — зелёные.
