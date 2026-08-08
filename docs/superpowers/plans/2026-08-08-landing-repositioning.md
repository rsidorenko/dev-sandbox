# Plan — Лендинг: репозиционирование + конверсия (слайс B1)

- **Дата:** 2026-08-08
- **Spec:** [`docs/superpowers/specs/2026-08-08-landing-repositioning-design.md`](../specs/2026-08-08-landing-repositioning-design.md)
- **Ветка:** `feat/landing-repositioning`

## Сводка

Pure-frontend рефактор лендинга: репозиционирование, главный CTA «3 дня бесплатно», устранение
рискованных формулировок и дублирования, фикс числа устройств, trust-bar, 4 новых FAQ. 5 фаз.

## Фаза 1 — Конфиг (`shared/config/site.ts`)

### Задача 1.1 — Константы устройств + trust
- **Файлы:** `frontend/src/shared/config/site.ts`
- **Что сделать:** добавить `devices: { base: 5, min: 1, max: 20 }`; данные для trust-bar можно
  собрать прямо в виджете (trialDays/refundDays уже есть).
- **Acceptance:** `siteConfig.devices` существует; тип `Tariff` не сломан.

### Задача 1.2 — 4 новых FAQ
- **Файлы:** `frontend/src/shared/config/site.ts`, `frontend/src/shared/config/site.test.ts`
- **Что сделать:** добавить пункты: «Можно ли попробовать бесплатно?» / «Что будет после пробного?» /
  «Несколько устройств одновременно?» / «Есть ли автопродление?». Поправить FAQ про устройства —
  число брать из `devices`. Обновить `site.test.ts` (если он фиксирует кол-во/состав FAQ).
- **Acceptance:** FAQ содержит 10 пунктов; тест зелёный.

## Фаза 2 — Hero

### Задача 2.1 — Репозиционирование + главный CTA
- **Файлы:** `frontend/src/widgets/landing/HeroSection.tsx`
- **Что сделать:** badge «Защищённое соединение»; H1 «Защищённое соединение без сложных настроек»;
  subtext: «Подключитесь за пару минут и используйте защищённое соединение на телефоне, компьютере и
  других устройствах. iOS · Android · Windows · macOS · Linux.»; **первый/крупный CTA — `🎁 {trialDays}
  дня бесплатно` → `/trial`** (`reachGoal("trial_click")`); вторичный «Выбрать тариф» → `#tariffs`
  (`reachGoal("choose_plan")`); убрать «Как это работает»-кнопку (лишняя); микрокопия доверия оставить.
- **Acceptance:** нет «Свободный интернет», «без ограничений», «YouTube/Instagram»; главный CTA — trial.

## Фаза 3 — Features

### Задача 3.1 — Убрать рискованные формулировки + 3 карточки
- **Файлы:** `frontend/src/widgets/landing/FeaturesSection.tsx`
- **Что сделать:** карточка «Шифрование трафика» → описание «Трафик передаётся по зашифрованному
  соединению, что снижает риск перехвата данных при передаче.»; убрать карточку «Доступ к сервисам без
  границ», заменить на «Поддержка без ожидания» («Что-то не получается — напишите в Telegram, поможем
  подключиться.»); «Все устройства» → число из `siteConfig.devices` («Подключайте до N устройств…»,
  базовое число). Сетка `lg:grid-cols-3`.
- **Acceptance:** нет «перехват невозможен», «без границ»; 3 карточки; устройство из конфига.

## Фаза 4 — Дедупликация + CTA + TrustBar

### Задача 4.1 — Удалить дубль «как работает»
- **Файлы:** удалить `frontend/src/widgets/landing/DeliverySection.tsx`; `frontend/src/app/(site)/page.tsx`
- **Что сделать:** удалить импорт/использование `DeliverySection` из композитора. Остаётся `HowItWorks`.
- **Acceptance:** один блок «как работает»; сборка без orphan-imports.

### Задача 4.2 — Финальный CTA на trial
- **Файлы:** `frontend/src/widgets/landing/CtaSection.tsx`
- **Что сделать:** H2 «Попробуйте Bravada бесплатно»; primary «Попробовать бесплатно» → `/trial`
  (`reachGoal("trial_click")`); secondary «Выбрать тариф» → `#tariffs`.
- **Acceptance:** главный CTA — trial.

### Задача 4.3 — TrustBar
- **Файлы:** новый `frontend/src/widgets/landing/TrustBar.tsx`; `frontend/src/app/(site)/page.tsx`
- **Что сделать:** узкая полоса под hero: «🎁 {trialDays} дня бесплатно · ↩ Возврат {refundDays} дней
  · 💳 Карта + СБП · 💬 Поддержка в Telegram». Подключить в композиторе сразу после `HeroSection`.
- **Acceptance:** trust-bar рендерится; значения из `siteConfig`.

## Фаза 5 — Валидация

### Задача 5.1 — Тесты/линт/сборка/grep
- **Команды:** `cd frontend && npm test && npm run lint && npm run build`;
  `grep -rn "перехват невозможен\|без ограничений\|Свободный интернет\|YouTube\|Instagram" frontend/src/widgets frontend/src/app` → пусто.
- **Acceptance:** всё зелёное; grep пуст.

## Стратегия тестирования

- `shared/config/site.test.ts` — обновить под новый FAQ/`devices`.
- `npm run build` ловит orphan-imports/типы после удаления `DeliverySection`.
- Визуальная проверка `npm run dev` (hero/features/trust/cta).

## Риски и откат

- Риск: forgot убрать импорт `DeliverySection` → `next build` упадёт → правлю по ошибке сборки.
- Откат: чисто frontend, без миграций; revert MR.

## Чек-лист перед PR

- [ ] `cd frontend && npm test` — зелёный
- [ ] `npm run lint` — чисто
- [ ] `npm run build` — собирается
- [ ] grep рискованных фраз пуст
- [ ] Коммиты маленькие, сообщения на русском, без `Co-Authored-By`
