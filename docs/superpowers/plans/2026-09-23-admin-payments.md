# План разбора платежей

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-payments-design.md`
- **Ветка:** `feat/admin-payments`

1. `web_api/admin_payments.py`: UNION read model, attention reasons, bounds, snapshot pagination.
2. API route/types, `admin/payments/page.tsx`, navigation: фильтры в URL, состояния трёх этапов.
3. PostgreSQL integration и browser smoke, полный regression/build, ревью.
4. Отдельный MR→CI→merge→deploy. Откат кода без миграций и влияния на оплату.
