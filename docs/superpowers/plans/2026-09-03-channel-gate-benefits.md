# Plan — подписка на канал для бесплатных выгод

- **Дата:** 2026-09-03
- **Spec:** `docs/superpowers/specs/2026-09-03-channel-gate-benefits-design.md`
- **Ветка:** `feat/channel-gate-benefits`

## Сводка

Распространить уже настроенный Telegram-гейт на получение бонусов и промокодов
в боте и личном кабинете. Выполнение состоит из двух фаз: серверная защита
всех путей и понятные ответы интерфейса.

## Фаза 1 — серверная проверка

### Задача 1.1 — обобщить контракт проверки подписки
- **Файлы:** `backend/src/app/application/trial_channel_gate.py`
- **Что сделать:** описать неопределённый результат `is_member` без привязки к
  fail-open и сохранить обратную совместимость существующего флага.
- **Acceptance:** вызывающий код может отличить только подтверждённое членство
  от отсутствия либо ошибки проверки.

### Задача 1.2 — защитить Telegram-выдачу выгод
- **Файлы:** `backend/src/app/bot_transport/runtime_facade.py`,
  `backend/src/app/bot_transport/storefront_ui.py`
- **Что сделать:** проверять членство перед триалом, клеймом дней, клеймом
  промокода и ручным вводом промокода; создать повторяемую клавиатуру перехода
  в канал.
- **Acceptance:** отказ не выполняет UPDATE claim и не вызывает
  `apply_promo_intent`; повторная проверка продолжает исходный клейм.

### Задача 1.3 — защитить веб-пути
- **Файлы:** `backend/src/app/web_api/app.py`,
  `backend/src/app/web_api/channel_subscription.py`,
  `backend/src/app/web_api/profile.py`, `backend/src/app/web_api/notifications.py`,
  `backend/src/app/web_api/promocode.py`
- **Что сделать:** создать проверяющий клиент для Web API при включённом флаге,
  проверять Telegram ID до клейма уведомления, активации триала и применения промокода.
- **Acceptance:** сервер возвращает 403 `channel_subscription_required` до
  изменения БД; account без проверяемого Telegram ID тоже не проходит гейт.

## Фаза 2 — интерфейс и тесты

### Задача 2.1 — объяснить требование на сайте
- **Файлы:** `frontend/src/features/promocode/*`,
  `frontend/src/features/notifications/*`
- **Что сделать:** вывести понятный текст и ссылку на канал для серверного
  кода `channel_subscription_required`.
- **Acceptance:** пользователь из кабинета может сразу перейти в канал и
  затем повторить действие.

### Задача 2.2 — покрыть критические сценарии
- **Файлы:** `backend/tests/test_trial_channel_gate.py`,
  `backend/tests/test_broadcast_bonus_claim.py`,
  `backend/tests/test_broadcast_promo_claim.py`,
  `backend/tests/test_promo_redemption.py`, `backend/tests/test_web_api_*`
- **Что сделать:** добавить сценарии member/non-member/error и проверить, что
  выдача не была потреблена до прохождения проверки.
- **Acceptance:** новые тесты не зависят от живого Telegram API и не меняют
  существующие сценарии при выключенном флаге.

## Стратегия тестирования

- Юнит-тесты бота: membership checker, ручной промокод и клейм дней/промо с
  фейковым checker.
- Интеграционные Web API: auth + CSRF + fake checker, 403 до изменения claims
  и успешный путь после `member`.
- Полный прогон: `cd backend && .venv/bin/python -m pytest -q`,
  `.venv/bin/python scripts/run_mvp_repo_release_health_check.py`; frontend
  lint/typecheck по доступным npm-скриптам.

## Риски и откат

- Риск: бот не администратор `@bravada_vpn` или Telegram API недоступен, и
  выдача временно недоступна. Митигация: явное сообщение и повторная попытка;
  оператор проверяет роль бота в канале.
- Откат: выключить `TRIAL_CHANNEL_SUBSCRIBE_ENABLE`; миграций нет, уже
  созданные claims и промокоды остаются целыми.

## Чек-лист перед PR

- [ ] `cd backend && .venv/bin/python -m pytest -q` — зелёный
- [ ] `.venv/bin/python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] Линт backend и frontend чист
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [ ] Spec и plan соответствуют фактическому решению
