# План — основа аналитики воронки

- Дата: 2026-09-11
- Spec: `docs/superpowers/specs/2026-09-11-funnel-foundation-design.md`
- Ветка: `feat/funnel-foundation`

## Задачи

- [x] Миграция 081: внутренний флаг, аудит, серверное представление событий,
  взаимодействия. Acceptance: повторное применение безопасно, цены не затронуты.
- [x] `application/funnel_events.py`, `bot_transport/runtime_facade.py`:
  ограниченный best-effort сбор. Acceptance: выключенный флаг и ошибки БД не
  меняют ответы пользователя; ключи и тексты не попадают в события.
- [x] `web_api/admin_users.py`, `web_api/app.py`: admin API флага.
  Acceptance: auth + CSRF, строгий boolean, причина, аудит и изменение атомарны.
- [x] `web_api/admin_stats.py`: исключение internal, зрелая когорта.
  `frontend/src/entities/admin/types.ts`, `frontend/src/app/admin/stats/page.tsx`:
  показать числитель/знаменатель и окно 10 суток, не смешивать с legacy rate.
- [x] Тесты PostgreSQL и unit; runbook `backend/docs/funnel_foundation.md`.
- [x] Полный pytest, ruff, health-check, frontend typecheck, review diff.
- [ ] Коммит на русском, push, GitLab MR, проверка CI без частого опроса.

## Безопасность

Цены и платёжные транзакции неизменны. События выключены по умолчанию; для включения
нужен отдельный rollout. Внешние аккаунты не помечаем internal без точного списка.
Миграции проверяем на отдельной тестовой БД, production не трогаем.
