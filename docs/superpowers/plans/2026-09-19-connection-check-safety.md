# Plan — достоверная и защищённая проверка VPN

- **Дата:** 2026-09-19
- **Spec:** `docs/superpowers/specs/2026-09-19-connection-check-safety-design.md`
- **Ветка:** `fix/connection-check-safety`

## Сводка

Добавить явный egress/check-host, fail-unknown семантику, tokenized speed endpoint и полные показатели качества в админке.

## Фаза 1 — схема и детект

### Задача 1.1 — добавить connection check host
- **Файлы:** `backend/migrations/096_vpn_servers_connection_check_host.sql`
- **Что сделать:** добавить поле и безопасно заполнить только прямые xui/tcp-строки.
- **Acceptance:** повторное выполнение безопасно; relay/CDN не получают ложный egress.

### Задача 1.2 — сделать DNS uncertainty явной
- **Файлы:** `backend/src/app/application/connection_check.py`, `backend/src/app/web_api/connection_check.py`
- **Что сделать:** сохранять незаполненные/неразрешённые серверы и возвращать unknown при неполной карте.
- **Acceptance:** unknown не записывается как `connected=false`.

## Фаза 2 — endpoint и админка

### Задача 2.1 — защитить speed endpoint
- **Файлы:** backend route/handler и frontend connection-check API/card.
- **Что сделать:** добавить token в URL, entitlement check и общий immutable payload.
- **Acceptance:** старый публичный URL не отдаёт payload; expired/unknown token отклоняется.

### Задача 2.2 — расширить качество серверов
- **Файлы:** backend admin stats, frontend types и `QualitySection`.
- **Что сделать:** учитывать успешные и неуспешные проверки, показывать CR подключения и unknown bucket.
- **Acceptance:** сумма серверных bucket покрывает все checks периода.

## Стратегия тестирования

- Unit/ASGI: DNS/config uncertainty, отсутствие записи, tokenized/cached speed payload.
- PostgreSQL: migration и admin quality aggregate.
- Frontend: API URL, карточка и таблица качества.
- Полные backend/frontend проверки и release health check.

## Риски и откат

- Риск: для ненастроенных relay/CDN статус станет unknown → безопаснее ложного отрицания; оператор может заполнить `connection_check_host`.
- Риск: старый frontend вызывает публичный URL → endpoint возвращает 404; backend и frontend разворачиваются одним релизом.
- Откат: кодовый commit и additive-колонка; колонку удалять не требуется.

## Чек-лист перед PR

- [ ] Полный backend pytest зелёный
- [ ] PostgreSQL migration/admin tests зелёные
- [ ] Frontend lint/typecheck/unit/build зелёные
- [ ] Release health check проходит
- [ ] Git prose на русском, без co-author trailers
