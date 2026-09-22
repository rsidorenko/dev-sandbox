# План мониторинга серверов

- **Spec:** `docs/superpowers/specs/2026-09-23-admin-server-monitor-design.md`
- **Ветка:** `feat/admin-server-monitor`

1. Добавить миграцию101 и запись результатов StatsService в `traffic_stats_scheduler.py` с изоляцией ошибок.
2. `web_api/admin_servers.py`: безопасный GET и честная модель fresh/error/stale/unknown; зарегистрировать маршрут.
3. `admin/servers/page.tsx`, DTO/API/навигация: группировка хост/подключения, трафик и свежесть.
4. Проверить реальную PostgreSQL, unit failure isolation, frontend/e2e, полный backend и build.
5. MR, ревью, CI, merge, deploy; проверить свежие наблюдения и неизменность VPN. Откат кода не требует удаления аддитивной таблицы.
