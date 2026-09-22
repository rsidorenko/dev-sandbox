# План: проверка исхода VPN-выдачи

- Spec: `docs/superpowers/specs/2026-09-22-balance-provider-result-design.md`
- Ветка: `fix/balance-provider-result`

1. Воспроизвести отказ результата без исключения через настоящий провайдер и обе ветви lifecycle.
2. Проверять outcome в `_ensure_vless_keys_atomic`, сохранив существующие повторы.
3. Проверить временное восстановление, компенсацию и отсутствие сброса lifecycle при отказе.
4. Полные локальные проверки, review, MR, CI, merge и проверка деплоя.

Откат: отдельный revert через MR. Схема БД не меняется.
