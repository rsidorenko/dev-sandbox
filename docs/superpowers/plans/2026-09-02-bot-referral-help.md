# Plan — Упрощение рефералки и FAQ в Telegram-боте

- **Дата:** 2026-09-02
- **Spec:** `docs/superpowers/specs/2026-09-02-bot-referral-help-design.md`
- **Ветка:** `codex/feat-bot-referral-help`

## Сводка

Упростить UI реферальной программы и заменить справочный текст интерактивным FAQ из десяти
вопросов фронтенда. Пересмотр UX: FAQ отрисовывается одним сообщением, а веб-реферальная ссылка
копируется нативной кнопкой. Работа не требует миграций и затрагивает только Telegram transport.

## Фаза 1 — Контент и клавиатуры

### Задача 1.1 — Обновить реферальный экран
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py` (`text_referral_program`, новая
  `referral_program_keyboard`)
- **Что сделать:** оставить главный оффер +30 дней, компактную полезную статистику и две URL-кнопки;
  сформировать Telegram share URL с безопасным URL-кодированием.
- **Acceptance:** детальных ставок и сырых ссылок в тексте нет; обе персональные ссылки доступны
  кнопками.

### Задача 1.2 — Добавить FAQ-каталог и текст справки
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`
- **Что сделать:** ввести неизменяемый каталог из десяти пар вопрос/ответ, собрать их в один
  структурированный HTML-текст и оставить только кнопку возврата.
- **Acceptance:** все вопросы и ответы видны без callback-навигации.

## Фаза 2 — Навигация и тесты

### Задача 2.1 — Подключить упрощённую маршрутизацию storefront
- **Файлы:** `backend/src/app/bot_transport/runtime_facade.py`
- **Что сделать:** заменить fallback-клавиатуру рефералки на специализированную, использовать
  `copy_text` для веб-ссылки и убрать маршрутизацию FAQ-callback.
- **Acceptance:** `CB_HELP` открывает полный справочный текст; веб-кнопка копирует ссылку.

### Задача 2.3 — Отправить Help как Telegram Rich Message
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`, `backend/src/app/bot_transport/runtime_facade.py`,
  `backend/src/app/runtime/telegram_httpx_raw_client.py`, `backend/src/app/runtime/polling.py`
- **Что сделать:** представить FAQ как Rich HTML с отдельными `<details>`-блоками, пронести rich payload
  через отрендеренный пакет и вызвать `sendRichMessage` только для экрана Help.
- **Acceptance:** Bot API получает корректный `rich_message`; обычные отправки по-прежнему используют
  `sendMessage`.

### Задача 2.2 — Зафиксировать поведение тестами
- **Файлы:** `backend/tests/test_bot_transport_runtime_facade.py`, при необходимости новый
  `backend/tests/test_storefront_ui.py`
- **Что сделать:** проверить короткий реферальный текст, URL обеих кнопок, весь FAQ-каталог,
  отрисовку ответа и возврат.
- **Acceptance:** тесты не зависят от внешней сети или БД.

## Стратегия тестирования

- Юнит-тесты: каталог FAQ, клавиатуры и корректное кодирование share URL.
- Facade-тесты: переходы рефералки и «Помощи» через callback update.
- Полная проверка: `cd backend && python -m pytest -q`,
  `python scripts/run_mvp_repo_release_health_check.py`, `ruff check src tests` и
  `ruff format --check src tests`.

## Риски и откат

- Риск: FAQ фронтенда меняется отдельно от бота. Митигация: тест фиксирует текущие десять вопросов;
  изменение FAQ должно обновлять оба каталога в одном MR.
- Риск: некорректный URL share. Митигация: тестировать значения `url` и `text` после разборки query.
- Откат: обычный revert одного MR; данных и миграций нет.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт и форматирование чисты
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [ ] Spec и plan обновлены, если реальность расходится с планом
