# Plan — Устранение ложного предупреждения Safari

- **Дата:** 2026-09-17
- **Spec:** `docs/superpowers/specs/2026-09-17-safari-fraud-warning-design.md`
- **Ветка:** `fix/safari-fraud-warning`

## Сводка

Исправляем потерю Search Console verification, закрываем production advisory, усиливаем
канонизацию домена и оформляем воспроизводимый процесс пересмотра внешней классификации.

## Фаза 1 — Production trust chain

### Задача 1.1 — Передать verification token в Next build
- **Файлы:** `frontend/Dockerfile`, `docker-compose.yml`, `docker-compose.prod.yml`.
- **Что сделать:** добавить build arg и ENV без значения по умолчанию в коде.
- **Acceptance:** статический тест подтверждает непрерывную цепочку Compose → Dockerfile.

### Задача 1.2 — Канонизировать host и добавить security contact
- **Файлы:** `nginx/conf.d/default.conf`, `frontend/public/.well-known/security.txt`.
- **Что сделать:** redirect `www` на HTTPS apex; опубликовать стандартный security.txt.
- **Acceptance:** nginx config однозначно обслуживает apex, public-файл валиден.

## Фаза 2 — Устранение известных advisory

### Задача 2.1 — Обновить Next.js
- **Файлы:** `frontend/package.json`, `frontend/package-lock.json`.
- **Что сделать:** перейти на исправленную поддерживаемую версию Next.js и совместимый config.
- **Acceptance:** production audit без high/critical; unit/typecheck/build/E2E зелёные.

## Фаза 3 — Проверка и пересмотр классификации

### Задача 3.1 — Добавить регрессионный тест и runbook
- **Файлы:** `backend/tests/test_public_site_security_config.py`,
  `backend/docs/safe_browsing_incident_runbook.md`.
- **Что сделать:** закрепить конфигурационные инварианты и последовательность incident response.
- **Acceptance:** тест ловит потерю build arg, canonical redirect и security.txt.

### Задача 3.2 — Deploy и appeal
- **Файлы:** внешнее состояние GitLab/production/Google/Apple.
- **Что сделать:** после зелёного MR выполнить production deploy, проверить URL и подать review.
- **Acceptance:** production соответствует проверкам; внешний запрос подтверждён или явно blocked.

## Стратегия тестирования

- Backend/static: инфраструктурные инварианты и полный pytest/ruff.
- Frontend: lint, typecheck, unit, production build и Playwright E2E.
- Security: `npm audit --omit=dev --audit-level=high`.
- Production: TLS, HTTP→HTTPS, www→apex, verification meta, security.txt и CSP.

## Риски и откат

- Next.js major update может изменить runtime → полный build/E2E до push; откат package manifests.
- Ошибка nginx может нарушить доступность → `nginx -t` в контейнере до deploy и HTTP probes после.
- Внешняя классификация снимается асинхронно → фиксируем номер/подтверждение review, не маскируем статус.

## Чек-лист перед PR

- [x] Backend pytest, ruff check и ruff format зелёные.
- [x] Frontend lint/typecheck/unit/build/E2E зелёные.
- [x] Production npm audit без high/critical.
- [x] Spec и plan соответствуют реализации.
- [x] Git-facing prose на русском, без `Co-Authored-By`.
