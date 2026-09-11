# Plan — Отдельные ссылки ручного и автоматического выбора

- **Дата:** 2026-09-11
- **Spec:** `docs/superpowers/specs/2026-09-11-additive-auto-selection-design.md`
- **Ветка:** `fix/additive-auto-selection`

## Сводка

В трёх фазах добавляем метаданные alias-ссылки, переводим routing на `vlessRoute`,
покрываем регрессии и доставляем изменение в production.

## Фаза 1 — Контракт данных и ссылок

### Задача 1.1 — Добавить additive-колонки
- **Файлы:** `backend/migrations/080_vpn_servers_auto_selection_alias.sql`
- **Что сделать:** добавить route и отображаемые поля дополнительной ссылки с
  нейтральными default.
- **Acceptance:** миграция повторяемая и не меняет существующие строки.

### Задача 1.2 — Отдавать ручную и автоматическую ссылки
- **Файлы:** `backend/src/app/issuance/xui_client.py`,
  `backend/src/app/issuance/xui_vless_provider.py`
- **Что сделать:** загрузить alias-метаданные, модифицировать только байты 7–8 UUID,
  добавить второй `VlessServerConfig` без нового lifecycle-клиента.
- **Acceptance:** обе ссылки имеют разные labels/routes, но одну нормализованную
  VLESS-идентичность.

## Фаза 2 — Безопасная конфигурация Xray

### Задача 2.1 — Перевести routing на vlessRoute
- **Файлы:** `backend/scripts/configure_xray_auto_selection.py`
- **Что сделать:** заменить inbound-scoped копии на route-scoped, удалить старые
  правила этого balancer и сохранить глобальные фиксированные правила.
- **Acceptance:** ручная ссылка идёт по исходному outbound, alias — через leastPing;
  повторный merge не меняет результат.

### Задача 2.2 — Сделать publish недеструктивным
- **Файлы:** `backend/scripts/configure_xray_auto_selection.py`,
  `backend/docs/xray_auto_selection_runbook.md`
- **Что сделать:** publish восстанавливает ручные поля и отдельно записывает alias;
  обновить формат закрытого профиля и порядок production rollout/rollback.
- **Acceptance:** существующая строка остаётся ручным сервером, alias включается
  дополнительными колонками.

## Фаза 3 — Регрессии и доставка

### Задача 3.1 — Закрепить поведение тестами
- **Файлы:** `backend/tests/test_configure_xray_auto_selection.py`,
  `backend/tests/test_sub_happ_filter.py`, релевантные provider-тесты.
- **Что сделать:** проверить UUID route, две ссылки, custom template, routing и publish.
- **Acceptance:** старый сценарий «переименовать строку в автовыбор» невозможен.

### Задача 3.2 — Доставить и проверить production
- **Файлы:** без дополнительных tracked-файлов.
- **Что сделать:** полная локальная валидация, MR, зелёный CI, merge, dry-run/apply
  обоих профилей и smoke-тест подписок/выходов.
- **Acceptance:** четыре ожидаемых названия присутствуют, оба Xray active, CI green.

## Стратегия тестирования

- Юнит-тесты provider: route-модификация UUID и alias-ссылка для generated/custom link.
- Юнит-тесты оператора: строгий профиль, route-scoped правила, идемпотентность,
  guarded publish ручных и alias-полей.
- Полный `backend` pytest, Ruff и release health-check.
- Production smoke: безопасно выводить только названия ссылок, route и статусы.

## Риски и откат

- Риск: клиент передаст изменённый UUID иначе ожидаемого → перед rollout проверяется
  реальный VLESS egress; ручная ссылка остаётся независимым fallback.
- Риск: старые inbound-scoped правила останутся выше новых → merge удаляет все правила
  принадлежащего balancer перед построением route-scoped набора.
- Откат: вернуть backup Xray-конфига, обнулить auto-selection колонки; ручные поля
  остаются рабочими, миграция колонок не требует rollback.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] Ruff clean
- [ ] Коммит на русском, без `Co-Authored-By`
- [ ] Миграция additive и идемпотентна
- [ ] Spec и plan соответствуют реализации
