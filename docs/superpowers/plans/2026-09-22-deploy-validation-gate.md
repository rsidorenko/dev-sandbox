# План: проверки production-деплоя

- Spec: `docs/superpowers/specs/2026-09-22-deploy-validation-gate-design.md`
- Ветка: `fix/deploy-validation-gate`
- Дата: 2026-09-22

1. Подтвердить текущую конфигурацию и отсутствие проверки в серверном скрипте.
2. Исправить зависимости, сериализацию и условия запуска `.gitlab-ci.yml`.
3. Добавить `scripts/ci/verify_health.sh` и исполняемые тесты отказа/восстановления.
4. Выполнить pytest, ruff, release health check, bash syntax и GitLab CI lint.
5. Review → MR → успешный CI → merge → проверить main pipeline и production HTTP.

Откат: отдельный revert через MR, только если новая проверка ошибочна.
При настоящей недоступности не подавлять ошибку; диагностировать причину.
