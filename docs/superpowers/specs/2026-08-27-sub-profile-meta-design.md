# Design Spec — Метаданные подписки /sub/ (title, дни, самолётик-на-бота)

- **Автор / ответственный:** rsidorenko / Claude Code
- **Дата:** 2026-08-27
- **Статус:** draft
- **Связанный plan:** `docs/superpowers/plans/2026-08-27-sub-profile-meta.md`

## Контекст и проблема

Happ (и прочие клиенты — Karing/v2rayN) поддерживают стандартные метаданные
подписки через HTTP-заголовки ответа `/sub/` (доки Happ: App management,
standard parameters — Provider ID не нужен). Сейчас `/sub/` отдаёт только тело
(base64-ссылки) + Happ-специфичный `routing`. Юзер хочет:

1. имя подписки «Bravada VPN» + красивый смайлик;
2. количество оставшихся дней в приложении; если дней нет — подписка выглядит
   неактивной/истёкшей;
3. «самолетик» на баре подписки со ссылкой на нашего Telegram-бота.

## Цели

- `profile-title` = «🛡️ Bravada VPN» (base64: UTF-8, лимит 25 симв. — 14 ✓)
- `subscription-userinfo: upload=0; download=0; total=0; expire=<unix>` —
  Happ сам рисует оставшиеся дни; после истечения — состояние «истекла»
- `support-url: https://t.me/<BOT_USERNAME>` — иконка Telegram (самолётик)
  справа на баре, ведёт в бота
- `profile-update-interval: 24` — ежедневный автообновление, чтобы дни не
  протухали после продления

## Non-goals

- Не трогаем entitlement-гейт (403 для истёкших) — «неактивна» получается из
  expire-таймстемпа + 403 на обновлении, менять семантику гейта не будем.
- Не считаем реальный трафик (upload/download/total = 0 — продаём время,
  не байты; Happ при total=0 показывает только срок).
- Advanced-параметры (sub-expire и т.п.) не трогаем — они требуют Provider ID.

## Текущее состояние

- `backend/src/app/web_api/subscription.py:58` — `handle_subscription`: row
  уже содержит `trial_expires_at`/`active_until_utc`; ответ — PlainTextResponse,
  при `SUB_HAPP_ROUTING_ENABLE` + Happ-UA добавляется заголовок `routing`.
- Прокси-слой УЖЕ готов: `_SUBSCRIPTION_HEADERS`
  (`backend/src/app/runtime/telegram_webhook_main.py:232`) включает
  `profile-title`, `profile-update-interval`, `subscription-userinfo`,
  `support-url`; форвардинг протестирован
  (`backend/tests/test_telegram_webhook_main.py:448-459`).

## Предлагаемое решение

Чистый хелпер `_profile_meta_headers(row) -> dict[str, str]` в
`subscription.py`; при новом флаге `SUB_PROFILE_META_ENABLE` (opt-in, по умолчанию
выключен — как `SUB_HAPP_ROUTING_ENABLE`) заголовки навешиваются на ответ для
ВСЕХ клиентов (это стандарт конвенции, не Happ-only; чужие клиенты игнорируют
незнакомые заголовки, Karing умеет `subscription-userinfo`). expire = `max()`
от непустых `trial_expires_at`/`active_until_utc` — та же логика «later wins»,
что и в entitlement. `support-url` читает `BOT_USERNAME` на каждый запрос
(горячая смена env без правки кода); без него заголовок не эмитится.

## Альтернативы

- Доставка через тело (`#profile-title: …`) — отвергнуто: тело = base64-список
  ссылок, вставлять plain-строки нельзя; заголовки уже поддержаны прокси.
- Эмит только для Happ по UA — отвергнуто: заголовки стандартные, Karing-юзерам
  дни тоже полезны; UA-гейт остаётся только у Happ-специфичного `routing`.

## Влияние

- **Файлы:** `backend/src/app/web_api/subscription.py` (+хелпер, +флаг),
  `backend/tests/test_sub_profile_meta.py` (новый).
- **Миграции БД:** нет.
- **Безопасность:** заголовки не содержат секретов; `BOT_USERNAME` публичен;
  rate-limit `/sub/` не меняется (интервал 24ч ≈ 1 запрос/день на юзера).
- **Билдинг/Retention/VPN-провайдер:** не затронуты.

## Открытые вопросы

- Нет.

## Критерии приёмки

- [ ] `SUB_PROFILE_META_ENABLE=1` → ответ `/sub/` несёт 4 заголовка (title
      base64-«🛡️ Bravada VPN», userinfo с корректным expire, support-url
      t.me-бота, interval 24); без флага — не несёт.
- [ ] Прокси (webhook-режим) заголовки пропускает (уже покрыто тестом).
- [ ] Тесты покрывают: base64-раундтрип и ≤25 симв.; expire=max(trial,
      active_until); нет дат → нет userinfo; @-префикс BOT_USERNAME; пустой
      BOT_USERNAME → нет support-url.
- [ ] `pytest -q` зелёный, ruff чист.

---

## Инкремент 2 — дни через announce (2026-08-28)

Пользователь попросил показывать остаток подписки в днях, «как у другого VPN»
(скрин: нижняя строка чипов «221.43 GiB left | 4 days | Active»). Разбор:

- Нативный чип «N days» Happ привязан к строке статуса с трафиком; без
  `total>0` строка не рисуется. Владелец явно отказался от декоративной
  квоты трафика («про трафик вообще не нужно») → чип недоступен.
- Стандартный (без Provider ID) текстовый механизм — `announce` (plain или
  Base64, ≤200 отображаемых символов). Advanced-параметры `sub-info` /
  `sub-expire` требуют Provider ID — не используем (см. осн. часть spec'а).

Решение: `announce: base64("⏳ Осталось: N <день/дня/дней>")`, N — ceil
остатка до того же `expire`, что в `subscription-userinfo` (консистентность
даты и счётчика). Истёкшим / без дат — не эмитируем. Автообновление 24→12ч,
чтобы счётчик не отставал от даты более чем на полсуток.
