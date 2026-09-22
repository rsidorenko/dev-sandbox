# План: сверка ключей Xray

Спецификация: [дизайн](../specs/2026-09-22-xray-identity-reconcile-design.md).

1. В `backend/tests/test_reconcile_bare_xray.py` воспроизвести старый UUID при том же email; получить падение на текущем коде.
2. В `backend/src/app/issuance/xray_api_client.py` добавить строгую выдачу email → (UUID, flow), покрыть ошибки RPC/JSON/protobuf и дополнительные поля.
3. В `backend/src/app/issuance/xui_vless_provider.py` исправлять расхождения, учитывать ошибки операций. Проверить повторный проход после неудачного добавления и отсутствие изменений при совпадении.
4. Запустить целевые и полные тесты, ruff, release health check; независимый review; MR, зелёный CI, merge и проверка deploy/health.

Откат: отдельный revert MR. Ошибки чтения не должны вызывать удаления. Проверка производства — только чтение до merge, без ротации пользовательских ключей.
