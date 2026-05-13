## 02 — Repository structure & module boundaries (single-service)

### Цель структуры репозитория для MVP

Эта структура нужна, чтобы:
- реализовать **single-service** backend/control plane как один deployable артефакт, но с **жёсткими логическими границами** модулей;
- сделать безопасность “по умолчанию” через обязательные примитивы (idempotency, validation, RBAC, secrets access, audit, rate limiting);
- изолировать **domain** от инфраструктуры (Telegram, billing provider, issuance provider, observability backend);
- обеспечить тестируемость: доменная и application логика тестируются без внешних интеграций;
- минимизировать риск “случайного” смешения ответственности и утечек PII/секретов.

---

### Почему single repository + single deployable backend/control plane

Выбор для MVP: **один репозиторий + один deployable** (backend/control plane), потому что:
- проще и безопаснее управлять изменениями доменной логики: меньше межсервисных контрактов и сетевых отказов;
- проще enforce security baseline: единый слой валидации, идемпотентности, аудита, error handling;
- меньше операционной сложности (наблюдаемость, секреты, релизы) — критично для ранней стадии;
- сохраняется расширяемость: когда появятся устойчивые границы и нагрузка/организация, можно выделять компоненты, но сейчас логические модули достаточно.

**Важно**: single-service не означает “монолит без правил”; наоборот, правила зависимостей и каталоги ниже задают enforceable boundaries.

---

### Top-level structure (дерево директорий, без кода)

Пример минимального дерева:

repo/
  docs/
    architecture/
      01-system-boundaries.md
      02-repository-structure.md
  backend/
    README.md
    src/
      bot_transport/
      application/
      domain/
      billing/
      subscription/
      issuance/
      admin_support/
      observability/
      security/
      persistence/
      shared/
    tests/
      unit/
      integration/
      contract/
  scripts/
    README.md
  ops/
    README.md

Примечания:
- `backend/` — единственный deployable (single-service).
- `docs/` — архитектурные решения и “границы”.
- `scripts/` и `ops/` — операционные вспомогательные артефакты (без привязки к Docker/CI в рамках этого шага).

---

### Какие директории обязательны в MVP, а какие out-of-scope

#### Обязательные в MVP
- `docs/architecture/` — зафиксированные границы и правила.
- `backend/src/` — модули backend/control plane.
- `backend/tests/` — минимум unit-тестов для domain/application и интеграционных тестов для ключевых адаптеров.
- `ops/` — только базовые операционные заметки (как запускать/какие переменные окружения нужны) без конкретики по инфраструктуре.
- `scripts/` — утилиты для локальных проверок/админ-задач (без раскрытия секретов).

#### Явно out-of-scope пока (не добавлять в MVP структуру)
- `frontend/`, `mobile/`, публичный “admin portal UI”.
- отдельные сервисы/папки `billing-service/`, `bot-service/` и т.п.
- сложные генераторы SDK/клиентов, отдельные репозитории контрактов.
- схемы БД, миграции, конкретные ORM/DB manifests (появятся позже отдельным шагом).

---

### Логические модули внутри backend/control plane

Ниже — модули и границы. Названия каталогов в `backend/src/` должны соответствовать модулю.

#### 1) `bot_transport/` (bot interface / transport)
- Responsibility:
  - принять Telegram updates (webhook/polling — как transport choice позже),
  - привести вход к внутренним “командам/интентам” (transport DTO),
  - применить базовые anti-spam/rate limit на входе,
  - вызвать application layer.
- Depends on:
  - `application/`
  - `security/` (validation primitives, rate limiting primitives, safe error handling)
  - `observability/` (structured logging facade + PII redaction)
  - `shared/` (correlation ids, time, base types)
- Must not depend on:
  - `persistence/` напрямую
  - `billing/`, `issuance/` напрямую
  - `domain/` напрямую (через application commands/DTO; доменные типы — только через application boundary)
- Notes on testability:
  - контрактные тесты на “transport → application command” без Telegram SDK;
  - фокус на validation/rate limit и safe error mapping.

#### 2) `application/` (application layer)
- Responsibility:
  - orchestration: use-cases (например: “get status”, “start checkout”, “refresh access”, “admin block user”),
  - транзакционные границы (в смысле unit-of-work, без выбора технологии),
  - вызовы domain policy + persistence + adapters.
- Depends on:
  - `domain/`
  - `persistence/` (через interfaces/contracts, см. ниже)
  - `billing/` (через contracts)
  - `issuance/` (через contracts)
  - `security/` (RBAC, idempotency, audit hooks, validation)
  - `observability/`
  - `shared/`
