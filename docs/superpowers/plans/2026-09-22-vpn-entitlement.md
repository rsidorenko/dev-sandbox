# План: право на VPN-доступ

- Spec: `docs/superpowers/specs/2026-09-22-vpn-entitlement-design.md`
- Ветка: `fix/vpn-entitlement`

1. Регрессионные тесты реальных handlers и провайдера, подтвердить красный результат.
2. Общая политика entitlement и точная дата create_user.
3. Применить политику к GET keys и перевыпуску в Web/Telegram; сохранить кабинет.
4. Проверки, независимое review, MR, CI, merge и проверка production-деплоя.

Откат через MR, миграций нет. Ротацию ключей не смешивать с проверкой права.
