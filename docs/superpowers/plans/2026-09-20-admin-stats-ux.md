# Plan — Понятная и мобильная админ-статистика

- **Дата:** 2026-09-20
- **Spec:** `docs/superpowers/specs/2026-09-20-admin-stats-ux-design.md`
- **Ветка:** `feat/admin-stats-ux`

## Сводка

В трёх фазах добавляем runtime-статусы экспериментов, систему доступных подсказок и
адаптивные представления для наиболее сложных блоков статистики.

## Фаза 1 — Наблюдаемость экспериментов

### Задача 1.1 — Вернуть runtime-каталог из API
- **Файлы:** `backend/src/app/web_api/admin_stats.py`, backend tests
- **Что сделать:** добавить безопасный список ключей, статусов, вариантов и целей без env-значений.
- **Acceptance:** ответ различает активный тест и исторические экспозиции; unit-тест покрывает флаги.

### Задача 1.2 — Типизировать контракт
- **Файлы:** `frontend/src/entities/admin/types.ts`
- **Что сделать:** добавить тип runtime-статуса в `AdminStatsResponse`.
- **Acceptance:** TypeScript принимает API без `any`.

## Фаза 2 — Пояснения метрик

### Задача 2.1 — Создать доступную иконку подсказки
- **Файлы:** `frontend/src/features/admin/MetricHelp.tsx`, тест
- **Что сделать:** hover/focus tooltip на desktop и раскрытие по tap; aria-label и читаемый текст.
- **Acceptance:** компонент доступен клавиатурой и тестируется через Testing Library.

### Задача 2.2 — Подключить подсказки к dashboard
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`, `QualitySection.tsx`
- **Что сделать:** пояснить KPI и основные секции, включая знаменатели и диагностические сигналы.
- **Acceptance:** нет главных аналитических блоков без контекстной подсказки.

## Фаза 3 — Мобильный адаптив

### Задача 3.1 — Перестроить controls и KPI
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`
- **Что сделать:** горизонтальный touch-scroll пресетов, полноширинные мобильные поля, одна колонка KPI на узких экранах, компактные графики.
- **Acceptance:** интерфейс не сжимает элементы на ширине 320–390 px.

### Задача 3.2 — Добавить мобильные карточки сложных таблиц
- **Файлы:** `frontend/src/app/admin/stats/page.tsx`, `QualitySection.tsx`
- **Что сделать:** карточки кампаний и экспериментов на mobile, desktop-таблицы сохранить; остальные таблицы снабдить scroll affordance.
- **Acceptance:** основные решения по рекламе и тестам доступны без широкой прокрутки.

## Стратегия тестирования

- Backend unit: каталог runtime-флагов.
- Frontend unit: MetricHelp и QualitySection.
- Полный pytest; frontend unit, lint, `tsc --noEmit`, production build.
- Визуальная проверка desktop/mobile через browser после локального build при доступности.

## Риски и откат

- Риск: tooltip перекрывает контент → мобильное раскрытие и ограниченная ширина.
- Риск: карточки скроют редкие поля → desktop-таблица сохраняется, mobile показывает ключевые метрики.
- Откат: один UI/API MR, без миграций и изменений расчётов.

## Чек-лист перед PR

- [ ] `cd backend && .venv/bin/python -m pytest -q` — зелёный
- [ ] release health проходит
- [ ] frontend tests, lint, typecheck и build зелёные
- [ ] Коммит без `Co-Authored-By`
- [ ] Spec и plan соответствуют реализации
