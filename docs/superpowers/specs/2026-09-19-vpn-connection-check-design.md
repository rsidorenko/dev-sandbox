# Design Spec — Автоматическая проверка подключения (Connection Check)

- **Автор / ответственный:** команда Bravada VPN
- **Дата:** 2026-09-19
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-09-19-vpn-connection-check.md`

## Контекст и проблема

Сейчас единственный сигнал «пользователь подключился» — ручные кнопки «✅ VPN работает» /
«❓ Не получилось» в fast-path клавиатуре (`storefront_ui.py:2142`). Это субъективно: пользователь
может нажать «работает» не проверяя, или нажать «не получилось» из-за проблемы в одном приложении.
Аналитика воронки не может отличить «реально подключён» от «нажал кнопку».

Практика Mullvad (Connection Check) и Proton (автотюнинг протоколов): показывать пользователю
автоматический вердикт «трафик действительно идёт через VPN» вместо ручного подтверждения.

Дополнительная проблема: первый трафик в `traffic_stats` фиксирует >0 байт за день — это может быть
фоновый обмен устройства, а не рабочий VPN. Автоматическая проверка даёт точечный достоверный сигнал
«в момент проверки трафик шёл через наш сервер N».

## Цели

- Веб-страница `/check/<token>`, определяющая по IP-адресу запроса, идёт ли трафик через наши серверы.
- Показ сервера, задержки (RTT) и скорости загрузки; кнопки «Всё работает» / «Есть проблема».
- Сохранять результат проверки (connected, сервер, latency, throughput, платформа) без IP-адресов.
- Кнопки пишут в существующие funnel-события `connection_confirmed`/`connection_problem` (surface=web),
  причина проблемы — в `trial_feedback` теми же кодами, что в боте.
- Кнопка «🧫 Проверить подключение» в fast-path клавиатурах бота рядом с ручными кнопками.
- Feature-flag `CONNECTION_CHECK_ENABLE` (default off), включение через env как у остальных флагов.

## Non-goals

- Не меняем ручные кнопки бота и не выносим их в эксперимент (A/B против Connection Check — отдельная задача).
- DNS-leak и WebRTC-leak проверки: требуют wildcard-DNS инфраструктуры / ненадёжны в мобильных
  браузерах — вне скоупа, задокументировано как ограничение.
- Не трогаем выдачу ключей, оплату, lifecycle, уведомления.
- Synthetic-мониторинг серверов (latency p50/p95 по серверам) — в `feat/admin-quality-dashboard`.
- Split tunneling и автопереключение протоколов — продуктовые задачи, не диагностика.

## Текущее состояние

- `/sub/{token}` (`web_api/subscription.py:72`) — bearer-token endpoint с entitlement-гейтом
  (`_entitlement_active`), in-memory rate limit, `?open=karing` deep-link паттерн.
- uvicorn запускается без `--forwarded-allow-ips` (`backend/entrypoint.sh:26`) →
  `request.client.host` = IP nginx-контейнера. Но nginx ставит `X-Real-IP $remote_addr`
  (`nginx/conf.d/default.conf:97`) — перезаписываемый, неподдельный клиентом заголовок.
- `vpn_servers` (`migrations/024`): `server_host` (hostname), `client_surface` (057), `is_active`.
  Egress IP сервера = IP `server_host` (Xray на той же машине; для Cloud.ru relay — тоже).
- funnel-события (`application/funnel_events.py`): закрытый enum, `surface ∈ {telegram, web}`,
  fail-open запись, dedup `(surface, event_name, dedup_key)`.
- `trial_feedback` (`application/trial_feedback.py`): connection_problem требует
  `connection_route ∈ {direct, guided}` — с web маршрут неизвестен.
- nginx маскирует `/sub/<token>` в access-log (PD-audit R-04, `map $request_uri $request_uri_masked`).
- Frontend: Next.js app router, `(site)` группа публичных страниц, `apiFetch` (`shared/api/client`),
  vitest-тесты рядом с исходниками.

## Предлагаемое решение

### Backend (4 эндпоинта, все за rate-limit по IP)

1. `GET /api/v1/check/{token}` — валидация токена + entitlement (как `/sub`); определяет клиентский IP
   из `X-Real-IP` (fallback `request.client.host`), матчит против множества IP активных `vpn_servers`
   (DNS-resolve `server_host` с TTL-кешем 10 мин, fail-open: при невозможности резолва —
   `connected: null`). Ответ: `{ok, enabled, connected, server_label, server_country}`.
2. `GET /api/v1/check/ping` — 1 байт, `no-store` — для JS-замера RTT (3 запроса, медиана на клиенте).
3. `GET /api/v1/check/speed?bytes=N` — N случайных байт, N ∈ [65536, 1048576] (default 262144),
   `application/octet-stream`, `no-store`.
4. `POST /api/v1/check/{token}/result` — тело `{latency_ms?, throughput_kbps?, platform?}`;
   `connected` и `server_label` сервер определяет сам по IP этого запроса (клиентскому значению
   не доверяем), числа клампятся в границы CHECK-ов колонок. Вставка строки в `connection_checks`,
   fail-open.
5. `POST /api/v1/check/{token}/feedback` — `{verdict: ok|problem, reason_code?, platform?}`:
   funnel-событие `connection_confirmed`/`connection_problem` (surface=web, dedup
   `webcheck:{uid}:{ГГГГММДД}`), при `problem` + `reason_code` — запись в `trial_feedback`
   с `connection_route=''`.

### Таблица `connection_checks` (миграция 093, additive)

`internal_user_id` (FK ON DELETE CASCADE), `connected` bool, `detected_server_label` text,
`latency_ms` int NULL, `throughput_kbps` int NULL, `platform` text, `created_at`. Индексы по
`(internal_user_id, created_at DESC)` и `(created_at DESC)`. IP-адреса не хранятся и не логируются.

### Frontend

Страница `frontend/src/app/(site)/check/[token]/page.tsx` (клиентский компонент) +
`features/connection-check/`: статус-карта («✅ VPN подключён — сервер Нидерланды 🇳🇱» /
«⚠️ VPN не обнаружен» + подсказка включить и обновить / «не удалось проверить»), тесты задержки и
скорости с прогрессом, кнопки вердикта, чипы причин (6 кодов из бота), экран благодарности,
ссылка в поддержку. Платформа определяется по UA (android/ios/win/mac/tv→'').

### Bot

В `connect_fastpath_keyboard` и trial-fastpath клавиатуру добавляется URL-кнопка
«🧫 Проверить подключение» → `{site}/check/{token}` (токен извлекается из `subscription_url`
заменой `/sub/` → `/check/`, с валидацией формата). Кнопка показывается только при
`CONNECTION_CHECK_ENABLE`.

### nginx

`map $request_uri $request_uri_masked` дополняется маскировкой `/check/<token>` и
`/api/v1/check/<token>(/result)`; `ping`/`speed` (без секрета) остаются видимыми.

## Альтернативы

- **Доверять `request.client.host`** — не работает: без `--forwarded-allow-ips` это IP nginx.
- **Включить `FORWARDED_ALLOW_IPS=*` в uvicorn** — меняет глобальное поведение (rate-limit `/sub`
  начнёт делить по XFF, который клиент может подделать appending-ом) — хуже, чем точечный `X-Real-IP`.
- **Хранить egress-IP серверов в БД** — ещё одна ручная сущность; DNS-резолв с кешем самоподдерживается.
- **Сравнение «IP с VPN vs IP без VPN»** — нельзя выключить VPN пользователя remotely; отклонено.
- **Клиент сам сообщает connected** — подделывается; серверный IP-матч авторитетен.

## Влияние

- **Файлы:** `web_api/connection_check.py` (новые хендлеры + маршруты в `app.py`),
  `application/connection_check.py` (IP-резолв/матч/запись), `application/trial_feedback.py`
  (разрешить `route=''` для web), `bot_transport/storefront_ui.py` (+1 кнопка),
  миграция `093_connection_checks.sql`, nginx map, frontend страница/фича/API-клиент.
- **Миграции:** additive `CREATE TABLE IF NOT EXISTS` + 2 индекса; повторяемая.
- **Безопасность:** bearer-токен как в `/sub`; rate-limit in-memory на все 5 путей; `X-Real-IP`
  неподделаем за nginx (порт backend не опубликован — только nginx публикует 80/443);
  маскировка токена в nginx-логах; клампинг чисел; никакие IP не сохраняются.
- **Биллинг:** не изменяется.
- **Retention / lifecycle:** не изменяется; `ON DELETE CASCADE` совместим с удалением тестовых юзеров.
- **VPN-провайдер:** не изменяется (только чтение `vpn_servers`).

## Ограничения (принято осознанно)

- Если сайт доступен по IPv6, а VPN туннелирует только IPv4 — проверка увидит ISP-адрес и скажет
  «не обнаружен» (false negative). Подсказка на странице это оговаривает.
- Egress IP считается равным IP `server_host`; при NAT/multi-IP серверах возможен false negative.
- In-memory rate limit per-process (как существующий `/sub`).

## Открытые вопросы

- Нет блокирующих. Пороги скорости/задержки для «хорошо/плохо» на странице — мягкая шкала
  (цвет по диапазонам), без жёстких порогов осуждения.

## Критерии приёмки

- [x] `/check/<token>` показывает вердикт по IP, сервер, задержку и скорость; кнопки работают.
- [x] Результат пишется в `connection_checks` без IP; сбой записи не ломает страницу.
- [x] Кнопки пишут funnel-события surface=web; причина — в `trial_feedback` с route=''.
- [x] Кнопка в fast-path клавиатурах бота появляется только при `CONNECTION_CHECK_ENABLE`.
- [x] nginx маскирует токен в `/check/` и `/api/v1/check/` путях.
- [x] Тесты: IP-матч (v4/нет совпадения/кеш), X-Real-IP приоритет и валидация, контракты
      эндпоинтов (200/403/404/429, клампинг), route='' в trial_feedback, UI-состояния страницы.
- [x] `pytest -q`, ruff, release-health, frontend lint/build/tests — зелёные.
