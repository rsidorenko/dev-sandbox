# Plan — Метаданные подписки /sub/

- **Дата:** 2026-08-27
- **Spec:** `docs/superpowers/specs/2026-08-27-sub-profile-meta-design.md`
- **Ветка:** `feat/sub-profile-meta`

## Сводка

Эмит стандартных метаданных подписки (`profile-title`, `subscription-userinfo`,
`support-url`, `profile-update-interval`) из `/sub/` за флагом
`SUB_PROFILE_META_ENABLE`. Одна фаза: хелпер + вайринг + тесты. Прокси-слой
уже готов (см. spec «Текущее состояние»).

## Фаза 1 — эмит заголовков

### Задача 1.1 — хелпер `_profile_meta_headers`
- **Файлы:** `backend/src/app/web_api/subscription.py`
- **Что сделать:** флаг `_PROFILE_META_ENABLE` (env `SUB_PROFILE_META_ENABLE`,
  паттерн как `_HAPP_ROUTING_ENABLE`); константа `_DEFAULT_PROFILE_TITLE =
  "🛡️ Bravada VPN"`; env-оверрайд `SUB_PROFILE_TITLE`; хелпер возвращает
  dict: title как `base64:<b64 utf-8>`, interval `24`, userinfo с
  `expire=int(max(непустые trial_expires_at, active_until_utc).timestamp())`
  (нет дат → ключа нет), support-url `https://t.me/<BOT_USERNAME без @>`
  (пусто → ключа нет).
- **Acceptance:** чистая функция, детерминированная по row+env.

### Задача 1.2 — вайринг в `handle_subscription`
- **Файлы:** `backend/src/app/web_api/subscription.py` (после сборки response)
- **Что сделать:** `if _PROFILE_META_ENABLE: response.headers.update(...)`;
  обновить докстринг модуля.
- **Acceptance:** без флага поведение/ответ байт-в-байт как раньше.

### Задача 1.3 — тесты
- **Файлы:** `backend/tests/test_sub_profile_meta.py` (новый; образец —
  `test_sub_happ_filter.py`: `_row(**overrides)`, aware-datetime)
- **Acceptance:** все кейсы из критериев приёмки spec'а.

## Стратегия тестирования

- Юнит: `test_sub_profile_meta.py` — base64-раундтрип, ≤25 симв., max(trial,
  active_until), отсутствие дат, @-стриппинг, пустой BOT_USERNAME, interval.
- Прокси-форвардинг уже покрыт `test_telegram_webhook_main.py`.
- Интеграционные не нужны (заголовки — чистая функция от row).

## Риски и откат

- Риск: не-ASCII в заголовке → митигировано base64-префиксом.
- Риск: чужие клиенты показывают «0/0» трафика → total=0, Happ/Karing при
  total=0 рисуют только срок; консистентно с примером из доков Happ.
- Откат: `SUB_PROFILE_META_ENABLE` не задан (дефолт) — фича выключена; код
  можно revert'нуть одним коммитом, миграций нет.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] ruff чист (`ruff check` + `ruff format --check`)
- [ ] Коммиты на русском, без `Co-Authored-By`
- [ ] После мерджа: hot-change `/opt/bravada/.env`
      (`SUB_PROFILE_META_ENABLE=1`, проверить наличие `BOT_USERNAME`) +
      `docker compose -f docker-compose.prod.yml up -d --force-recreate backend`
- [ ] LIVE-check: `curl -sD- <prod>/sub/<token>` с UA Happ → 4 заголовка.

---

## Инкремент 2 — «Осталось: N дней» через announce (2026-08-28)

- **Ветка:** `feat/sub-announce-days`
- **Запрос:** показывать остаток подписки в днях (как у VPN со скриншота
  пользователя), не только дату; трафик НЕ показывать (явное решение
  владельца — нативный чип «N days» Happ рисует только при total>0, фейковую
  квоту отвергли).
- **Решение:** стандартный параметр `announce` (дока Happ → App management,
  без Provider ID, ≤200 симв.): `base64("⏳ Осталось: N день/дня/дней")`,
  N = ceil((max(trial, active_until) − now)/86400), русский плюрализм
  (`_ru_days`). Истёкшая подписка / нет дат → заголовок не эмитируется.
  `profile-update-interval` 24→12ч — дни не отстаивают от даты на баре.
- **Тесты:** 21 новый кейс (склонения 1/2/5/11/21/101/111, ceil, <1 дня,
  expired, консистентность с expire, ≤200 симв., DI-параметр `now=`).
- **Env:** новых нет — входит в `SUB_PROFILE_META_ENABLE`, прокси-вайтлист
  `announce` уже содержал.
