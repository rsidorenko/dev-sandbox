# Plan — Управляемый запуск теста гейта канала

- **Дата:** 2026-09-20
- **Spec:** `docs/superpowers/specs/2026-09-20-experiment-channel-gate-launch-design.md`
- **Ветка:** `feat/experiment-channel-gate-launch`

## Сводка

В двух фазах добавляем kill-switch checkout-эксперимента, проверяем fallback и после
доставки переключаем production на единственный активный тест гейта канала.

## Фаза 1 — Безопасное управление экспериментами

### Задача 1.1 — Добавить checkout kill-switch
- **Файлы:** `backend/src/app/web_api/experiments.py`, `docker-compose.prod.yml`, `.env.example`
- **Что сделать:** запретить новые назначения checkout при выключенном флаге и документировать переменную.
- **Acceptance:** endpoint возвращает `experiment_disabled` до обращения к БД; default production — off.

### Задача 1.2 — Зафиксировать fallback тестами
- **Файлы:** `backend/tests/test_experiments.py`, `backend/tests/test_production_compose_trial_channel_gate.py`, frontend checkout tests
- **Что сделать:** проверить disabled/enabled режимы и сохранение control UI при отказе API.
- **Acceptance:** выключенный тест не пишет назначение, включённый сохраняет прежний контракт.

## Фаза 2 — Доставка и запуск

### Задача 2.1 — Проверить и доставить MR
- **Файлы:** без новых файлов
- **Что сделать:** прогнать backend/frontend проверки, открыть MR, дождаться зелёного CI и слить.
- **Acceptance:** main задеплоен успешно.

### Задача 2.2 — Включить channel-gate в production
- **Файлы:** production `.env` (не коммитится)
- **Что сделать:** установить checkout=0, channel-gate=1, пересоздать backend и проверить runtime.
- **Acceptance:** контейнер сообщает checkout=0 и channel-gate=1; healthcheck проходит.

## Стратегия тестирования

- Backend unit: endpoint не пишет назначение при disabled и сохраняет его при enabled.
- Frontend unit: неуспешный resolve оставляет control-разметку.
- Compose contract: оба экспериментальных флага opt-in.
- Полные backend/frontend тесты и release health перед push.

## Риски и откат

- Риск: soft-вариант уменьшит подписки на канал → откат `EXPERIMENT_CHANNEL_GATE_ENABLE=0`.
- Риск: ошибочное включение checkout → `EXPERIMENT_CHECKOUT_REASSURANCE_ENABLE=0`.
- Миграций нет; исторические назначения остаются неизменными.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист
- [ ] frontend lint, typecheck, tests и build зелёные
- [ ] Коммит без `Co-Authored-By`
- [ ] Spec и plan соответствуют реализации
