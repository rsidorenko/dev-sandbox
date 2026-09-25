# План — неизменяемое начало онбординга

- Spec: `docs/superpowers/specs/2026-09-25-trial-onboarding-anchor-design.md`
- Ветка: `fix/trial-onboarding-anchor`

1. Миграция105: nullable поле и безопасный backfill только без первого трафика.
2. Snapshot upsert и collector: сохранять время выдачи, не трогать сроки триала.
3. admin_stats и notification_scheduler: разделить нижнюю границу событий и часы триала.
   Качество данных: отдельный счётчик неизвестного начала.
4. Реальный PostgreSQL regression и полный набор backend; frontend typecheck/tests.
5. Review, отдельный MR, CI, merge, smoke; проверить новую колонку read-only в production.

Риск: старые записи после подключения восстановить нельзя. Явная неизвестность
и консервативный fallback; никаких предположений по первой регистрации пользователя.
