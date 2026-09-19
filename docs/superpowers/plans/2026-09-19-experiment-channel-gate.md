# Plan — Эксперимент: жёсткий vs мягкий гейт подписки на канал

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-experiment-channel-gate-design.md`
- **Ветка:** `feat/experiment-channel-gate`

## Сводка

A/B `trial_channel_gate_v1` (control = жёсткий гейт, soft = триал без подписки +
предложение после) на боте и web, с kill-switch и расширением админ-агрегата
экспериментов meaningful-метрикой. 3 фазы: ядро/бот → web/админка → валидация/MR.

## Фаза 1 — Ядро и бот

### Задача 1.1 — Реестр и модуль эксперимента
- **Файлы:** `backend/src/app/domain/experiments.py`,
  `backend/src/app/application/trial_gate_experiment.py` (новый)
- **Что сделать:** `TRIAL_CHANNEL_GATE_EXPERIMENT = "trial_channel_gate_v1"`,
  variants `("control", "soft")`; `experiment_channel_gate_enabled()` (env
  `EXPERIMENT_CHANNEL_GATE_ENABLE`, default off); `resolve_trial_gate_variant(pool,
  internal_user_id)` — bucket → идемпотентный INSERT → вариант; fail-open `"control"`.
- **Acceptance:** unit: включён → валидный вариант + INSERT; выключен/нет pool/ошибка
  БД → "control" без записи.

### Задача 1.2 — Бот: вариант в trial-активации
- **Файлы:** `backend/src/app/bot_transport/runtime_facade.py` (`_handle_trial_activation`)
- **Что сделать:** до `_channel_subscription_gate_response` — резолв варианта;
  soft → пропуск гейта; после успеха soft → клавиатура с предложением подписки.
- **Acceptance:** soft не вызывает `gate.is_member`; control — вызывает; claim/rollback
  логика не изменена.

### Задача 1.3 — Клавиатура с предложением
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`
  (`trial_connect_fastpath_keyboard`)
- **Что сделать:** параметр `channel_suggest: bool = False` → дополнительный ряд
  URL-кнопки «📣 Подписаться на канал» (channel_username_from_env).
- **Acceptance:** тест: без флага рядов столько же; с флагом — ряд с URL канала.

## Фаза 2 — Web и админка

### Задача 2.1 — Web: пропуск гейта для soft
- **Файлы:** `backend/src/app/web_api/profile.py` (`handle_activate_trial`)
- **Что сделать:** до `require_channel_subscription` — вариант по internal_user_id
  из claims; soft → пропуск.
- **Acceptance:** интеграционно (мок checker): soft активирует без 403; control — 403.

### Задача 2.2 — Админ-агрегат: meaningful по вариантам
- **Файлы:** `backend/src/app/web_api/admin_stats.py` (`_EXPERIMENT_RESULTS`)
- **Что сделать:** LEFT JOIN meaningful (bare_xray running sum ≥1МиБ, stat_date ≥
  даты назначения) → `meaningful_traffic_users`, `meaningful_rate`; фронт — колонка
  в таблице экспериментов.
- **Acceptance:** интеграционный тест: назначение + трафик ≥1МиБ → meaningful=1.

## Фаза 3 — Валидация и MR

- compose: проброс `EXPERIMENT_CHANNEL_GATE_ENABLE`; полный pytest, ruff,
  release-checks, frontend lint/build/tests; diff-review (гейт для промо/бонусов не
  тронут); MR (рус.), CI, squash-merge, удалить ветку.

## Стратегия тестирования

- Unit: резолвер вариантов; клавиатура.
- Интеграция (DATABASE_URL opt-in): assignment идемпотентен; `_EXPERIMENT_RESULTS`
  meaningful-колонка на синтетике; web-активация с fake checker (403/200 по варианту).
- Бот-флоу: существующие тесты trial-гейта + новые для soft-пропуска.

## Риски и откат

- Риск: загрязнение выборки web-активациями → гейт пропускается на обеих
  поверхностях одним резолвером.
- Риск: падение подписок канала у soft — принято (guardrail недоступен для замера,
  отслеживаем качественно).
- Откат: `EXPERIMENT_CHANNEL_GATE_ENABLE=0` (без деплоя); назначенные ранее soft-юзеры
  уже могли получить триал без подписки — эффект необратим, но ограничен периодом.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] release-checks / ruff / frontend — зелёные
- [ ] Коммиты на русском, без Co-Authored-By
- [ ] Spec/plan соответствуют реализации
