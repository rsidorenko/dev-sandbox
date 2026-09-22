# План инцидентов и аудита

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-incidents-design.md`
- **Ветка:** `feat/admin-incidents`

1. Миграция102: инциденты и события; обновить inventory migrations test.
2. `admin_attention.py`: общие сигналы для инцидентов/будущего обзора, толькоread.
3. `admin_incidents.py`: GETснимок и строгие POSTcreate/update с auth/CSRF/version/history.
4. `admin/incidents/page.tsx`: сигналы, lifecycle, комментарии, история и аудит; навигация.
5. PostgreSQL и browser проверки, fullsuite/build/ruff, ревью→MR→CI→merge→deploy.

Откат кода сохраняет новые таблицы и аудит; изменения исходных доменных данных отсутствуют.