- Must not depend on:
  - конкретные transport детали (`bot_transport/`), кроме общих DTO/commands на границе
- Notes on testability:
  - unit-тесты use-cases с in-memory fakes для persistence/billing/issuance;
  - проверка идемпотентности, аудита, RBAC на уровне use-case.

#### 3) `domain/`
- Responsibility:
  - доменные правила и инварианты (без IO): subscription lifecycle rules, entitlement decisions,
  - доменные ошибки/результаты (не transport-specific).
- Depends on:
  - `shared/` (time, ids, result/error base types)
- Must not depend on:
  - `bot_transport/`, `billing/` adapters, `issuance/` adapters
  - `observability/` (никаких логов в домене)
  - `security/` (кроме простых value-objects/validation результатов, но лучше через `shared/`)
  - `persistence/` (никаких репозиториев/SQL и т.п.)
- Notes on testability:
  - чистые unit-тесты без моков внешних систем;
  - property-based тесты допустимы позже, но не обязательны.

#### 4) `billing/` (billing abstraction)
- Responsibility:
  - contracts + adapters к billing provider’ам,
  - нормализация billing events (внутренний формат),
  - верификация webhook authenticity (через security primitives),
  - дедупликация событий (через idempotency primitives + persistence).
- Depends on:
  - `security/` (signature verification, idempotency primitives, validation)
  - `application/` (или наоборот? правило ниже: billing adapters вызываются application, а не наоборот)
  - `shared/`, `observability/`
  - `persistence/` (через interfaces, для event ledger/idempotency)
- Must not depend on:
  - `bot_transport/`
  - `domain/` напрямую (billing — внешний контекст; доменные решения принимает subscription module/application)
- Notes on testability:
  - contract tests на “provider payload → normalized event” (без провайдера);
  - интеграционные тесты адаптера с sandbox/фикстурами позже.

#### 5) `subscription/` (subscription lifecycle)
- Responsibility:
  - application-facing orchestration вокруг domain subscription rules,
  - обработка переходов состояний как use-case/сервис (без деталей хранения),
  - решения “выдать/отозвать доступ” как команды к issuance.
- Depends on:
  - `domain/` (правила и инварианты)
  - `application/` (может быть частью application; выделение как модуль помогает удерживать границы)
  - `persistence/` (через interfaces)
  - `security/` (idempotency + audit)
  - `observability/`, `shared/`
- Must not depend on:
  - `bot_transport/` напрямую
  - concrete billing provider adapter (только normalized events/contracts)
- Notes on testability:
  - unit-тесты state transitions через domain + fake persistence;
  - сценарии повторов (идемпотентность) обязательны.

#### 6) `issuance/` (config issuance abstraction)
- Responsibility:
  - contracts + adapters выдачи/ротации/отзыва доступа,
  - гарантии: идемпотентность операций, безопасная обработка ошибок, минимизация утечек (не логировать артефакт).
- Depends on:
  - `security/` (secrets access, idempotency)
  - `observability/` (redaction)
  - `shared/`, `persistence/` (через interfaces)
- Must not depend on:
  - `bot_transport/`
  - `domain/` напрямую (domain решает “нужно ли”, issuance делает “как”)
- Notes on testability:
  - unit-тесты контрактов + adapter fakes;
  - “no secret/PII in logs” тесты на логирование.

#### 7) `admin_support/` (admin/support)
- Responsibility:
  - admin use-cases (разблок/блок, ресинхронизация, просмотр статуса),
  - enforcement RBAC/allowlist и обязательный audit.
- Depends on:
  - `application/`
  - `security/` (RBAC/allowlist, audit)
  - `persistence/` (через interfaces)
  - `observability/`, `shared/`
- Must not depend on:
  - `bot_transport/` напрямую (если админ через Telegram — это транспортный слой вызывает admin_support use-case)
- Notes on testability:
  - unit-тесты на RBAC/allowlist и audit emission;
  - негативные тесты на запрещённые операции.

#### 8) `observability/`
- Responsibility:
  - единые фасады для логов/метрик/трейсов,
  - redaction/masking правил,
  - correlation ids propagation policy.
- Depends on:
  - `shared/`
- Must not depend on:
  - `domain/` (домен не должен импортировать observability)
- Notes on testability:
  - тесты редактирования (PII/secret redaction),
  - тесты стабильности формата лог-событий (contract-ish).

