# Runbook: миграция main из Франции (Aeza Paris) в РФ (локализация ч.5 ст.18 152-ФЗ)

Дата: 2026-09-11. Контекст: PD-аудит `docs/audit/pd-forensic-2026-09-11/` — finding HIGH:
вся БД (ПД пользователей) + зашифрованные age-бэкапы физически находятся на main
`109.120.178.49` (AS210644 Aeza, Париж, Франция) при требовании хранить ПД граждан РФ
на территории РФ (ч.5 ст.18 152-ФЗ).

> ⚠️ Runbook — план, а не автоскрипт. Каждый этап выполняется оператором вручную
> с проверкой результата. Общий простой сервиса при последовательном выполнении:
> 20–40 минут (окно DNS-flip).

---

## 0. Что переносится и что остаётся

| Компонент | Сейчас | Действие |
|---|---|---|
| backend (bot) + webhook | main, docker `bravada-backend-1` | перенос артефакт-деплоем |
| PostgreSQL + volume pgdata | main, docker named-volume | pg_dump → restore (см. §5) |
| nginx + TLS certs | main, контейнер + том certs | новый выпуск LE на новом IP |
| frontend (Next.js) | main, контейнер | пересборка (артефакт) |
| admin-панель | main, WEB_ADMIN_* env | вместе с .env |
| /opt/bravada-backups (age-шифр.) | main | копия на новый + остаться у оператора |
| CI-runner `bravada-ci-eu` 45.137.42.105 | отдельная ВМ | НЕ переносится; обновить PROD_HOST (§8) |
| GPT-контейнеры `/opt/bravada-gpt` | main | отдельное решение оператора (не блокирует миграцию; при переносе — отдельный аудит ПД) |
| VPN-флот (entries/egress/CDN) | вне main | не трогаем; правятся только allowlist-ы (§7) |

Выбор хостера: RU-провайдер с уже проверенной инфраструктурой — **cloud.ru**
(там же живут DE/FI/US entries: известные готчи SG/MTU) или Yandex Cloud
(готча: недоступность GitHub с ВМ → SFTP-build). Ниже — общий порядок, он одинаков.

## 1. Заказ и подготовка ВМ (за день до миграции)

1. VPS: 4 vCPU / 8 GB RAM / 60+ GB SSD, Ubuntu 24.04, регион RU.
   Текущая загрузка main: ~11 GB диска, БД < 300 MB в gzip.
2. Завести пользователя `romtan` + ssh-ключ (ed25519), root-логин выключить после настройки.
3. База:
   ```bash
   ufw default deny incoming && ufw allow 22,80,443/tcp && ufw enable
   apt-get update && apt-get install -y fail2ban docker.io docker-compose-v2 age
   # MTU-гочти (урок 2026-09-01): путь к раннеру/внешнему миру ~1450
   echo '{"mtu":1400}' > /etc/docker/daemon.json && systemctl restart docker
   ```
4. Docker Hub из РФ: если `docker pull` виснет — прописать зеркало в
   `/etc/docker/daemon.json` (`registry-mirrors`). Проверить `docker pull hello-world`.
5. Проверить исходящий доступ: `curl api.telegram.org`, `api.yookassa.ru`,
   `api.crypto.bot`, GitHub (для артефактов; недоступен → SFTP-путь доставки артефакта).

## 2. Предварительные правки на флоте (до DNS-flip, без даунтайма)

Новый IP обозначим `<NEW>`. Старый main `109.120.178.49` — `<OLD>`.

1. **cloud.ru SG (DE/FI/US entries)**: в SG добавить входящий 22 + панельные порты с `<NEW>`
   (сейчас разрешён только `<OLD>`). Без этого reconcile/reprovision перестанет доставать панели.
2. **FRA egress 77.110.100.210**: `ufw allow from <NEW> to any port 10444` (from-origin leg).
3. **CDN origins (89.169.178.45, 93.77.160.88 — id=34 НЕ ТРОГАТЬ иначе)**: SSH-доступ romtan@ c main —
   добавить ключ/allowlist нового main (sshd allow-users / ufw).
