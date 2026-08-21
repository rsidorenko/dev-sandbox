# SSL / Let's Encrypt auto-renewal runbook

## Purpose
TLS для `bravada-connect.ru` обслуживается полностью автоматически: продление и
применение сертификата не требуют ручных операций ни разу в 90-дневном цикле.

## Background: инцидент 2026-08-20 (почему архитектура такая)

20.08.2026 в 21:43 UTC сертификат истёк — сайт и бот (вебхук Telegram) лежали ~26 часов.

Корень — split-brain: certbot-контейнер исправно продлевал сертификат в named-томе
`certs:/etc/letsencrypt` (продлил 21.07), но nginx читал bind-mount
`./ssl:/etc/nginx/ssl` с копией от 22.05, и ничто не синкало том в `./ssl`.
Предыдущий план (host-certbot + setup_letsencrypt_autorenew.sh, ранбук от 04.07
«Fix before 2026-08-20») требовал ручной установки на сервере — его так и не
выполнили. Урок: механизм, требующий ручного шага на проде, не работает.

## Текущая архитектура (fix/tls-cert-split-brain, 2026-08-21)

Всё внутри docker-compose.prod.yml, переживает любой деплой без оператора:

1. **certbot-сервис** (`certbot renew` каждые 12ч) продлевает сертификат в
   named-томе `certs`. HTTP-01 валидация — через webroot `certbot-www`
   (nginx обслуживает `/.well-known/acme-challenge/`).
2. **nginx читает сертификат напрямую из того же тома**:
   `ssl_certificate /etc/letsencrypt/live/bravada-connect.ru/fullchain.pem;`
   (том смонтирован в nginx read-only).
3. **nginx-контейнер делает graceful reload каждые 6ч** (`command` с
   reload-лупом — канонический паттерн certbot↔nginx). Максимальный лаг
   применения продлённого сертификата — 6 часов.

Bind-mount `./ssl` больше НЕ используется nginx. Каталог `/opt/bravada/ssl`
сохраняется деплоем как аварийная копия (если named-том `certs` когда-нибудь
потеряют, сертификат можно восстановить вручную в том: fullchain.pem+privkey.pem).

## CA policy (prevent the next revocation wave)

- **Use DV CAs only**: Let's Encrypt, ZeroSSL, Buypass. No organization screening → not
  subject to OFAC/EU sanctions revocation.
- **Avoid OV/EV** from sanction-bound CAs (GlobalSign/GMO, DigiCert, Sectigo, Entrust,
  Certum) — they must screen the owning organization and can revoke RU legal entities, as
  GlobalSign did on 2026-06-13.
- **Do not** put the National UC of MinTsifry / "Russian Trusted CA" on a public
  (client-facing) domain — Apple does not trust them, which reintroduces the iOS
  "This Connection Is Not Private" error.
- OCSP stapling убран из конфига: Let's Encrypt прекратил OCSP для новых
  сертификатов (директивы стали no-op и сыпали warning на каждый reload).

## Verification

```bash
# 1. Сертификат в томе (должен быть notAfter ~+90d)
docker exec bravada-certbot-1 openssl x509 \
  -in /etc/letsencrypt/live/bravada-connect.ru/fullchain.pem -noout -dates

# 2. Что реально отдаёт nginx (совпадает с п.1 = том применяется)
echo Q | openssl s_client -connect bravada-connect.ru:443 -servername bravada-connect.ru \
  | openssl x509 -noout -issuer -dates

# 3. Лупы живы
docker logs bravada-certbot-1 --tail 5      # «Certificate not yet due for renewal» / renew
docker exec bravada-nginx-1 ps              # виден фоновый sh с reload-лупом
```

## Risks / notes

- **Свежий сервер с пустым томом `certs`**: nginx не стартует (нет файла
  сертификата) → chicken-and-egg для первичного выпуска. Bootstrap: запустить
  certbot-сервис с `certonly --webroot` ДО первого старта nginx (или временно
  закомментировать 443-блок). На текущем проде том уже живёт и переживает деплои.
- Если добавить второй домен — renew-конфиг certbot в томе должен существовать
  для него же (`/etc/letsencrypt/live/<domain>/`), иначе nginx не стартует.
- Критично: НЕ возвращать nginx к bind-mount `./ssl` — это и есть баг инцидента.