#### 9) `security/` (shared security/config primitives)
- Responsibility:
  - RBAC/allowlist primitives,
  - idempotency primitives (keys, dedupe helpers, retry classification),
  - strict validation primitives (schema/constraints; технология позже),
  - secrets access abstraction (env/config boundary),
  - safe error handling policy (error taxonomy, mapping to user-safe responses),
  - rate limiting / anti-spam primitives.
- Depends on:
  - `shared/`
  - (опционально) `observability/` только через интерфейс, чтобы не зациклить зависимости
- Must not depend on:
  - `bot_transport/`, `billing/`, `issuance/` конкретика
  - `persistence/` конкретика (но может определять интерфейсы, которые persistence реализует)
- Notes on testability:
  - unit-тесты на ключевые политики: allowlist, idempotency key generation, validation outcomes, error redaction.

#### 10) `persistence/`
- Responsibility:
  - interfaces для репозиториев/единиц работы (contracts),
  - реализации хранения (конкретная технология позже),
  - хранение idempotency records, audit log append, event ledger.
- Depends on:
  - `shared/`
  - `security/` (для audit/idempotency interfaces, но избегать циклов через contracts в `shared/`/`security/`)
- Must not depend on:
  - `bot_transport/`, `billing/` adapters, `issuance/` adapters
  - `domain/` конкретика (допустимо использовать доменные типы только через маппинг на границе persistence, но лучше держать DTO/records в persistence и маппинг в application)
- Notes on testability:
  - unit-тесты на репозиторные контракты (in-memory implementation),
  - интеграционные тесты реализации хранения — позже.

---

### Allowed dependency direction rules (слои/модули)

Правило “стрелки” (допустимые зависимости):

- `domain` → `shared`
- `application` → `domain`, `security`, `observability`, `shared`, (contracts в `persistence`, `billing`, `issuance`)
- `bot_transport` → `application`, `security`, `observability`, `shared`
- `billing` adapters → `security`, `observability`, `shared`, `persistence` (через interfaces)
- `issuance` adapters → `security`, `observability`, `shared`, `persistence` (через interfaces)
- `admin_support` → `application`, `security`, `observability`, `shared`
- `persistence` implementations → `shared` (+ contracts из `security` если нужно)

Ключевое: **внешние интеграции = adapters**, и они не должны “тащить” domain внутрь себя.

---

### Forbidden coupling rules (запрещённые сцепления)

- `domain` не импортирует:
  - observability/logging,
  - secrets/config,
  - persistence,
  - transport,
  - внешние SDK.
- `bot_transport` не делает:
  - прямых записей в БД,
  - прямых вызовов billing/issuance,
  - доменных решений (только application use-cases).
- `billing` и `issuance` adapters:
  - не меняют subscription state напрямую (только через application/subscription use-cases),
  - не логируют чувствительные payloads.
- “Shared utils” не должен содержать:
  - бизнес-правила,
  - доступ к секретам,
  - логирование сырых входов.
- Любой код, который читает секреты, должен жить в **одном месте**: `security/` (через abstraction), а не быть разбросанным по модулям.

---

### Где должны жить ключевые вещи

- **Contracts / interfaces**
  - Внутренние contracts между модулями (billing, issuance, persistence) должны жить рядом с модулем, но быть “чистыми”:
    - `backend/src/billing/contracts/`
    - `backend/src/issuance/contracts/`
    - `backend/src/persistence/contracts/`
  - Общие типы (ids, result/error base types) — `backend/src/shared/`.

- **Adapters / integrations**
  - Конкретные интеграции — в соответствующем модуле:
    - `backend/src/billing/adapters/`
    - `backend/src/issuance/adapters/`
    - `backend/src/bot_transport/adapters/` (если нужно)

- **Config loading**
  - Только в `backend/src/security/config/` (как abstraction: parse/validate/normalize), чтобы enforce “single config boundary”.

- **Secrets access**
  - Только в `backend/src/security/secrets/` (env/config boundary + redaction policy).
  - Остальные модули получают секреты через интерфейсы/объекты конфигурации, не читая env напрямую.

- **Audit logic**
  - Audit primitives и event schema — `backend/src/security/audit/`.
  - Запись audit (append) — через persistence contract (`persistence/contracts`) и implementation (`persistence/`).

- **Idempotency primitives**
  - Генерация/проверка ключей, политика повторов — `backend/src/security/idempotency/`.
  - Хранение ключей — `persistence/` (ledger таблицы/records описываются позднее, здесь только место).

