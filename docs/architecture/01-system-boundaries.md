## 01 — System boundaries & responsibility map

### Цель системы и scope MVP

**Цель**: Telegram-first подписочный сервис, который:
- принимает запросы пользователей через Telegram-бота,
- оформляет/подтверждает оплату через абстракцию биллинга,
- управляет жизненным циклом подписки,
- выдаёт и отзывает доступ (конфигурацию) через абстракцию “config issuance”,
- предоставляет минимальные инструменты админам/саппорту,
- обеспечивает наблюдаемость, аудит и безопасную обработку данных.

**MVP scope (входит)**:
- Telegram-бот как основной UX (команды, статусы, ссылки на оплату/управление).
- Backend/control plane: бизнес-логика, оркестрация, политики доступа.
- База данных для состояния пользователей/подписок/операций/аудита.
- Billing abstraction: приём событий оплаты, сверка статусов, reconciliation.
- Subscription lifecycle: единая модель состояний и переходов, идемпотентность.
- Config issuance abstraction: выпуск/ротация/отзыв доступа (как абстракция).
- Admin/support tools: минимальный набор админ-операций с ограничением доступа.
- Observability: структурированные логи, метрики, трассировка (минимально).
- Security baseline: секреты, валидация входов, контроль доступа, аудит, анти-спам.

**MVP scope (не входит)**:
- Полноценный web-портал, мобильные приложения, публичный админ UI.
- Глубокая аналитика/BI, маркетинг-автоматизация.
- Мульти-tenant/white-label.
- Сложные промо-механики (рефералки, купоны со сложными правилами) — только если понадобятся позднее.
- Хардкод конкретного провайдера платежей или конкретного провайдера конфигов (только через абстракции).
- Глубокие схемы развертывания (Docker/CI/infra) и миграции БД — вне этого документа.

---

### System boundaries (что в системе / что вне системы)

#### В системе (System-of-Interest)
- **Telegram bot layer** (наша часть): приём/отправка сообщений, обработка команд, проксирование интентов в backend.
- **Backend/control plane**: авторизация действий, бизнес-правила, обработчики событий, API для бота и админов.
- **Database**: источник истины для пользователей/подписок/операций/аудита.
- **Billing abstraction**: слой интеграции и нормализации событий биллинга.
- **Subscription lifecycle**: доменная модель подписки и её переходов.
- **Config issuance abstraction**: слой выдачи/отзыва конфигураций доступа.
- **Admin/support tools**: контролируемые админ-операции.
- **Observability**: логи/метрики/трейсы (минимально достаточно для эксплуатации).
- **Security baseline**: кросс-секционные требования безопасности.

#### Вне системы (External)
- **Telegram platform** (сеть/апдейты/доставка).
- **Payment provider(s)** (платёжные операции, уведомления).
- **Service provider for access/config** (например, инфраструктура доступа), если она внешняя по отношению к control plane.
- **Secret store / environment config** (механизм предоставления секретов окружением).
- **Email/SMS/push** (если появится) — вне MVP по умолчанию.

---

### Внешние акторы и внешние системы

#### External actors
- **End user**: пользователь Telegram, покупает подписку, получает доступ, управляет статусом.
- **Admin/Support operator**: выполняет ограниченные операции (проверка статуса, ручная блокировка, возврат/компенсация по политике, ресинхронизация).
- **Billing provider operator/system**: внешний источник событий об оплатах (webhook), может присылать повторы.
- **Attacker/abuser**: спам/флуд, попытки обойти оплату, кража конфигов, эксплуатация уязвимостей.

#### External systems
- **Telegram Bot API/Webhooks**
- **Billing provider webhooks / API**
- **Config/access provider API** (через abstraction)
- **Logging/metrics backend** (может быть внешним)

---

### Подсистемы: responsibility map

> Формат для каждой подсистемы: Responsibility, Owned data, Inbound interfaces, Outbound interfaces, Trust boundary.

#### 1) Telegram bot layer

- **Responsibility**
  - UX в Telegram: команды, кнопки, меню, ответы.
  - Нормализация входов (команды/callback), первичная валидация формата.
  - Отправка интентов в backend/control plane.
  - Anti-spam на уровне входных апдейтов (минимально) и graceful degradation.

- **Owned data**
  - Эфемерное состояние диалогов (если нужно) и кэш контекста (опционально).
  - Не является источником истины для подписок/доступа.

- **Inbound interfaces**
  - Telegram updates (webhook/polling) от Telegram platform.
  - Привилегированный admin ingress для MVP не через этот слой (`Telegram admin chat` — вне MVP, отложен).

- **Outbound interfaces**
  - Вызовы в backend/control plane (внутренний API).
  - Отправка сообщений в Telegram platform.

