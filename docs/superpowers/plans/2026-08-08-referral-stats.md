# Plan — Динамика рефералки: приглашено L1/L2, всего заработано

- **Дата:** 2026-08-08
- **Spec:** [`docs/superpowers/specs/2026-08-08-referral-stats-design.md`](../specs/2026-08-08-referral-stats-design.md)
- **Ветка:** `feat/bot-referral-stats`

> Ретроспективный план (фича реализована) — эталон SDD-паттерна. Символы валидны на ветке
> `feat/bot-referral-stats`; на `main` появятся после мёрджа MR.

## Сводка

Добавить агрегирующую статистику рефералки (приглашено L1/L2, всего заработано) и отрисовать её
в боте. Три фазы: репозитории → application-слой → UI + тесты. Без миграций.

## Фаза 1 — Контракты и репозитории (агрегации)

### Задача 1.1 — `count_by_level(level)` для связей
- **Файлы:** `backend/src/app/persistence/referral_contracts.py`,
  `backend/src/app/persistence/postgres_referral.py`, `backend/src/app/persistence/in_memory.py`
- **Что сделать:** добавить метод подсчёта рефералов по уровню (`1` или `2`) для конкретного
  пользователя. Контракт → PostgreSQL-реализация (`COUNT(*) ... WHERE level = $1`) → in-memory.
- **Acceptance:** возвращает `0` без данных; корректно различает уровни; идемпотентен.

### Задача 1.2 — `sum_credits(user)` для реферальных начислений
- **Файлы:** контракт + postgres + in-memory (тот же набор файлов)
- **Что сделать:** сумма реферальных начислений пользователя **в копейках**.
- **Acceptance:** возвращает `0` без начислений; сумма сходится с балансом.

## Фаза 2 — Application-слой

### Задача 2.1 — `ReferralInfo` и `get_referral_info`
- **Файлы:** `backend/src/app/application/referral_handler.py`
- **Что сделать:** данные `ReferralInfo` (`l1_invited: int`, `l2_invited: int`,
  `total_earned_rubles: float`); метод `get_referral_info(internal_user_id)` собирает
  `count_by_level(1)`, `count_by_level(2)` и `rubles_from_kopecks(sum_credits(user))`.
- **Acceptance:** корректно агрегирует; деньги в копейках внутри, рубли — только в поле отображения.

## Фаза 3 — UI и тесты

### Задача 3.1 — Рендер динамики в `text_referral_program`
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py` (`text_referral_program`)
- **Что сделать:** отрисовать «💸 Всего заработано: {total_earned_rubles:.2f} ₽», блок
  «👤 Приглашено:» с прямыми (L1) и косвенными (L2) по строкам.
- **Acceptance:** читаемо в Telegram; корректный падёж/формат; HTML-теги валидны.

### Задача 3.2 — Тесты
- **Файлы:** `backend/tests/test_domain_referral.py` (и при необходимости — репозиторные)
- **Что сделать:** покрыть `count_by_level`, `sum_credits`, сборку `ReferralInfo`,
  конвертацию копеек→рубли, рендер `text_referral_program`.
- **Acceptance:** `pytest -q` зелёный; есть кейсы «нет данных → нули» и «есть данные → корректная сумма».

## Стратегия тестирования

- Юнит: `backend/tests/test_domain_referral.py` — агрегации и `rubles_from_kopecks`.
- In-memory репозитории покрывают `count_by_level`/`sum_credits` без БД.
- При желании — `backend/scripts/run_postgres_mvp_smoke_local.py` для PostgreSQL-проверки (опционально).

## Риски и откат

- Риск: расхождение суммы `sum_credits` с отображаемым балансом → митигация тестом «сумма == баланс».
- Откат: фича чисто аддитивная (новый метод + строки UI); откат = revert MR, без миграций.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff-линт чист (`ruff check` + `ruff format --check`)
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
- [ ] Миграций нет — ничего откатывать в БД не требуется
