# План обзора сервиса

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-service-overview-design.md`
- **Ветка:** `feat/admin-service-overview`

1. `web_api/admin_overview.py`: согласованные агрегаты и повторное использование attention/server snapshots.
2. `admin/page.tsx`, DTO/navigation: обзор, переходы и поиск.
3. PostgreSQL, frontendunit/build, browser smoke, независимое ревью.
4. MR→CI→merge→deploy. После доставки всех MR проверить API/health/ключи и VPN, очистить ветки, итоговый отчёт. Откат только кода.
