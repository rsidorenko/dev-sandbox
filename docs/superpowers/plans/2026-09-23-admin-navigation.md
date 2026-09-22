# План навигации админки

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-navigation-design.md`
- **Ветка:** `feat/admin-navigation`

1. Выделить `frontend/src/features/admin/AdminNavigation.tsx`: общие пункты, активность вложенных маршрутов, доступный drawer.
2. Подключить в `frontend/src/app/admin/layout.tsx`, сохранить gate и тему.
3. Проверить адаптивность и клавиатуру тестами компонента, выполнить frontend test/lint/build, backend pytest и health check.
4. Отдельный MR, ревью, зелёный CI, merge и проверка деплоя.

## Риски и откат
Ошибки мобильного фокуса проверяются отдельно. Откат коммита возвращает старую навигацию; данных и миграций нет.