- **Validation primitives**
  - Общие валидаторы (строгая схема входов, ограничения размеров, allowlists) — `backend/src/security/validation/`.
  - Transport-specific validation glue — в `bot_transport/` и `billing/` (но с использованием общих primitives).

- **Rate limiting / anti-spam primitives**
  - Политики, ключи (per-user/per-chat/per-source), классификация действий — `backend/src/security/rate_limit/`.
  - Вызов enforcement:
    - в `bot_transport/` (на вход Telegram updates),
    - в `application/` (на критические use-cases),
    - на webhook обработчиках billing (через `billing/` + `security/`).

---

### Как структура помогает enforce ключевые требования безопасности

- **Idempotency**
  - primitives централизованы в `security/idempotency/`,
  - persistence для дедупликации — в `persistence/`,
  - use-cases в `application/` обязаны использовать эти primitives (не “по месту”).

- **RBAC / admin allowlist**
  - только `security/rbac/` (или `security/admin_access/`) содержит правила и интерфейс проверки;
  - `admin_support/` не может выполнять state-changing операции без зависимости на `security/`.

- **Strict input validation**
  - общий набор валидаторов в `security/validation/`;
  - `bot_transport/` и `billing/` не имеют права “пропускать” сырой payload внутрь application без нормализации и validation outcome.

- **Secret management через env/config**
  - чтение env/secret store инкапсулировано в `security/secrets/` и `security/config/`;
  - redaction на уровне observability не позволяет утекать секретам.

- **Auditability**
  - audit primitives в `security/audit/`;
  - application/use-cases обязаны писать audit для state changes;
  - persistence содержит единый путь записи.

- **Минимизация PII в логах**
  - `observability/` содержит redaction policy;
  - запрет логировать raw inbound payloads закреплён как rule (см. forbidden coupling).

- **Dependency pinning**
  - технология не выбрана, но структура должна предусматривать единое место для “lock/pin” (будет добавлено позже как manifest/lockfile на корне `backend/`).
  - принцип: зависимости фиксируются на уровне `backend/` как одного deployable, а не по модулям.

- **Safe error handling**
  - классификация ошибок и политика “user-safe vs internal” — в `security/errors/` (или `security/error_handling/`);
  - transport слои (`bot_transport`, billing webhook handlers) используют mapping из security, чтобы не возвращать/логировать чувствительные детали.

---

### Правила именования модулей и файлов (чтобы не смешивать domain и infrastructure)

- Модули называются по ответственности, **не по технологии**: `billing`, `issuance`, `bot_transport`, а не `stripe`, `wireguard`, `telegram_sdk`.
- Внутри модулей:
  - `contracts/` — только интерфейсы и типы без IO;
  - `adapters/` — конкретные интеграции (инфраструктура);
  - `service/` или `use_cases/` — orchestration (application-level), если нужно.
- Domain:
  - имена отражают бизнес-смысл (например, `subscription/` внутри `domain/`), но без деталей хранения/протоколов.
- Запрещённый паттерн: `shared/utils/` с бизнес-логикой.
- Файлы, содержащие внешние протоколы/payloads, должны быть явно помечены местом: transport/billing adapters, а не domain.

---

### Минимальный набор документов (уже есть) и какие будут следующими

Уже есть (архитектура):
- `docs/architecture/01-system-boundaries.md` — границы системы и responsibility map на уровне подсистем.
- `docs/architecture/02-repository-structure.md` — структура репозитория и module boundaries (этот документ).

Следующие логичные документы после этого шага (не создаются сейчас):
- `03-domain-and-use-cases.md` — перечень MVP use-cases и boundary между application и domain (без детального моделинга сущностей).
- `04-security-controls.md` — конкретизация security baseline: idempotency policy, audit policy, log redaction rules, admin access model.
- `05-integration-contracts.md` — high-level contracts для billing/issuance/telegram transport (без детальных payloads).

---

### Definition of Done: этап “repository structure fixed”

- Структура директорий репозитория зафиксирована и соответствует single-service подходу.
- Для каждого модуля определены:
  - responsibility,
  - allowed dependencies,
  - forbidden dependencies,
  - заметки по тестируемости.
- Приняты правила dependency direction и forbidden coupling.
- Определены “единые точки” для:
  - config loading,
  - secrets access,
  - audit,
  - idempotency,
  - validation,
  - rate limiting/anti-spam,
  - safe error handling,
  - observability redaction.
- Нет требования выбрать язык/фреймворк/БД/ORM: структура остаётся технологически нейтральной.
