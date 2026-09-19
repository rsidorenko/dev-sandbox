# Design Spec — Эксперимент: длительность триала 3 vs 7 дней

- **Автор / ответственный:** команда Bravada VPN
- **Дата:** 2026-09-19
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-09-19-experiment-trial-duration.md`

## Контекст и проблема

Триал сейчас 3 дня. Данные RevenueCat (мобильные подписки 2026) показывают, что триалы
до 4 дней конвертируются хуже, 5–9 дней — хороший компромисс; переносить цифры на
Telegram VPN напрямую нельзя — только экспериментом. Предпосылка «сначала D1/D2/D3-метрики»
выполнена (funnel-diagnostics-v2 смержена).

## Цели

- A/B `trial_duration_v1`: `control` = 3 дня, `7d` = 7 дней; цены и тарифы не меняются.
- Длительность применяется единообразно во всех трёх точках:
  1. активация в боте (`runtime_facade._handle_trial_activation`);
  2. активация на web (`profile.handle_activate_trial`);
  3. перенос начала триала на первый трафик (`postgres_traffic_stats` scheduler).
- Экспозиция — активация триала; назначение стабильное, идемпотентное.
- Kill-switch `EXPERIMENT_TRIAL_DURATION_ENABLE` (default off) — при выкле бит-в-бит
  текущее поведение.
- Тексты, сообщающие пользователю его длительность, показывают фактические дни
  (бот-экран успеха; web-экран успеха из ответа API).

## Non-goals

- Не меняем маркетинговые тексты «попробовать 3 дня бесплатно» (до активации) —
  для 7d-варианта это undersell, не ложь; зафиксировано как допущение эксперимента.
- Не трогаем тарифы/цены/уведомления (они считаются от дат, не от константы).
- Не тестируем иные длительности (14 дней и т.п.).

## Текущее состояние

- `domain/trial.py`: `TRIAL_DURATION_DAYS = 3`, `trial_expires_at(started_at)`.
- Бот: `runtime_facade.py:1291` `expires = trial_expires_at(now)`.
- Web: `profile.py:435` — так же.
- Первый трафик: `postgres_traffic_stats.py:89-111` — bulk UPDATE
  `trial_expires_at = NOW() + make_interval(days => $2)` c `TRIAL_DURATION_DAYS`.
- Эксперименты: реестр + `experiment_assignments` (паттерн из trial_channel_gate_v1).
- Web `/trial` success-текст берёт `siteConfig.trialDays` (статичные 3) — для 7d
  неточен; ответ активации несёт `trial.started_at/expires_at` — можно посчитать.

## Предлагаемое решение

- `domain/experiments.py`: `TRIAL_DURATION_EXPERIMENT = "trial_duration_v1"`,
  variants `("control", "7d")`.
- `application/trial_duration_experiment.py`: `TRIAL_DURATION_VARIANTS_DAYS =
  {"control": 3, "7d": 7}`; `experiment_trial_duration_enabled()` (env, default off);
  `async resolve_trial_duration_days(pool, internal_user_id) -> int` — bucket →
  идемпотентный INSERT → дни; fail-open 3.
- `domain/trial.py`: `trial_expires_at(started_at, days=TRIAL_DURATION_DAYS)`.
- Бот: days из резолвера → `trial_expires_at(now, days=days)`; успех-текст
  `text_trial_connect_fastpath(days=...)` с русским плюрализмом.
- Web: days из резолвера → аналогично; фронт success-экран считает дни из
  `trial.expires_at - started_at` вместо статичных siteConfig.trialDays.
- Traffic scheduler: scalar-подзапрос по `experiment_assignments`
  (`CASE variant '7d' → 7 ELSE 3 END`, отсутствующее назначение → 3) в
  `trial_expires_at`/`active_until_utc`; когда эксперимент выключен, назначений нет →
  всегда 3 (текущее поведение).

## Альтернативы

- Менять только активацию, не первый-трафик shift — ломает 7d (shift сбросил бы
  окно к 3 дням); отклонено.
- Хранить дни в subscription_snapshots — новая колонка ради эксперимента;
  отклонено (variant уже в experiment_assignments).

## Влияние

- **Файлы:** domain (реестр, trial), application (новый модуль), facade, profile,
  postgres_traffic_stats (SQL), storefront_ui (текст успеха), compose, web /trial.
- **Миграции:** нет.
- **Безопасность:** fail-open в 3 дня; выключенный флаг = текущее поведение;
  вариативное окно корректно взаимодействует с entitlement (все проверки от дат).
- **Биллинг/VPN/lifecycle:** не меняются; reconcile/deactivate работают от дат.

## Открытые вопросы

- Нет блокирующих. При ~97 триалах/мес значимый результат — месяцы; осознанно.

## Критерии приёмки

- [x] Выключенный флаг: все три точки дают 3 дня (существующие тесты без изменений).
- [x] Включённый: 7d-вариант получает 7 дней в боте, web и при shift первого
      трафика; control — 3.
- [x] Назначение стабильное и пишется один раз.
- [x] Бот/web экраны успеха показывают фактические дни.
- [x] Тесты: резолвер, все три точки, тексты, синхронность SQL-CASE с реестром.
- [x] pytest / ruff / release-checks / frontend — зелёные.