4. **LT entry 45.91.52.204**: проверить, есть ли привязки к `<OLD>` (ufw), добавить `<NEW>`.
5. Проверить `vpn_servers` в БД: адреса панелей — они НЕ зависят от main, менять не нужно.

## 3. Перенос .env и секретов

1. С `<OLD>`: `scp /opt/bravada/.env` и `/opt/bravada/backend/.env` (если отдельный) → `<NEW>`:/opt/bravada/.
2. Сверить по чек-листу env из deploy-конфига: `DATABASE_URL`, `TELEGRAM_BOT_TOKEN`,
   `WEBHOOK_SECRET`, `FIELD_ENCRYPTION_KEY` (⚠️ обязательно тот же — им зашифрованы
   пароли панелей в БД), `CHECKOUT_REFERENCE_SECRET`, `BOT_USERNAME`, `SUBSCRIPTION_BASE_URL`,
   `NEXT_PUBLIC_SITE_URL` (без изменений — домен тот же), SMTP-креды, `WEB_ADMIN_*`,
   `JWT_ADMIN_SECRET`, YooKassa/CryptoBot креды.
3. Ничего не регенерировать при миграции — иначе потеря расшифровки панелей/сессий.

## 4. Развёртывание стека на `<NEW>` (до DNS-flip)

1. Артефакт: обычный деплой через CI (после §8) ИЛИ вручную залить release-артефакт в
   `/opt/bravada-release` и выполнить `scripts/ci/deploy_remote.sh` (он сам: бэкап→swap→build).
   Домен пока не указывает сюда — TLS-выпуск будет в §6 после DNS-flip.
2. `docker compose -f docker-compose.prod.yml build` должен пройти (frontend build args
   идут из .env; `NEXT_PUBLIC_YM_ID` остаётся пустым — Метрика выключена, MR !114).
3. Контейнеры пока НЕ запускаем в прод-режиме (или запускаем без webhook), БД ещё не перенесена.

## 5. Перенос БД (окно миграции)

1. На `<OLD>`: остановить запись — `docker compose stop backend` (бот замолчит на 10–20 мин;
   polling-фолбэк не используется, webhook URL ещё ведёт на старый nginx и будет 502 — это ок,
   Telegram ретраит).
2. Финальный дамп с шифрованием (age уже установлен, pubkey на месте):
   ```bash
   docker compose -f /opt/bravada/docker-compose.prod.yml exec -T postgres \
     pg_dump -U vpnbot vpnbot | gzip | age -r "$(cat /root/.config/age/bravada-backup.pub)" \
     > /opt/bravada-backups/db-FINAL-$(date +%Y%m%d-%H%M%S).sql.gz.age
   ```
3. Забрать на локальную машину, расшифровать ключом оператора
   (`D:/VPN-secrets/bravada-backup-age-key.txt`), убедиться `gzip -t` OK, залить
   plain-файл на `<NEW>`:/tmp/db.sql.gz (по SSH — канал зашифрован).
4. На `<NEW>`: поднять только postgres, восстановить:
   ```bash
   docker compose -f docker-compose.prod.yml up -d postgres
   zcat /tmp/db.sql.gz | docker compose exec -T postgres psql -U vpnbot -d vpnbot
   rm /tmp/db.sql.gz   # plaintext не задерживать на диске
   ```
5. Сверка: число юзеров/таблиц (`SELECT count(*) FROM user_identities;` — сверить с дампом),
   `\dt` — 30+ миграций на месте. age-копию ФИНАЛЬНОГО дампа оставить и у оператора.

## 6. DNS-flip + TLS

1. Заранее (за 24 ч) снизить TTL A-записи `bravada-connect.ru` до 60–300 с (DNS-провайдер домена).
2. Переключить A-запись → `<NEW>`. Ждать резолва (`dig +short bravada-connect.ru` с пары сетей).
3. На `<NEW>`: выпустить LE (nginx уже настроен deploy-скриптом; HTTP-01):
   сначала `--staging` (лимиты LE!), потом боевой + deploy-hook. Проверить reload-луп
   (урок split-brain MR !70: nginx должен читать том `certs`).
