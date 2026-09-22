# План разделов аналитики

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-analytics-sections-design.md`
- **Ветка:** `feat/admin-analytics-sections`

1. Валидатор URL `features/admin/analyticsLocation.ts` и тесты дат/секции.
2. Контейнер `admin/stats/page.tsx`: URL-state, race guard, freshness, четыре раздела.
3. `QualitySection.tsx`: подписи для удержания и VPN без потери компонентов.
4. Browserfixtures всех графиков/драфта/навигации, полный regression/build.
5. MR→ревью→CI→merge→deploy; откат UI без миграций и изменения метрик.
