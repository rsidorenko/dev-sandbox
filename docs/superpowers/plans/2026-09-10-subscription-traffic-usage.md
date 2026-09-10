# Plan — Расход трафика в карточке подписки

- **Дата:** 2026-09-10
- **Spec:** `docs/superpowers/specs/2026-09-10-subscription-traffic-usage-design.md`
- **Ветка:** `feat/subscription-traffic-usage`

## Сводка

Передать накопленный месячный upload/download из существующего хранилища
статистики в стандартный заголовок подписки. Работа состоит из чтения данных,
подключения его к `/sub/` и тестов.

## Фаза 1 — Контракт статистики

### Задача 1.1 — Добавить чтение месячного upload/download
- **Файлы:** `backend/src/app/persistence/postgres_traffic_stats.py`
  (класс `PostgresTrafficStatsRepository`),
  `backend/tests/test_bare_xray_traffic.py`
- **Что сделать:** добавить метод, возвращающий накопленные за текущий MSK-месяц
  upload и download для пользователя; `NULL` привести к нулю.
- **Acceptance:** запрос использует те же email и границы месяца, что и текущий
  месячный total; направления не смешиваются.

## Фаза 2 — Метаданные подписки

### Задача 2.1 — Передать статистику в subscription-userinfo
- **Файлы:** `backend/src/app/web_api/subscription.py`
  (`handle_subscription`, `_profile_meta_headers`),
  `backend/tests/test_sub_profile_meta.py`
- **Что сделать:** читать статистику только при включённом
  `SUB_PROFILE_META_ENABLE` и подставлять её в upload/download заголовка.
- **Acceptance:** `total=0` сохранён; при пустой статистике заголовок содержит
  нули; upload/download равны переданным байтам.

## Стратегия тестирования

- Юнит-тесты `backend/tests/test_bare_xray_traffic.py` проверяют SQL-результат
  нового метода на фейковом пуле.
- Юнит-тесты `backend/tests/test_sub_profile_meta.py` проверяют стандартный
  заголовок с фактическими направлениями трафика.
- Полный набор: `cd backend && python -m pytest -q`.

## Риски и откат

- Риск: старая база без миграции 079 не содержит таблицу статистики. Митигация:
  миграции применяются до запуска версии; статистика включается только вместе с
  существующим сборщиком.
- Откат: удалить коммит фичи; схема БД не меняется.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист (`backend:lint` = `ruff check` + `ruff format --check`)
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [ ] Spec и plan соответствуют реализации
