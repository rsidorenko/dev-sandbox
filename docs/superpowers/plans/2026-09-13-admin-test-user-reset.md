# Plan — безопасный сброс тестового пользователя

- **Дата:** 2026-09-13
- **Spec:** `docs/superpowers/specs/2026-09-13-admin-test-user-reset-design.md`
- **Ветка:** `feat/admin-test-user-reset`

## Фаза 1 — аудит и application service

### Задача 1.1 — добавить append-only audit
- Создать идемпотентную миграцию `084_admin_user_reset_actions.sql`.
- Обновить migration-ledger test.

### Задача 1.2 — реализовать сброс
- Создать application service с eligibility-проверкой `is_internal`, отзывом VLESS и
  транзакционной очисткой всех пользовательских таблиц.
- Сохранить audit после очистки; вернуть агрегированные counts и correlation ID.
- Не изменять payment/VPN CRUD — переиспользовать существующий provider port.

## Фаза 2 — admin API

### Задача 2.1 — endpoint сброса
- Добавить auth+CSRF endpoint с точным подтверждением Telegram ID и обязательной причиной.
- Стабильно отобразить not-found, non-internal, provider failure и conflict.

### Задача 2.2 — контракт списка
- Добавить `is_internal` в frontend type (backend уже возвращает поле).
- Добавить API client methods для маркировки и сброса.

## Фаза 3 — интерфейс

### Задача 3.1 — маркировка тестового аккаунта
- Показать тип аккаунта и модальное окно смены `is_internal` с причиной.

### Задача 3.2 — destructive confirmation
- Показывать кнопку сброса только у `is_internal`.
- Требовать точный Telegram ID и причину, объяснить последствия и обновить список после успеха.

## Фаза 4 — тесты и доставка

### Задача 4.1 — backend PostgreSQL integration
- Покрыть auth/CSRF, подтверждение, internal gate, provider failure и полный успешный cleanup.
- Доказать, что повторно созданная identity снова имеет доступный триал.

### Задача 4.2 — frontend tests
- Покрыть блокировку кнопки до точного ID и вызов API с ожидаемым payload.

### Задача 4.3 — delivery
- Полный pytest, isolated PostgreSQL, Ruff, release checks, frontend test/build.
- Commit/push/MR только из `feat/admin-test-user-reset`; CI должен быть зелёным.

## Чек-лист

- [x] Миграция additive/idempotent.
- [x] Обычный пользователь не может быть сброшен.
- [x] Ошибка VPN не меняет PostgreSQL.
- [x] Успешный сброс очищает state и сохраняет audit.
- [x] Повторная регистрация снова допускает триал.
- [ ] Backend/frontend/local/CI проверки зелёные.
