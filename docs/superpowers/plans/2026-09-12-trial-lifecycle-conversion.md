# Plan — Конверсионный жизненный цикл триала

- **Дата:** 2026-09-12
- **Spec:** `docs/superpowers/specs/2026-09-12-trial-lifecycle-conversion-design.md`
- **Ветка:** `feat/trial-lifecycle-conversion`

## Сводка

Реализация состоит из четырёх фаз: разделение lifecycle-проверок, улучшение текстов, добавление
веб-CTA и регрессионная проверка всего контура.

## Фаза 1 — Надёжный lifecycle планировщика

### Задача 1.1 — Разделить предупреждения по временным окнам
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** оставить обычное предупреждение в окне `(6 ч; 24 ч]`, добавить срочное `(0 ч; 6 ч]`,
  исключать уже доставленные этапы по `notification_log` за всю историю.
- **Acceptance:** окна не пересекаются; старый `trial_expiring` подавляет повтор обычного предупреждения;
  каждый тип доставляется once-ever.

### Задача 1.2 — Отделить paywall от отзыва ключей
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** существующая проверка только отзывает ключи и меняет состояние; следующая проверка
  выбирает недавно деактивированные триалы без доставленного `trial_expired` и отправляет paywall.
- **Acceptance:** ошибка доставки не возвращает доступ, но оставляет пользователя доступным для повторной
  доставки на следующем тике; paywall не отправляется до `keys_deactivated_at`.

### Задача 1.3 — Добавить follow-up после окончания
- **Файлы:** `backend/src/app/runtime/notification_scheduler.py`
- **Что сделать:** добавить once-ever уведомление в окне 24–48 часов после `keys_deactivated_at`, только
  если `plan_id IS NULL` и состояние остаётся `expired`.
- **Acceptance:** оплативший пользователь исключён; старые завершённые триалы не получают массовую
  рассылку при выкладке.

## Фаза 2 — Конверсионные сообщения

### Задача 2.1 — Обновить тексты и кнопки
- **Файлы:** `backend/src/app/bot_transport/notification_texts.py`
- **Что сделать:** добавить отдельные тексты 24h, 6h и follow-up; сделать paywall конкретнее; сохранить
  совместимый `text_trial_expiring` и callback `CB_BUY_VPN`.
- **Acceptance:** сообщения объясняют сохранение ключей/настроек и автоматическое восстановление после
  оплаты, не содержат цены и не обещают автоматического списания.

## Фаза 3 — CTA веб-канала

### Задача 3.1 — Добавить переход к каталогу тарифов
- **Файлы:** `frontend/src/features/notifications/NotificationsBell.tsx`,
  `frontend/src/features/notifications/NotificationsBell.test.tsx`
- **Что сделать:** для двух trial-kind показать ссылку «Выбрать тариф» на `/#tariffs`.
- **Acceptance:** CTA есть у `trial_expiring` и `trial_expired`, но не появляется у остальных видов
  уведомлений и не конфликтует с бонусным claim.

## Фаза 4 — Проверка и документация

### Задача 4.1 — Покрыть lifecycle регрессиями
- **Файлы:** `backend/tests/test_trial_lifecycle_conversion.py`,
  `backend/tests/test_critical_paths.py`, `backend/tests/test_notification_scheduler_web.py`
- **Что сделать:** проверить SQL-границы и дедуп, типы сообщений, порядок новых стадий и допустимость
  веб-kind.
- **Acceptance:** тесты падают при пересечении окон, возврате старой дневной дедупликации или удалении
  любого из четырёх этапов.

### Задача 4.2 — Описать эксплуатационное поведение
- **Файлы:** `backend/docs/trial_lifecycle_conversion.md`
- **Что сделать:** зафиксировать этапы, notification types, retry/dedup и безопасный откат.
- **Acceptance:** оператор может по документу проверить логи и понять, почему конкретное сообщение
  отправилось или было пропущено.

## Стратегия тестирования

- Юнит-тесты backend: изолированный fake pool проверяет запросы, границы окон и аргументы доставки.
- Существующий critical-path тест проверяет полный порядок scheduler tick.
- Существующий контракт веб-kind гарантирует совместимость с CHECK миграции 077.
- Frontend Vitest проверяет наличие и отсутствие CTA по kind.
- Полная проверка: `cd backend && python -m pytest -q`, release health check и frontend `npm test`.

## Риски и откат

- Риск: избыточные сообщения → непересекающиеся окна и once-ever `NOT EXISTS`.
- Риск: повтор сообщения после выкладки → совместимость со старым `trial_expiring`.
- Риск: массовый follow-up старым пользователям → ограничение по свежему `keys_deactivated_at`.
- Риск: paywall до реального отключения → обязательные `state_label = 'expired'` и
  `keys_deactivated_at IS NOT NULL`.
- Откат: revert единственного feature-коммита; миграций и необратимых изменений данных нет.

## Чек-лист перед PR

- [x] `cd backend && python -m pytest -q` — зелёный
- [x] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [x] ruff-линт чист (`ruff check` + `ruff format --check`)
- [x] frontend Vitest и lint проходят
- [x] Коммиты и MR на русском, без `Co-Authored-By`
- [x] Spec и plan соответствуют реализации
