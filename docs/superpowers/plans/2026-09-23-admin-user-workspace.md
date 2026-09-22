# План рабочего места поддержки

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-user-workspace-design.md`
- **Ветка:** `feat/admin-user-workspace`

1. `backend/src/app/web_api/admin_user_read_model.py`: безопасный SQL списка с фасетами и диагностические DTO; подключить `admin_users.py`.
2. `frontend/src/entities/admin/types.ts`, `features/admin/api.ts`: расширить контракты без изменения мутаций.
3. `admin/users/page.tsx`: серверный поиск, фильтры, сортировка, пагинация, колонки, сохранение фильтра и URL.
4. `admin/users/[id]/page.tsx`: карточка, диагностика, история, дополнительные действия.
5. Сохранить query string при auth redirect, проверить PostgreSQL/API, browser smoke и существующие тесты.
6. Отдельный MR, ревью, CI, merge и deploy. Откат коммита без миграций.
