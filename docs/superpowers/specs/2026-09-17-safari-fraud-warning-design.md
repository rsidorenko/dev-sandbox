# Design Spec — Устранение ложного предупреждения Safari о мошенническом сайте

- **Автор / ответственный:** Codex
- **Дата:** 2026-09-17
- **Статус:** approved
- **Связанный plan:** `docs/superpowers/plans/2026-09-17-safari-fraud-warning.md`

## Контекст и проблема

Safari на iPhone показывает для `bravada-connect.ru` красный interstitial «Предупреждение
о мошенническом сайте». Это репутационная классификация браузера, а не ошибка TLS или React.
Она блокирует вход на сайт и напрямую снижает конверсию.

Продакшен-проверка показала валидный сертификат Let's Encrypt, HTTPS, строгий CSP, отсутствие
сторонних скриптов на главной и доступный файл подтверждения Google Search Console. При этом
метатег Google verification отсутствует в production HTML: `layout.tsx` читает переменную,
но Dockerfile и Compose не передают её в `next build`. Дополнительно `npm audit --omit=dev`
обнаружил критические/high advisory в устаревшем Next.js 14.2.35 и его зависимостях.

## Цели

- Восстановить передачу Google Search Console verification в production-сборку.
- Обновить Next.js до поддерживаемой ветки без известных production advisory.
- Свести публичный сайт к одному каноническому host и добавить стандартный security contact.
- Зафиксировать проверяемый runbook очистки и пересмотра Google/Apple классификации.
- После merge и deploy проверить production и подать запрос на пересмотр классификации.

## Non-goals

- Не обходить предупреждение Safari и не просить пользователей отключать защиту.
- Не утверждать, что кодовый deploy мгновенно удалит запись из внешней базы репутации.
- Не менять аутентификацию, биллинг или VPN-провайдер.

## Текущее состояние

- `frontend/src/app/layout.tsx` поддерживает `NEXT_PUBLIC_GOOGLE_SITE_VERIFICATION`.
- `.env.prod.example` документирует переменную.
- `frontend/Dockerfile` не объявляет и не экспортирует соответствующий build arg.
- `docker-compose.prod.yml` и `docker-compose.yml` не передают build arg.
- `nginx/conf.d/default.conf` обслуживает apex и `www` одинаково, несмотря на canonical apex.
- `frontend/public/google1a1baf7dbc7fea2d.html` доступен на продакшене.
- Публичного `/.well-known/security.txt` нет.

## Предлагаемое решение

Передать verification token через Compose → Docker build arg → Next metadata, не фиксируя
значение в репозитории. Обновить Next.js и совместимый ESLint config до безопасной версии,
оставив React 18 в поддерживаемом диапазоне. В nginx выделить `www` в отдельные redirect-only
виртуальные хосты и всегда отправлять его на `https://bravada-connect.ru`. Добавить
RFC 9116-compatible `security.txt` с публичным адресом поддержки.

Операторский runbook должен разделять техническую очистку и внешнюю реклассификацию:
проверка TLS/редиректов/HTML/зависимостей, deploy, подтверждение Security Issues в Search
Console, затем запросы Review в Google Safe Browsing и Apple Website Review. Запросы не
автоматизируются: формы могут требовать авторизацию и CAPTCHA.

## Альтернативы

- Только подать appeal: отклонено, потому что production-сборка теряет verification meta и
  содержит известные уязвимости.
- Только обновить зависимости: не решает владение доменом и пересмотр классификации.
- Отключить login/оплату: несоразмерно и не требуется без признаков компрометации.
- Сменить домен: потеря SEO/доверия и перенос проблемы вместо устранения причины.

## Влияние

- **Файлы:** Dockerfile/Compose, nginx, package manifests, public security.txt, тест и runbook.
- **Миграции БД:** нет.
- **Безопасность:** устраняются известные advisory; секрет verification не коммитится.
- **Биллинг:** не затрагивается.
- **Retention / lifecycle:** не затрагивается.
- **VPN-провайдер:** не затрагивается.

## Открытые вопросы

- Какая система репутации создала запись, окончательно подтверждается в Search Console и
  Apple Website Review после входа владельца; Safari может использовать отдельный список.
- Срок снятия предупреждения определяется Google/Apple и обычно не совпадает с моментом deploy.

## Критерии приёмки

- [x] Verification variable проходит до Next production build без раскрытия значения.
- [x] `www` перенаправляется на canonical HTTPS apex.
- [x] `/.well-known/security.txt` доступен и содержит Contact/Canonical/Expires.
- [x] `npm audit --omit=dev` не содержит high/critical production advisory.
- [x] Frontend unit/typecheck/build/E2E и инфраструктурные тесты зелёные.
- [ ] После deploy production-проверки подтверждают новую конфигурацию.
- [ ] Подготовлен и отправлен запрос на пересмотр либо зафиксирован конкретный внешний blocker.
