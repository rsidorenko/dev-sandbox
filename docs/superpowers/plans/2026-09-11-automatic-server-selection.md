# Plan — Автовыбор VPN-сервера

- **Дата:** 2026-09-11
- **Spec:** `docs/superpowers/specs/2026-09-11-automatic-server-selection-design.md`
- **Ветка:** `feat/automatic-server-selection`

## Сводка

Реализуем идемпотентный генератор Xray `leastPing`, безопасный операторский apply/publish и
покрываем обычный и LTE-профили одним контрактом без новых серверов или доменов.

## Фаза 1 — Конфигурационный контракт

### Задача 1.1 — Описать и валидировать профиль
- **Файлы:** `backend/scripts/configure_xray_auto_selection.py`
- **Что сделать:** добавить dataclass-профиль, загрузку внешнего JSON и строгую проверку тегов,
  кандидатов, fallback, surface и путей.
- **Acceptance:** неполный, пересекающий чужое пространство тегов или одноузловой профиль
  отклоняется до SSH/DB-операций.

### Задача 1.2 — Реализовать идемпотентный Xray merge
- **Файлы:** `backend/scripts/configure_xray_auto_selection.py`
- **Что сделать:** управляемо добавить outbounds, balancer, observatory и inbound rules.
- **Acceptance:** второй merge равен первому; существующие foreign-rules переведены на
  balancer, `direct` и не принадлежащие профилю элементы сохранены.

## Фаза 2 — Безопасное применение

### Задача 2.1 — Добавить dry-run и apply с откатом
- **Файлы:** `backend/scripts/configure_xray_auto_selection.py`
- **Что сделать:** SSH read, сохранение live-users, временный файл, Xray test, backup, restart,
  verify и restore при ошибке.
- **Acceptance:** dry-run ничего не пишет; любой сбой до verify не оставляет сломанный конфиг.

### Задача 2.2 — Публиковать автоподключение после verify
- **Файлы:** `backend/scripts/configure_xray_auto_selection.py`
- **Что сделать:** по `--publish` обновить только указанные active bare-xray rows в транзакции.
- **Acceptance:** label/surface меняются только после успешного apply и ожидаемого row count.

### Задача 2.3 — Описать rollout обычного и LTE-пулов
- **Файлы:** `backend/docs/xray_auto_selection_runbook.md`
- **Что сделать:** дать JSON-шаблон без секретов, команды dry-run/apply, проверки и rollback.
- **Acceptance:** оператор может подготовить оба профиля из текущих entry/exit без догадок и
  без копирования секретов в репозиторий.

## Фаза 3 — Проверка

### Задача 3.1 — Добавить unit-тесты
- **Файлы:** `backend/tests/test_configure_xray_auto_selection.py`
- **Что сделать:** покрыть parsing/validation, сохранение чужого конфига, идемпотентность,
  fallback, порядок правил, redaction и publish guard.
- **Acceptance:** все новые тесты зелёные.

## Стратегия тестирования

- Юнит-тесты: `backend/tests/test_configure_xray_auto_selection.py`.
- Полный backend regression: `python -m pytest -q`.
- Статика: release health check и ruff check/format check.
- Production: сначала два read-only dry-run; apply по одному профилю с E2E-проверкой egress.

## Риски и откат

- Риск: restart bare Xray потеряет in-memory users → до записи обязательно сохраняем их через
  gRPC; при любой ошибке не продолжаем.
- Риск: недоступны все кандидаты → `fallbackTag` указывает на заранее проверенный outbound.
- Риск: новый маршрут пересечётся с существующим → инструмент владеет только тегами своего
  префикса и правилами своих inbound.
- Откат: восстановить созданный `.pre-auto-selection.bak`, перезапустить Xray и вернуть label
  через сохранённые значения из dry-run.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист (`backend:lint` = `ruff check` + `ruff format --check`)
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [ ] Spec и plan соответствуют реализации