4. `curl -sI https://bravada-connect.ru/health` (или `/`) — 200/redirect с нового IP
   (`curl --resolve bravada-connect.ru:443:<NEW>` для проверки до резолва DNS).

## 7. Телеграм-webhook + smoke

1. Webhook URL не меняется (тот же домен): после DNS-flip выполнить
   `python scripts/configure_telegram_webhook.py --dry-run`, затем без dry-run
   (или `setWebhook` вручную) — убедиться, что Telegram видит 200 от нового nginx
   (`getWebhookInfo` → `last_error_message` пуст).
2. Smoke-чек (5–10 минут):
   - бот: `/start`, `/menu`, `/status` в Telegram;
   - сайт: главная, логин, тарифы, профиль;
   - `/sub/<token>` реального юзера (base64-тело валидно);
   - тестовая оплата: создать чекаут-URL и пройти его (YooKassa тест/малая сумма) —
     webhook fulfillнулся, подписка применилась;
   - админка: вход, статистика;
   - `docker ps` — все контейнеры Up; `docker logs backend --since 5m` — без ERROR.

## 8. CI-runner и деплой-цель

1. На раннере `bravada-ci-eu` (45.137.42.105): обновить хост прода (alias/IP в
   `~/.ssh/config` или переменной окружения деплоя) `<OLD>` → `<NEW>`.
2. Если PROD-адрес зашит в GitLab CI variables — обновить в настройках проекта
   (Settings → CI/CD → Variables).
3. Прогнать тестовый деплой любого мелкого MR — убедиться, что pipeline деплоит на `<NEW>`.

## 9. Откат (если что-то пошло не так в первые часы)

1. DNS A-запись обратно на `<OLD>` (TTL уже низкий — быстро).
2. На `<OLD>`: `docker compose start backend` (БД там осталась консистентной до
   момента финального дампа; расхождение — только события после flip).
3. Webhook снова сам заходит на `<OLD>` после резолва. Потерянные за окно платежи —
   добираются ретраями YooKassa (webhook-ретраи) и `/success`-командой юзера.
4. Решение о повторной попытке — только после диагноза; НЕ держать два активных
   backend-инстанса одновременно (double-fulfillment риск; идемпотентность БД защищает,
   но webhook-секрет общий — путаница логов).

## 10. Гигиена после стабилизации (48 ч)

1. `<OLD>`: финальный зашифрованный дамп «на выход» → в архив оператора.
2. Полное уничтожение: `docker system prune -a --volumes`, wipe дисков, удаление ВМ у Aeza,
   удаление бэкапов с Aeza-хранилищ (если были снапшоты — удалить и их, это тоже копии ПД).
3. DNS-провайдер: TTL вернуть на дефолт (3600).
4. fail2ban на `<NEW>`: убедиться, что IP оператора в ignoreip (урок: бан собственного IP,
   прыжок через Frankfurt sshpass).
5. Обновить `docs/audit/pd-forensic-2026-09-11/` (finding локализации → closed) и memory
   (`project_fleet_state` — новый main).

## Готчи (накопленные, применимые к новому main)

- **MTU 1400** в daemon.json — иначе зависают деплои/пуллы (урок 2026-09-01).
- **cloud.ru SG** по умолчанию открывает только 22 — не забыть 80/443.
- **Yandex Cloud ВМ** не достукиваются до GitHub — артефакты возить SFTP.
- **LE duplicated-cert лимит** — сначала staging.
- **docker cp не копирует live/ симлинки** (certbot) — только cat.
- **environment[] в docker-compose.prod.yml — явный список**: новые env-флаги бэкенда
  добавлять и туда, `.env` + force-recreate недостаточно.
- **reprovision-таймер** (30 мин) на новом main должен завестись сам (systemd) —
  проверить `systemctl list-timers | grep reprovision`; это индикатор health флота.
