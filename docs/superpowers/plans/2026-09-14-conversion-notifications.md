# Plan — Конверсионные уведомления

- **Дата:** 2026-09-14
- **Spec:** `docs/superpowers/specs/2026-09-14-conversion-notifications-design.md`
- **Ветка:** `feat/conversion-notifications`

## Сводка

Реализация состоит из четырёх фаз: контракт текстов и CTA, ранние этапы воронки,
сегментация lifecycle/referral и регрессионная проверка безопасности.

## Фаза 1 — Контракт сообщений

### Задача 1.1 — Добавить ранние конверсионные сообщения
- **Файлы:** `backend/src/app/bot_transport/notification_texts.py`
- **Что сделать:** добавить тексты активации триала, подключения и незавершённого checkout;
  использовать одну основную кнопку и существующие callback.
- **Acceptance:** тексты не содержат жёстко заданных цен, CTA ведут в существующие маршруты.

### Задача 1.2 — Обновить lifecycle-тексты
- **Файлы:** `backend/src/app/bot_transport/notification_texts.py`
- **Что сделать:** разделить 24/6-часовые сообщения по наличию трафика, сократить тексты
  expired/subscription/key-deleted и убрать кнопку главного меню.
- **Acceptance:** сообщения объясняют состояние и следующее действие без технического жаргона.

## Фаза 2 — Этапы scheduler

### Задача 2.1 — Добавить activation/connection/checkout проверки
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** добавить SQL-выборки с временными окнами, internal-фильтром и once-ever
  дедупом; для подключения выбрать callback последней платформы.
- **Acceptance:** новые методы вызываются до lifecycle-предупреждений и не меняют БД,
  кроме штатных записей доставки.

### Задача 2.2 — Применить интервал scheduler
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** использовать `_CHECK_INTERVAL_SECONDS`, дефолт 900 секунд.
- **Acceptance:** env override работает; остановка цикла остаётся interruptible.

## Фаза 3 — Сегментация и идемпотентность

### Задача 3.1 — Сегментировать активный триал по трафику
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** вычислять `has_traffic`, форматировать окончание в MSK и выбирать текст/CTA.
- **Acceptance:** один и тот же lifecycle-этап не дублируется, ветви traffic/no-traffic проверяемы.

### Задача 3.2 — Перенести trial referral и убрать повтор paid-offer
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`,
  `backend/src/app/bot_transport/notification_texts.py`
- **Что сделать:** требовать положительный трафик после trial start; paid-offer исключить после
  любого уже показанного referral offer.
- **Acceptance:** trial-offer не отправляется до использования VPN; пользователь не получает два оффера.

### Задача 3.3 — Исправить дедуп paid expiry
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** исключить лог текущего трёхдневного окна конкретной даты окончания.
- **Acceptance:** повторный тик и смена UTC-даты не создают повтор, новое продление снова допустимо.

## Фаза 4 — Проверки

### Задача 4.1 — Добавить unit-тесты
- **Файлы:** `backend/tests/test_conversion_notifications.py`,
  `backend/tests/test_trial_lifecycle_conversion.py`,
  `backend/tests/test_notification_referral_offer.py`
- **Что сделать:** покрыть SQL-контракты, callback, тексты, scheduler order и интервал.
- **Acceptance:** целевые тесты зелёные и ловят отсутствие дедупа/неверный CTA.

### Задача 4.2 — Выполнить preflight
- **Файлы:** весь backend.
- **Что сделать:** pytest, release health check, ruff, `git diff --check`.
- **Acceptance:** проверки зелёные; изменения не затрагивают платежные и VPN CRUD-модули.

## Стратегия тестирования

- Юнит-тесты на фейковом pool проверяют SQL и параметры без реальной отправки Telegram.
- Тесты текстов проверяют отсутствие жёсткой цены и точные callback существующего UI.
- Полный `backend` pytest защищает оплату, выдачу VPN и миграции от регрессий.
- Миграционный прогон не нужен: новых миграций нет.

## Риски и откат

- Риск: лишнее уведомление из-за старого факта трафика → ограничить трафик датой старта триала.
- Риск: слишком частые тики → все этапы once-ever, SQL индексируется по пользователю/времени;
  интервал можно вернуть env-переменной без отката кода.
- Откат: revert feature commit; схема БД не меняется.

## Чек-лист перед PR

- [x] `cd backend && python -m pytest -q` — зелёный
- [x] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [x] ruff-линт чист (`backend:lint` = `ruff check` + `ruff format --check`)
- [x] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [x] Spec и plan соответствуют реализации
