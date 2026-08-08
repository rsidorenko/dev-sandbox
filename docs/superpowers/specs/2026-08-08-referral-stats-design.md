# Design Spec — Динамика рефералки: приглашено L1/L2, всего заработано

- **Автор:** rsidorenko
- **Дата:** 2026-08-08
- **Статус:** reviewed (ретроспективно — фича уже реализована)
- **Связанный plan:** [`docs/superpowers/plans/2026-08-08-referral-stats.md`](../plans/2026-08-08-referral-stats.md)
- **Ветка:** `feat/bot-referral-stats`

> **Тайминг-примечание.** Этот spec оформлен **ретроспективно** по коду ветки `feat/bot-referral-stats`
> и служит **живым эталоном** SDD-паттерна. Ссылки на символы (`get_referral_info`, `ReferralInfo`,
> `count_by_level`, `sum_credits`, `text_referral_program`) валидны на этой ветке и станут доступны
> на `main` после мёрджа соответствующего MR. До тех пор код смотри в ветке `feat/bot-referral-stats`.

## Контекст и проблема

Реферальная программа — двухуровневая (L1: 25–35 %, L2: 2–5 % в зависимости от плана, см.
`domain/referral.py`). Пользователь видит свою реферальную страницу в боте, но **не видит
динамики**: сколько людей приглашено (раздельно по уровням) и сколько всего заработано. Без
этого снижается мотивация делиться ссылкой и нет обратной связи по эффективности программы.

## Цели

- Показать пользователю в разделе рефералки: **приглашено L1** (прямые), **приглашено L2**
  (косвенные), **всего заработано** (сумма реферальных начислений в рублях).
- Использовать уже существующие данные (`referral_relationships`, реферальные транзакции) —
  без новых таблиц и миграций.

## Non-goals

- Не делаем детализацию по каждому рефералу (список/даты/суммы по людям) — отдельная задача.
- Не меняем ставки комиссий и логику начислений.
- Не трогаем web-API рефералки (`web_api/referral.py`) — только отображение в боте.

## Текущее состояние

Читать реальный код (CodeGraph → Read):

- `backend/src/app/application/referral_handler.py` — есть `get_referral_balance()`,
  `apply_referral_on_registration()`, `get_or_create_referral_code()`. Агрегирующего метода
  «статистика приглашений + заработок» **нет** — его добавляет эта фича.
- `backend/src/app/domain/referral.py` — `rubles_from_kopecks(kopecks: int) -> float`,
  `level1_commission_rate` / `level2_commission_rate`, `build_commissions_for_payment`.
- `backend/src/app/bot_transport/storefront_ui.py` — `text_referral_program(...)` рендерит
  блок рефералки; ранее без динамики.
- Persistence: `relationship_repo` (`referral_relationships`), репозиторий реферальных
  транзакций (`billing/referral credits`). Методов `count_by_level` / `sum_credits` для
  агрегаций раньше не было.
- ADR: `docs/architecture/` — биллинг и реферальная модель; `backend/docs/` — runbook'и.

## Предлагаемое решение

1. В `application/referral_handler.py` ввести данные **`ReferralInfo`** (`l1_invited`, `l2_invited`,
   `total_earned_rubles`) и метод **`get_referral_info(internal_user_id)`**, агрегирующий:
   - `l1_invited = relationship_repo.count_by_level(user, 1)`
   - `l2_invited = relationship_repo.count_by_level(user, 2)`
   - `total_earned_rubles = rubles_from_kopecks(transaction_repo.sum_credits(user))`
2. В контрактах/репозиториях добавить `count_by_level(level)` и `sum_credits(user)` с реализациями
   для PostgreSQL и in-memory (для тестов).
3. В `storefront_ui.text_referral_program(info)` отрисовать строки динамики.
4. Деньги — строго через копейки (`*_kopecks`) с конвертацией в рубли только на отображении.

## Альтернативы

- Считать «всего заработано» на лету из `billing_events_ledger` — отклонено: есть отдельная
  сущность реферальных транзакций с готовым балансом; используем её (`sum_credits`).
- Отдельный вид/экран статистики — отклонено: лишний клик; динамика нужна прямо на странице
  рефералки.

## Влияние

- **Файлы:** `application/referral_handler.py`, `domain/referral.py` (без изменений — только
  импорт `rubles_from_kopecks`), `persistence/referral_contracts.py`, `persistence/postgres_referral.py`,
  `persistence/in_memory.py`, `bot_transport/storefront_ui.py`.
- **Миграции БД:** нет.
- **Безопасность:** без изменений; данные только свои (по `internal_user_id`).
- **Биллинг:** читаем существующие начисления; **новых списаний нет**; деньги в копейках.
- **Retention / lifecycle:** не затрагивается.
- **VPN-провайдер:** не затрагивается.

## Открытые вопросы

- Округлять «всего заработано» до 2 знаков (`.2f`) — решено: да (формат `{:.2f} ₽`).

## Критерии приёмки

- [ ] В боте, в разделе рефералки, видны строки: «Всего заработано», «Приглашено: Прямые / Косвенные».
- [ ] `get_referral_info` корректно считает L1/L2 и сумму (юнит-тесты).
- [ ] `count_by_level`/`sum_credits` работают в PostgreSQL и in-memory.
- [ ] Деньги считаются в копейках, в рубли конвертируются только на отображении.
- [ ] `pytest -q` зелёный, `python scripts/run_mvp_repo_release_health_check.py` проходит.
