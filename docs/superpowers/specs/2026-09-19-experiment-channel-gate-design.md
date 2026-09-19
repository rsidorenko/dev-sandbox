# Design Spec — Эксперимент: жёсткий vs мягкий гейт подписки на канал

- **Автор / ответственный:** команда Bravada VPN
- **Дата:** 2026-09-19
- **Статус:** draft
- **Связанный plan:** `docs/superpowers/plans/2026-09-19-experiment-channel-gate.md`

## Контекст и проблема

Сейчас выдача триала требует подписки на Telegram-канал (жёсткий гейт,
`TRIAL_CHANNEL_SUBSCRIBE_ENABLE`). Гипотеза: барьер до первой ценности снижает
активацию — часть пользователей уходит, не подключившись. Альтернатива (паттерн
Proton/Mullvad — ценность до барьера): триал сразу, предложение подписаться после.
Это самый высококонтрастный диагностический эксперимент из рекомендованных: он
проверяет, сколько подключений мы теряем на гейте.

## Цели

- A/B `trial_channel_gate_v1`: `control` (жёсткий гейт, текущее поведение) vs
  `soft` (гейт пропускается, после активации — необязательная кнопка подписки).
- Стабильное серверное назначение вариантов (существующий механизм
  `experiment_assignments`, детерминированный bucket 50/50).
- Эксперимент действует на обеих поверхностях: бот и web `/trial` (иначе
  soft-пользователь, активирующий с сайта, получит смешанный опыт и загрязнит выборку).
- Измеримость: блок «Эксперименты» админки дополнительно считает
  meaningful-трафик по варианту (основная метрика), оплаты уже считаются.
- Kill-switch без деплоя: `EXPERIMENT_CHANNEL_GATE_ENABLE` (default off) — при выкле
  поведение в точности текущее.

## Non-goals

- Не меняем сам гейт для промокодов/бонусов (`is_trial=False` ветки) — только триал.
- Не трогаем длительность триала и цены (отдельный эксперимент).
- Не измеряем подписки на канал как guardrail: Telegram не даёт событий
  подписки/отписки, а текущее членство не является метрикой во времени.
- Не делаем предложение подписки «после первого подключения» (v1 — на экране успеха
  активации: ноль инвазивности в connect-флоу; фиксируем как отклонение от исходной
  идеи).

## Текущее состояние

- Гейт: `application/trial_channel_gate.py` (checker через getChatMember),
  бот-политика `_channel_subscription_gate_response` (`runtime_facade.py:1146`),
  вызов в `_handle_trial_activation` (`runtime_facade.py:1229`) до атомарного claim;
  web — `require_channel_subscription` (`web_api/channel_subscription.py:32`).
- Эксперименты: `domain/experiments.py` — реестр + детерминированный bucket;
  `experiment_assignments` (миграция 089) — назначение при первой экспозиции;
  админ-агрегат `_EXPERIMENT_RESULTS` (`web_api/admin_stats.py`): exposures /
  checkout / paid / revenue. Прецедент фиче-флага эксперимента —
  `checkout_reassurance_v1` на checkout-экране.
- Обе поверхности получают pool: бот — `_get_pool_from_composition`, web —
  `request.app.state.pool`.

## Предлагаемое решение

### Модуль `application/trial_gate_experiment.py`

- `experiment_channel_gate_enabled() -> bool` — env `EXPERIMENT_CHANNEL_GATE_ENABLE`.
- `TRIAL_CHANNEL_GATE_EXPERIMENT = "trial_channel_gate_v1"`; регистрация в
  `domain/experiments.py` с вариантами `("control", "soft")`.
- `async def resolve_trial_gate_variant(pool, internal_user_id) -> str`:
  выключен/нет pool/ошибка → `"control"`; иначе bucket → INSERT … ON CONFLICT DO
  NOTHING (экспозиция) → вернуть вариант. Fail-open, без влияния на бизнес-флоу.

### Бот (`_handle_trial_activation`)

До вызова гейта: `variant = await resolve_trial_gate_variant(pool, internal_user_id)`.
`variant == "soft"` → гейт пропускается (вместе с retry-веткой), сразу атомарный claim.
После успешной активации при soft → `trial_connect_fastpath_keyboard()` с
дополнительной URL-кнопкой «📣 Подписаться на канал» (не блокирует).

### Web (`handle_activate_trial`)

До `require_channel_subscription`: тот же `resolve_trial_gate_variant` по
`internal_user_id` из claims; soft → пропуск гейта. Экран успеха web не меняем.

### Админ-агрегат

`_EXPERIMENT_RESULTS` дополняется LEFT JOIN на meaningful-трафик
(bare_xray running sum ≥ 1 МиБ с даты назначения до конца окна): колонка
`meaningful_traffic_users` и `meaningful_rate` по варианту — основная метрика
эксперимента.

## Альтернативы

- Назначение при регистрации (большая выборка) — разводняет платёжные метрики
  блока экспериментов и меняет semantics существующего агрегата; отклонено.
- Soft-вариант только в боте — смешанный опыт web-активаций загрязняет выборку;
  отклонено.
- Убрать гейт целиком без эксперимента — теряем возможность понять эффект;
  отклонено.

## Влияние

- **Файлы:** `domain/experiments.py` (+1 эксперимент), `application/trial_gate_experiment.py`
  (новый), `bot_transport/runtime_facade.py` (вариант в trial-активации),
  `bot_transport/storefront_ui.py` (кнопка-предложение), `web_api/profile.py`
  (пропуск гейта), `web_api/admin_stats.py` (+meaningful в эксперименты),
  compose (env-проброс), фронт: отображение meaningful в таблице экспериментов.
- **Миграции:** нет.
- **Безопасность:** эксперимент не отключает гейт для control; выключенный
  флаг = текущее поведение; назначение идемпотентно.
- **Биллинг/VPN/lifecycle:** не изменяются (гейт — до claim, откат claim не затронут).

## Открытые вопросы

- Нет блокирующих. Порог статистической значимости при ~97 триалах/мес —
  результат ждём месяцами; осознанно (крупные изменения, micro-метрика ближайшая).

## Критерии приёмки

- [ ] При выключенном `EXPERIMENT_CHANNEL_GATE_ENABLE` поведение бота и web
      идентично текущему (гейт жёсткий).
- [ ] При включённом: soft-вариант получает триал без проверки подписки (бот и web),
      control — как сейчас; назначение стабильно и пишется в experiment_assignments.
- [ ] Soft-экран успеха бота содержит необязательную кнопку подписки.
- [ ] Админ-блок экспериментов показывает meaningful_traffic_users по вариантам.
- [ ] Тесты: вариант-резолвер (вкл/выкл/ошибка БД), бот-флоу (soft пропускает гейт,
      control — нет), web-флоу, meaningful-колонка агрегата, клавиатура.
- [ ] pytest / ruff / release-checks / frontend — зелёные.