- **Trust boundary**
  - Вход из публичного интернета (Telegram) → недоверенный.
  - Доверие к user identity строится только на проверенных Telegram полях + проверке целостности webhook (см. Security baseline).

- **Where required**
  - **Strict input validation**: команды, аргументы, callback data (размер, формат, allowlist).
  - **Rate limiting / anti-spam**: per-user, per-chat, per-IP (если применимо).
  - **PII minimization**: не логировать raw тексты сообщений по умолчанию.

---

#### 2) Backend / control plane

- **Responsibility**
  - Единая точка бизнес-правил: авторизация действий, политики подписок, выдача доступа.
  - Обработка событий из bot layer и billing abstraction.
  - Идемпотентные обработчики для событий/команд.
  - API для admin/support tools.
  - Координация subscription lifecycle и config issuance abstraction.

- **Owned data**
  - Доменная логика и “source-of-truth” состояние (через database).
  - Ключи идемпотентности/журналы операций.
  - Политики: планы/тарифы (как конфигурация), правила доступа.

- **Inbound interfaces**
  - Internal API от Telegram bot layer (user intents).
  - Webhook/events от billing abstraction (нормализованные).
  - Admin/support API (ограниченный доступ).
  - (Опционально) scheduled jobs (reconciliation, renewals checks) — как внутренняя функция.

- **Outbound interfaces**
  - Database CRUD (транзакционно).
  - Config issuance abstraction API calls.
  - Billing abstraction API calls (например, для сверки/рефанда).
  - Observability sinks (logs/metrics/traces).

- **Trust boundary**
  - Принимает данные из недоверенных источников (пользовательский ввод, webhooks) → обязателен слой валидации/аутентификации.
  - Внутренние вызовы к DB и issuance/billing адаптерам — доверенные, но требуют минимизации прав.

- **Where required**
  - **Idempotency**: все обработчики внешних событий (billing webhooks, Telegram updates, admin actions).
  - **RBAC / admin allowlist**: доступ к admin/support интерфейсам.
  - **Strict input validation**: все входные payloads, включая webhook schemas.
  - **Secret management**: токены, ключи интеграций, signing secrets.
  - **Auditability**: изменения состояния подписки/доступа и админ-операции, меняющие состояние, фиксируются в audit log; тонкое MVP правило для privileged read-only (`ADM-01` vs `ADM-02`) — в `11-admin-support-and-audit-boundary.md`.
  - **Rate limiting**: на user-intent endpoint и на admin endpoints.

---

#### 3) Database

- **Responsibility**
  - Хранение состояния и истории: пользователи, подписки, платежные события, выдача конфигов, аудит.
  - Транзакционная целостность: согласованные переходы subscription lifecycle.
  - Уникальные ограничения для идемпотентности (например, внешние event_id).

- **Owned data**
  - User record (минимальный идентификатор Telegram и внутренняя ссылка).
  - Subscription record (state, plan, период, timestamps).
  - Billing event ledger (нормализованные события, external ids).
  - Issuance records (какой доступ выдан, статус, ревокации).
  - Audit log (кто/что/когда/почему).

- **Inbound interfaces**
  - Только backend/control plane (никаких прямых внешних подключений).

- **Outbound interfaces**
  - Нет (кроме репликации/backup как операционной функции вне MVP описания).

- **Trust boundary**
  - Высокодоверенная зона. Доступ строго ограничен по сети и credentials.
  - Минимальные права (least privilege) на уровне ролей приложения.

- **Where required**
  - **Idempotency**: уникальные ключи на external event IDs + таблица idempotency keys.
  - **Auditability**: неизменяемые записи аудита (append-only где возможно).
  - **PII minimization**: хранить минимум; избегать избыточных полей и свободного текста.

---

#### 4) Billing abstraction

- **Responsibility**
  - Абстрагировать конкретного провайдера: нормализовать события оплат/подписок в единый контракт.
  - Проверка подлинности webhook (подпись/секрет) и базовая schema validation.
  - Защита от повторов: дедупликация по event id (в связке с DB и idempotency).
  - (Опционально) reconciliation: периодическая сверка статусов у провайдера.

- **Owned data**
  - Маппинг external identifiers → internal (минимально).
  - Конфигурация интеграции (не секреты; секреты — через env/config).

- **Inbound interfaces**
  - Webhooks от billing provider (публичный интернет).
  - Backend control plane calls (создать checkout/link, запросить статус, запросить refund — если нужно).

- **Outbound interfaces**
  - Billing provider API.
  - Backend/control plane (нормализованные события).

- **Trust boundary**
  - Граница с внешним биллингом: недоверенные входы до проверки подписи/таймстампа/анти-replay.

- **Where required**
  - **Idempotency**: webhook processing и любые “create payment/checkout” операции.
  - **Strict input validation**: строгая схема событий, reject unknown fields where practical.
  - **Secret management**: webhook secret, API keys.
  - **Auditability**: логировать только event ids и статус, без PII/платёжных PAN/секретов.

