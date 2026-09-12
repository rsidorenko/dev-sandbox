# Plan — сохранение контекста при повторном подключении

- **Дата:** 2026-09-12
- **Spec:** `docs/superpowers/specs/2026-09-12-connect-retry-context-design.md`
- **Ветка:** `fix/connect-retry-context`

## Сводка

Аддитивно разделяем problem callbacks короткого и подробного сценариев, сохраняем платформу и
приложение в навигации и закрепляем поведение маршрутными тестами.

## Фаза 1 — callback-контракт и UI

### Задача 1.1 — добавить контекст fast-problem
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`.
- **Что сделать:** добавить безопасный callback-префикс и allowlist-парсер платформы; направить
  кнопку «Не получилось» fast path на новый callback.
- **Acceptance:** callback не содержит subscription URL и укладывается в лимит Telegram.

### Задача 1.2 — разделить действия восстановления
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`.
- **Что сделать:** сформировать платформенную клавиатуру проблемы с повтором того же fast path,
  chooser другого приложения для iOS/Android/macOS и подробной Karing-инструкцией для Windows.
- **Acceptance:** пользователь не переключается с Happ на Karing без явного выбора.

## Фаза 2 — маршрутизация и аналитика

### Задача 2.1 — обработать новый callback
- **Файлы:** `backend/src/app/bot_transport/runtime_facade.py`.
- **Что сделать:** распознать allowlist callback, отрендерить новый экран проблемы и сохранить
  старые detailed retry маршруты без изменений.
- **Acceptance:** неизвестная платформа не становится storefront callback; старые сообщения
  продолжают работать.

### Задача 2.2 — сохранить событие проблемы
- **Файлы:** `backend/src/app/application/funnel_events.py`.
- **Что сделать:** сопоставить новый callback событию `connection_problem` и платформе.
- **Acceptance:** аналитика fast path не теряет событие после смены callback.

## Фаза 3 — тесты и доставка

### Задача 3.1 — добавить маршрутные и регрессионные тесты
- **Файлы:** `backend/tests/test_trial_connect_fastpath.py`,
  `backend/tests/test_funnel_foundation.py`.
- **Что сделать:** проверить полный маршрут fast path → проблема → повтор для четырёх платформ,
  переход в chooser/Windows details и сохранение detailed Karing retry.
- **Acceptance:** тесты проверяют текст приложения и callbacks конечного экрана, а не только
  отдельные функции клавиатуры.

### Задача 3.2 — выполнить delivery
- **Файлы:** только файлы этой фичи.
- **Acceptance:** точечные и полные тесты, Ruff, health-check и `git diff --check` зелёные;
  создан GitLab MR без автоматического слияния.

## Стратегия тестирования

- Facade-тесты с `StubVlessProvider` проходят настоящие последовательности callbacks и
  подтверждают повторный запрос того же fast path.
- UI assertions проверяют наличие chooser только там, где поддерживаются Happ и Karing.
- Funnel unit-тест подтверждает `connection_problem` для нового callback.
- Полный backend pytest и release health-check проверяют отсутствие регрессий оплаты/VPN.

## Риски и откат

- Риск: старые Telegram-сообщения содержат `*_NO` callbacks. Они сохраняются и продолжают вести
  в прежний detailed flow.
- Риск: повторный fast path снова читает конфиг VPN. Используется существующая безопасная
  функция `_render_connect_fastpath` с проверкой entitlement.
- Откат: один кодовый коммит; миграций и изменений данных нет.

## Чек-лист перед MR

- [x] `cd backend && python -m pytest -q` — зелёный.
- [x] `python scripts/run_mvp_repo_release_health_check.py` — проходит.
- [x] Ruff (`ruff check` + `ruff format --check`) — зелёный.
- [x] `git diff --check` — зелёный.
- [x] Нет изменений оплаты, миграций, VPN CRUD и секретов.
- [ ] Commit/push/MR выполнены только из `fix/connect-retry-context`.
