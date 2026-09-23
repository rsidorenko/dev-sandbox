# План: короткий путь от промокода рассылки до оплаты

Spec: `docs/superpowers/specs/2026-09-23-broadcast-promo-checkout-design.md`
Ветка: `fix/broadcast-promo-checkout`.

1. `storefront_ui.buy_vpn_keyboard`: опциональный режим прямой оплаты, явные действия,
   обычные callback по умолчанию неизменны.
2. `runtime_facade`: общий рендер тарифов после claim, текущие устройства и канонические
   цены; повторный claim с re-validation. Все gates и платёжный обработчик сохраняются.
3. `test_broadcast_promo_claim`: успех/повтор/отказы, устройства, цены, pipeline до
   фиктивного провайдера. Регресс обычной витрины и тесты скидок/платежей.
4. Полный pytest, Ruff и release health; независимый review; MR, CI, merge/deploy.
5. После deploy read-only проверки приложения и VPN. Откат — revert MR,
   миграций и новых секретов нет. Реальные рассылки и платежи в проверках не создаются.

Локальная проверка: 2604 passed, 163 skipped (PostgreSQL без DATABASE_URL), 20 прежних предупреждений;
Ruff check/format и release health прошли. Независимый review: APPROVE.
