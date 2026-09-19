# Plan — Автоматическая проверка подключения (Connection Check)

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-vpn-connection-check-design.md`
- **Ветка:** `feat/vpn-connection-check`

## Сводка

Веб-страница `/check/<token>` с серверным IP-детектом «трафик идёт через наш VPN», замером
задержки/скорости и кнопками вердикта; запись результатов в `connection_checks`; кнопка в боте.
4 фазы: backend-ядро → интеграция (trial_feedback, бот, nginx) → frontend → валидация/MR.

## Фаза 1 — Backend-ядро

### Задача 1.1 — Миграция `connection_checks`
- **Файлы:** `backend/migrations/093_connection_checks.sql`
- **Что сделать:** additive `CREATE TABLE IF NOT EXISTS connection_checks` (id BIGSERIAL PK,
  internal_user_id FK→user_identities ON DELETE CASCADE, connected BOOL NOT NULL DEFAULT FALSE,
  detected_server_label TEXT NOT NULL DEFAULT '', latency_ms INT CHECK 0..60000 NULL-able,
  throughput_kbps INT CHECK 0..1000000 NULL-able, platform TEXT NOT NULL DEFAULT '',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()) + индексы `(internal_user_id, created_at DESC)`,
  `(created_at DESC)`.
- **Acceptance:** повторный прогон миграции идемпотентен (IF NOT EXISTS).

### Задача 1.2 — Application-модуль: IP-детект и запись
- **Файлы:** `backend/src/app/application/connection_check.py` (новый)
- **Что сделать:**
  - `connection_check_enabled() -> bool` — env `CONNECTION_CHECK_ENABLE` (default off).
  - `real_client_ip(request) -> str` — валидируемый `X-Real-IP` (одиночный IP), fallback
    `request.client.host`; невалидное значение → fallback.
  - `async resolve_active_server_ips(pool) -> dict[label, set[ip]]` — активные `vpn_servers`
    (все surface), DNS через `loop.getaddrinfo`, модульный кеш TTL 600с, при ошибке резолва —
    пустое множество (fail-open), исключение не наружу.
  - `match_server_for_ip(ip, servers) -> label | None`.
  - `async record_connection_check(pool, internal_user_id, *, connected, server_label,
    latency_ms, throughput_kbps, platform) -> bool` — клампинг, fail-open, без IP в логах.
- **Acceptance:** юнит-тесты: матч v4; отсутствие матча; кеш (второй вызов без DNS);
  невалидный X-Real-IP → fallback; клампинг latency/throughput.

### Задача 1.3 — Web-эндпоинты и маршруты
- **Файлы:** `backend/src/app/web_api/connection_check.py` (новый),
  `backend/src/app/web_api/app.py` (маршруты), `backend/src/app/runtime/telegram_webhook_main.py`
  (проксируются общим `/api/` — проверить, что ничего дополнительно не нужно).
- **Что сделать:** 5 хендлеров по spec (`GET {token}`, `GET ping`, `GET speed`, `POST result`,
  `POST feedback`). Entitlement-проверка — тот же запрос, что в `/sub` (`state_label='active'`,
  `keys_deleted_at IS NULL`, trial/active окно). Rate-limit: in-memory per-IP
  (status/result/feedback 30/мин, ping 60/мин, speed 10/мин), общий модульный стор с cleanup.
  `feedback`: funnel-событие `connection_confirmed`/`connection_problem` surface=web,
  dedup `webcheck:{uid}:{ГГГГММДД}`, platform из enum; при problem+reason → `record_trial_feedback`.
- **Acceptance:** контрактные тесты: 200/404 (нет токена)/403 (истёк)/429; ping/speed байты и
  `Cache-Control: no-store`; feedback пишет funnel-событие; отказ БД не меняет HTTP-статус
  статус-эндпоинта.

## Фаза 2 — Интеграция: trial_feedback, бот, nginx

### Задача 2.1 — `trial_feedback` принимает route=''
- **Файлы:** `backend/src/app/application/trial_feedback.py`
- **Что сделать:** в `_valid_context` для `connection_problem` разрешить `connection_route=''`
  (web-источник; бот по-прежнему шлёт direct/guided). Обновить докстрок.
- **Acceptance:** тест: web-запись с route='' проходит, невалидный route по-прежнему отвергается.

### Задача 2.2 — Кнопка в fast-path клавиатурах бота
- **Файлы:** `backend/src/app/bot_transport/storefront_ui.py`
- **Что сделать:** хелпер `connection_check_url(subscription_url) -> str | None` (замена
  хвоста `/sub/{token}` → `/check/{token}` с валидацией); в `connect_fastpath_keyboard` —
  URL-кнопка «🧫 Проверить подключение» (только при `connection_check_enabled()`; None-URL →
  кнопка не добавляется). Guided-шаги (iOS/Android Happ и т.п.) не трогаем — у них нет
  subscription_url в области видимости, выносить кнопку туда следующим MR при необходимости.
- **Acceptance:** тесты клавиатур: кнопка есть при флаге+валидном URL, нет без флага/при
  невалидном URL; остальные ряды не изменились.

### Задача 2.3 — nginx: маскировка токена
- **Файлы:** `nginx/conf.d/default.conf`
- **Что сделать:** расширить `map $request_uri $request_uri_masked`: `ping`/`speed` видимы,
  `/check/<token>`, `/api/v1/check/<token>` и `/api/v1/check/<token>/result` маскируются.
- **Acceptance:** regex-паттерны не ломают существующую маскировку `/sub/`.

## Фаза 3 — Frontend

### Задача 3.1 — API-клиент
- **Файлы:** `frontend/src/entities/connection-check/api.ts`
- **Что сделать:** `checkApi.status(token)`, `.ping()`, `.speed(bytes)`, `.result(token, payload)`,
  `.feedback(token, payload)`; типы; plain `fetch` без csrf (bearer-токен в пути).
- **Acceptance:** vitest: парсинг ответов, обработка 403/404/429.

### Задача 3.2 — Страница `/check/[token]`
- **Файлы:** `frontend/src/app/(site)/check/[token]/page.tsx`,
  `frontend/src/features/connection-check/` (компоненты), возможно `next.config` — проверить,
  что динамический сегмент не требует generateStaticParams.
- **Что сделать:** состояния loading / error / not-found / expired / ok(disabled) / verdict;
  автоматический прогон: статус → 3×ping (медиана) → speed 256KB → POST result; кнопки
  «✅ Всё работает» / «⚠️ Есть проблема» → чипы 6 причин (коды бота) → POST feedback → thanks;
  платформа по UA; подсказка про IPv6/«включите VPN и обновите»; ссылка в поддержку;
  `export const dynamic = "force-dynamic"` при необходимости.
- **Acceptance:** vitest: рендер состояний, мок fetch, медиана задержки, кнопки шлют корректные
  payload; `npm run lint`/`build` зелёные.

## Фаза 4 — Валидация и MR

- Полный `pytest -q`; `python scripts/run_mvp_repo_release_health_check.py`;
  `ruff check` + `ruff format --check`; frontend `npm run lint && npm run build` + vitest.
- PostgreSQL smoke локально, если доступен; миграция повторяема.
- Diff-review: оплата/issuance/lifecycle не затронуты; нет IP в логах; нет секретов в коде.
- MR (рус.), CI зелёный, squash-merge, удалить ветку.

## Стратегия тестирования

- Юнит: `backend/tests/test_connection_check.py` (IP-матч, кеш, клампинг, эндпоинты),
  обновить `test_trial_feedback.py` (route=''), тесты клавиатур в соответствующем файле.
- Frontend: `api.test.ts` + `CheckPage.test.tsx` (мок fetch).
- Интеграционные: существующий postgres-smoke прогоняет миграции; добавить сценарий
  insert+constraint в smoke-набор, если там есть паттерн (иначе — юнит с in-memory невалиден,
  ограничимся контрактными тестами с мок-pool).

## Риски и откат

- Риск: DNS-резолв в запросе → задержка. Митигация: TTL-кеш 600с, резолв только активных серверов,
  fail-open `connected=null`.
- Риск: false negative (IPv6/NAT) → текст-подсказка на странице, «не обнаружен» ≠ «плохой VPN».
- Риск: abuse speed-эндпоинта → rate-limit 10/мин/IP, максимум 1МБ, случайные байты (нежаты gzip).
- Откат: `CONNECTION_CHECK_ENABLE=0` (кнопка и запись выключаются), эндпоинты остаются
  безвредными; миграция additive — отката не требует.

## Чек-лист перед PR

- [ ] `cd backend && python -m pytest -q` — зелёный
- [ ] `python scripts/run_mvp_repo_release_health_check.py` — проходит
- [ ] ruff чист; frontend lint/build/tests зелёные
- [ ] Коммиты на русском, без Co-Authored-By
- [ ] Миграция повторяема и additive
- [ ] Spec/plan соответствуют реализации
