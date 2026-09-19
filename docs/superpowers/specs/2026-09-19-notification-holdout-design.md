# Design Spec — Holdout уведомлений: измерение реального эффекта

- **Автор / ответственный:** команда Bravada VPN
- **Дата:** 2026-09-19
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-09-19-notification-holdout.md`

## Контекст и проблема

Планировщик рассылает nudges (подключись / оплати / вернись), но их реальный эффект
неизвестен: переход после сообщения нельзя отличить от естественного поведения. Без
контрольной группы любое «улучшение» текста недоказуемо. Практика экспериментов:
10–15% подходящих пользователей не получают конкретное уведомление (holdout), и
сравнивается последующее поведение.

## Цели

- Детерминированный per-(user, type) holdout: ~10% подходящих получают вместо
  уведомления запись `notification_log.delivery_status='holdout'` (дедуп работает,
  ретраев нет, выборка наблюдаема в SQL).
- Holdout только для nudge/маркетинговых типов; операционные (keys_deleted) и
  информационные (trial_expired, subscription_expired) никогда не удерживаются.
- Kill-switch и настройка без деплоя: `NOTIFICATION_HOLDOUT_ENABLE` (default off),
  `NOTIFICATION_HOLDOUT_PERCENT` (default 10).
- Измерение: блок в `/api/v1/admin/stats` — по типам за период: доставлено vs holdout,
  оплата в течение 7 дней после даты уведомления, конверсия.
- Экран «Эффект уведомлений» в админке (в секции «Качество и данные»).

## Non-goals

- Не удерживаем операционные уведомления об удалении ключей и базовые информационные.
- Не делаем holdout веб-канала отдельно от Telegram (хватит суммарного эффекта).
- Не меняем тексты/расписание/условия отправки.

## Текущее состояние

- `runtime/notification_scheduler.py::_send_notification` — двухканальная доставка,
  дедуп `notification_log (internal_user_id, notification_type, sent_date)` уникален;
  `delivery_status ∈ {delivered, terminal_failure}` (миграция 085, CHECK).
- Типы: trial_activation_nudge_15m, trial_connection_nudge_15m, checkout_abandoned_30m,
  trial_expiring_24h, trial_expiring_6h, trial_expired, trial_expired_followup_24h,
  subscription_expiring_3d, subscription_expired, keys_deleted, referral_offer_*.
- Реестр экспериментов и хешируемый bucket (domain/experiments) — паттерн
  детерминированного разбиения.

## Предлагаемое решение

- `application/notification_holdout.py`:
  - `_HOLDOUT_ELIGIBLE_TYPES` — whitelist nudge-типов (см. выше, без операционных);
  - `notification_holdout_enabled()`, `holdout_percent()` (1..50, default 10);
  - `is_notification_holdout(internal_user_id, notification_type) -> bool` —
    sha256(user:type) bucket < percent; не-eligible типы → всегда False.
- `_send_notification`: до отправки каналов — если holdout: INSERT notification_log
  (delivery_status='holdout') ON CONFLICT DO NOTHING → return False. Сбой вставки —
  обычная отправка (fail-open к текущему поведению).
- Миграция 095: расширить CHECK delivery_status до ('delivered','terminal_failure','holdout').
- Админ-блок `notification_holdout`: GROUP BY notification_type, delivery_status ∈
  {delivered, holdout} за период; оплата ≤7 дней после sent_date (billing accepted,
  исключая гранты); на выходе users + paid_users + rate по каждой группе.
- Frontend: карточка «Эффект уведомлений (holdout)» в QualitySection.

## Альтернативы

- Таблица holdout-назначений — состояние ради детерминизма, который даёт хеш; отклонено.
- Holdout на уровне тиков планировщика (не отправлять строку) — ненаблюдаемо в SQL;
  отклонено (статус в notification_log даёт и дедуп, и анализ).

## Влияние

- **Файлы:** application/notification_holdout.py (новый), runtime/notification_scheduler.py,
  миграция 095, web_api/admin_stats.py (+блок), frontend QualitySection (+карточка), compose.
- **Миграции:** CHECK-расширение (drop+re-add, данные совместимы).
- **Безопасность:** fail-open к отправке; выключенный флаг = текущее поведение.
- **Биллинг/VPN/lifecycle:** не меняются.

## Открытые вопросы

- Нет блокирующих. 10% при малых объёмах даст медленную статистику — сознательный
  компромисс между скоростью обучения и недополученной ценностью.

## Критерии приёмки

- [x] Флаг выкл — поведение планировщика бит-в-бит текущее.
- [x] Флаг вкл — eligible-тип в holdout-бакете получает одну запись 'holdout', ничего
      не отправляется, повторные тики пропускают; операционные типы не удерживаются.
- [x] Процент и типы детерминированы (одинаковый результат на повторный вызов).
- [x] Админ-блок показывает delivered vs holdout с оплатой ≤7д и конверсией.
- [x] Тесты: holdout-резолвер (вкл/выкл/типы/процент/детерминизм), планировщик
      (holdout → нет отправки, есть запись; сбой вставки → отправка), миграция, агрегат.
- [x] pytest / ruff / release-checks / frontend — зелёные.
