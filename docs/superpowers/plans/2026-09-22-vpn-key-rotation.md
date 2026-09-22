# План: ротация ключей

- Spec: `docs/superpowers/specs/2026-09-22-vpn-key-rotation-design.md`
- Ветка: `fix/vpn-key-rotation`
- Зависимость: MR проверки entitlement.

1. Миграция 099 и единый алгоритм legacy/seed с регрессионными тестами.
2. Сериализация мутаций и устойчивая pending-ротация в провайдере.
3. Web/Telegram используют reissue_user; Stub поддерживает реальную смену ключей.
4. Reconcile, cache и операторские утилиты используют актуальное поколение.
5. Проверки отказов/рестарта/конкурентности на локальном PostgreSQL, полный pytest,
   ruff, release health check, независимое review → MR → CI → merge → deploy.