---

#### 5) Subscription lifecycle

- **Responsibility**
  - Единая модель состояний подписки и переходов (state machine на уровне домена).
  - Правила: когда выдавать доступ, когда продлевать, когда отзывать, как обрабатывать grace period.
  - Согласование событий: Telegram intents + billing events → корректный итоговый state.

- **Owned data**
  - Правила и доменные инварианты (в коде backend; данные — в DB как state).
  - Справочник тарифов/планов как конфигурация (не секрет).

- **Inbound interfaces**
  - Нормализованные billing events.
  - User intents (например, “проверить статус”, “обновить доступ”).

- **Outbound interfaces**
  - Команды в config issuance abstraction (issue/rotate/revoke).
  - Уведомления пользователю через bot layer (через backend).

- **Trust boundary**
  - Работает внутри доверенной зоны backend, но входные события считаются недоверенными до валидации и идемпотентности.

- **Where required**
  - **Idempotency**: переходы состояний, выдача/отзыв доступа.
  - **Auditability**: каждое изменение state + причина (event link) в audit log.
  - **Strict validation**: запрет нелегальных переходов.

---

#### 6) Admin / support tools

- **Responsibility**
  - Минимальные операции поддержки:
    - просмотр статуса пользователя/подписки,
    - ручная блокировка/разблокировка по политике,
    - запуск reconciliation для конкретного пользователя/события,
    - повторная выдача/отзыв доступа (controlled).
  - Безопасный доступ, минимальная поверхность.

- **Owned data**
  - Конфиг allowlist/RBAC ролей админов.
  - Шаблоны “reason codes” для аудита (как конфигурация).

- **Inbound interfaces**
  - Для MVP: только `internal admin endpoint`; `Telegram admin chat` — отложен (post-MVP); см. `29-mvp-admin-ingress-boundary-note.md`.
  - Requests от операторов (trusted identities).

- **Outbound interfaces**
  - Backend/control plane (админ-команды).
  - Audit log.

- **Trust boundary**
  - Вход ограниченный, но всё равно недоверенный по умолчанию: обязательна аутентификация и авторизация.

- **Where required**
  - **RBAC / admin allowlist**: обязательна (минимум allowlist на Telegram user_id / admin identities).
  - **Idempotency**: админ-команды, которые меняют состояние.
  - **Strict validation**: parameters, reason code allowlist.
  - **Auditability**: кто сделал, что сделал, с каким основанием.

---

#### 7) Config issuance abstraction

- **Responsibility**
  - Единый контракт “выдать/обновить/отозвать доступ” независимо от провайдера конфигурации.
  - Управление жизненным циклом артефакта доступа: версия, ротация, ревокация.
  - Минимизировать утечки: выдавать пользователю только то, что нужно для подключения/доступа.

- **Owned data**
  - Маппинг internal user/subscription → issued artifact reference (не секретный материал по возможности).
  - Статус issuance операций и их idempotency keys.

- **Inbound interfaces**
  - Backend/control plane (commands: issue/rotate/revoke/status).

- **Outbound interfaces**
  - Внешний config/access provider API (если внешний) или внутренний модуль.
  - Database (для фиксации результата).

- **Trust boundary**
  - При обращении к внешнему провайдеру — граница доверия; ответы нужно валидировать и обрабатывать ошибки/ретраи.

- **Where required**
  - **Idempotency**: issue/rotate/revoke (исключить двойную выдачу).
  - **Secret management**: ключи доступа к provider API.
  - **Auditability**: фиксировать issuance/revocation события.
  - **PII minimization**: не логировать выданные конфиги/токены; логировать только reference ids.

---

#### 8) Observability

- **Responsibility**
  - Диагностика и эксплуатация: метрики ключевых потоков, структурированные логи, корреляция запросов.
  - Алерты по базовым SLO: падение webhook, рост ошибок issuance/billing, лаг обработки.

- **Owned data**
  - Технические события: request ids, correlation ids, error classes.
  - Политика редактирования/маскирования логов.

- **Inbound interfaces**
  - События/логи/метрики из bot layer, backend, billing, issuance.

- **Outbound interfaces**
  - Sink для логов/метрик/трейсов (абстрактно; провайдер не фиксируем).

- **Trust boundary**
  - Observability backend может быть внешним; поэтому данные должны быть минимизированы и очищены от секретов/PII.

- **Where required**
  - **PII minimization**: маскирование идентификаторов, запрет raw payload logging.
  - **Auditability support**: корреляция audit records с request/event ids (без раскрытия секретов).

---

#### 9) Security baseline (cross-cutting)

