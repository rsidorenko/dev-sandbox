# Plan — Упрощённая тарифная витрина и checkout

- **Дата:** 2026-09-16
- **Spec:** `docs/superpowers/specs/2026-09-16-simplified-checkout-design.md`
- **Ветка:** `feat/simplified-checkout`

## Фаза 1 — Тарифная витрина

### Задача 1.1 — Сократить первичный выбор
- **Файлы:** `frontend/src/shared/config/site.ts`,
  `frontend/src/widgets/landing/TariffsSection.tsx`, тесты.
- **Что сделать:** рекомендовать `3m`, показывать три основных срока, убрать остальные и custom за
  единое раскрытие.
- **Acceptance:** все планы доступны, но первичный экран содержит три решения.

## Фаза 2 — Checkout

### Задача 2.1 — Свернуть необязательные настройки
- **Файлы:** `frontend/src/app/(site)/(protected)/payment/[planId]/page.tsx`, тесты.
- **Что сделать:** свернуть расширение устройств, промокод и альтернативную оплату; переименовать
  основной способ в «Карта или СБП».
- **Acceptance:** стандартная покупка требует только одного решения и одного нажатия CTA.

### Задача 2.2 — Сохранить deep-link конфигурацию
- **Файлы:** checkout и тесты.
- **Что сделать:** раскрывать устройства при `?devices=N`, не менять payment API contract.
- **Acceptance:** прямой URL восстанавливает выбранное количество устройств.

## Фаза 3 — Проверка

- [x] Frontend unit tests — 151 passed.
- [x] Frontend lint и typecheck проходят.
- [x] Frontend build проходит.
- [x] Playwright `landing.spec.ts` и `checkout.spec.ts` — 10 passed.
- [x] `git diff --check` проходит.
