# Plan — зрелое окно notification holdout

- **Дата:** 2026-09-20
- **Spec:** `docs/superpowers/specs/2026-09-20-notification-holdout-window-design.md`
- **Ветка:** `fix/notification-holdout-window`

## Сводка

Исправить SQL-окно holdout, расширить API/UI зрелостью и закрепить пограничные случаи PostgreSQL-тестом.

## Фаза 1 — backend

### Задача 1.1 — использовать sent_at и зрелый знаменатель
- **Файлы:** `backend/src/app/web_api/admin_stats.py`.
- **Что сделать:** first exposure, точное 7-дневное окно, mature/pending counts.
- **Acceptance:** pre-send и immature платежи не входят в paid rate.

### Задача 1.2 — расширить тест
- **Файлы:** `backend/tests/test_notification_holdout.py`.
- **Что сделать:** зрелые delivered/holdout, pre-send payment и свежая экспозиция.
- **Acceptance:** знаменатели и оплаты совпадают с ожидаемыми.

## Фаза 2 — админка

### Задача 2.1 — показать зрелые и ожидающие наблюдения
- **Файлы:** admin types и `QualitySection`.
- **Что сделать:** поля total/pending и подписи зрелые/всего.
- **Acceptance:** оператор не принимает свежие данные за финальный CR.

## Стратегия тестирования

- PostgreSQL integration для временных границ.
- Frontend component test для mature/total.
- Полные backend/frontend проверки.

## Риски и откат

- Риск: CR визуально изменится после исключения незрелых данных → UI показывает оба объёма.
- Откат: один кодовый commit, миграций нет.

## Чек-лист перед PR

- [ ] Полный pytest зелёный
- [ ] PostgreSQL test зелёный
- [ ] Frontend lint/typecheck/unit/build зелёные
- [ ] Release health check проходит