- **Responsibility**
  - Минимальный безопасный набор практик:
    - аутентификация источников webhook,
    - строгая валидация входных данных,
    - idempotency и защита от повторов,
    - RBAC/allowlist для админов,
    - secret management,
    - аудит,
    - rate limiting/anti-spam,
    - минимизация PII в логах,
    - безопасная обработка ошибок.

- **Owned data**
  - Политики безопасности (конфигурация), список админов/ролей (в конфиге или DB, но не в коде).
  - Ключи/секреты — только через env/config (не хранить в репозитории).

- **Inbound interfaces**
  - Все внешние входы: Telegram updates, billing webhooks, admin requests.

- **Outbound interfaces**
  - Логирование (без секретов), audit trail, enforcement hooks в backend/bot/billing.

- **Trust boundary**
  - Чётко отделяет public edge (webhooks) от internal zone (backend/DB).

- **Explicit requirements checklist (must be present in implementation later)**
  - **Idempotency**:
    - billing webhooks processing
    - Telegram updates processing (как минимум для “state-changing” команд)
    - issuance operations (issue/rotate/revoke)
    - admin operations
  - **RBAC / admin allowlist**:
    - любые admin/support действия
  - **Strict input validation**:
    - Telegram command args + callback payload
    - billing webhook schema + signature + timestamp constraints
  - **Secret management via env/config**:
    - bot token
    - billing API keys + webhook secrets
    - issuance provider credentials
    - signing/encryption keys (если появятся)
  - **Auditability**:
    - subscription state changes
    - issuance changes
    - state-changing admin actions; privileged read-only (`ADM-01` vs `ADM-02`) — `11-admin-support-and-audit-boundary.md`
    - billing events ingestion (ledger)
  - **PII minimization in logs**:
    - запрет логирования message text по умолчанию
    - маскирование идентификаторов/корреляция через internal ids
  - **Rate limiting / anti-spam**:
    - per-user/per-chat для bot layer
    - per-endpoint для backend webhooks/admin endpoints
    - защита от burst и повторов

---

### Ключевые архитектурные принципы (smallest safe implementation)

1. **Single source of truth**: состояние подписки и доступа живёт в DB; бот — только интерфейс.
2. **Idempotency by default**: любой внешний вход, который может повториться, обрабатывается идемпотентно.
3. **Least privilege everywhere**: минимальные права для DB, issuance и billing интеграций.
4. **Fail closed for entitlement**: при неопределённости статуса оплаты/подписки — не выдаём доступ, а предлагаем проверить/повторить.
5. **Explicit trust boundaries**: webhooks и пользовательский ввод — всегда недоверенные.
6. **Strict validation over permissiveness**: reject/ignore неизвестные или некорректные поля и команды.
7. **Audit-first state changes**: каждое изменение подписки/доступа/админ-действие имеет причину и связанный event id.
8. **PII minimalism**: хранить и логировать минимум PII; использовать внутренние идентификаторы.
9. **Abstractions for external dependencies**: billing и issuance — через контракты, без привязки к провайдеру.
10. **Operational visibility**: метрики и корреляция событий обязательны в MVP, но без “тяжёлых” платформенных усложнений.

---

### Open questions / unknowns (не блокируют следующий шаг)

- Какой именно формат “config issuance artifact” нужен (ссылка/токен/файл/профиль) и какие минимальные свойства безопасности у артефакта (TTL, одноразовость, привязка к user)?
- Нужны ли trial/grace period в MVP, и как их отражать в состоянии подписки?
- Нужна ли поддержка нескольких планов/тарифов с разными правами доступа или один план в MVP?
- Требования к возвратам/chargeback и их влияние на доступ (немедленный отзыв vs grace)?
- MVP admin ingress choice already fixed separately: chosen = `internal admin endpoint`, deferred = `Telegram admin chat`; source of truth for this narrow decision is `29-mvp-admin-ingress-boundary-note.md`.
- Какой минимум observability нужен для on-call (какие 3–5 метрик и какие алерты)?
- Какие данные (если какие-то) нужно экспортировать для бухгалтерии/отчётности — и в каком объёме без PII?

---

### Definition of Done: этап “system boundaries fixed”

- Документ **01-system-boundaries** принят и служит единым источником истины для границ системы.
- Перечень подсистем и их ответственности согласован: нет “дыр” (unowned responsibility) и нет дублирования источника истины.
- Явно зафиксированы:
  - что **в системе**, что **вне системы**,
  - внешние акторы/системы,
  - trust boundaries по подсистемам.
- Явно перечислены места, где **обязательны**:
  - idempotency,
  - RBAC/allowlist,
  - строгая валидация,
  - secret management,
  - auditability,
  - минимизация PII в логах,
  - rate limiting/anti-spam.
- Открытые вопросы перечислены и не блокируют следующий следующий архитектурный шаг (контракты/модель домена).
