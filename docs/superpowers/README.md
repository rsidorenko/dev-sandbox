# Spec-Driven Development (superpowers)

Этот каталог — единый источник правды о том, как команда **TelegramBotVPN / Bravada VPN**
ведёт разработку по паттерну **SDD (Spec-Driven Development)** с использованием фреймворка
**superpowers** (вместо OpenSpec). Документ предназначен в первую очередь для людей и моделей
(включая GLM-5.2), которые пишут код в этом репозитории: открывая нетривиальную задачу,
следуй описанному здесь циклу.

---

## TL;DR

1. Любая нетривиальная фича начинается с **spec** (design-документ) →
   `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`.
2. После согласования spec превращается в **plan** (декомпозиция на задачи) →
   `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`.
3. Реализация идёт **строго по задачам плана**, каждая задача оставляет код в рабочем состоянии.
4. SDD **дополняет**, а не заменяет Trunk-Based цикл `/deliver` (см. [интеграцию ниже](#интеграция-с-deliver)).

Шаблоны: [`_templates/spec.md`](./_templates/spec.md), [`_templates/plan.md`](./_templates/plan.md).
Живой эталон: [`specs/2026-08-08-referral-stats-design.md`](./specs/2026-08-08-referral-stats-design.md).

---

## Почему SDD и почему superpowers

- **SDD (Spec-Driven Development)** — подход «сначала спецификация, потом код». Цель: устранить
  расхождение между тем, что задумано, и тем, что написано (code↔spec drift), декомпозировать
  задачу до реализации и иметь проверяемые критерии приёмки.
- **superpowers** ([github.com/obra/superpowers](https://github.com/obra/superpowers), автор Jesse Vincent / obra)
  — композабельный набор skills для Claude Code, реализующий SDD-цикл как набор markdown-скиллов:
  `brainstorming` → `writing-plans` → `executing-plans` / `subagent-driven-development` →
  `finishing-a-development-branch`. Он выбран **вместо OpenSpec**, потому что skills — это
  текстовые инструкции, которые харнес грузит всегда; они не требуют отдельного CLI и работают
  на любой модели, а не только на «родной» для OpenSpec.

> OpenSpec в этом репозитории **никогда не использовался** — это внедрение SDD «с нуля», а не
> миграция. Готового пути миграции openspec→superpowers не существует, и он здесь не нужен.

---

## Установка (опционально)

Ядро SDD (гайд, шаблоны, локальный скилл `.claude/skills/sdd/`) **уже в репозитории** и работает
без установки чего-либо — оно видимо любой модели в сессии, включая GLM-5.2.

Полный набор ~14 skills superpowers ставится по желанию, для расширенных сценариев
(subagent-driven dev, визуальный brainstorming и т.п.):

```
/plugin marketplace add obra/superpowers
/plugin
```

Затем выбрать и установить плагин `superpowers`. Скиллы появятся как доступные навыки.
Пути артефактов (ниже) совпадают с конвенцией superpowers v5.

---

## Жизненный цикл SDD

```
brainstorm  →  spec  →  review  →  plan  →  tasks  →  implement  →  finish
```

| Этап | Что происходит | Артефакт |
|---|---|---|
| **brainstorm** | Уточнить задачу, ограничения, альтернативы (сократовский диалог с пользователем). Читать реальный код и ADR — не полагаться на память. | (черновик в чате) |
| **spec** | Зафиксировать дизайн: контекст, цели, non-goals, текущее состояние, решение, влияние. | `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` |
| **review** | Проверить spec на полноту/противоречия (самопроверка или subagent-reviewer), согласовать с пользователем. | правки spec'а |
| **plan** | Декомпозировать spec на фазы и задачи, каждая задача — с file-refs и acceptance-критерием. | `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` |
| **tasks / implement** | Реализовывать по одной задаче из плана; после каждой — код компилируется/тесты зелёные. | код + коммиты |
| **finish** | Проверить тесты, прогнать preflight/health-check, открыть MR в GitLab. | MR |

### Конвенция именования файлов

- Дата в формате ISO в начале: `YYYY-MM-DD`.
- Slug в **kebab-case на английском** (транслитерация/короткое имя), без кирилллицы в имени файла.
- Проза **на русском**.
- Spec: `YYYY-MM-DD-<topic>-design.md`.
- Plan: `YYYY-MM-DD-<feature>.md` (тот же `<...>`, что и у spec'а, чтобы связать пару).

Пример: `specs/2026-08-08-referral-stats-design.md` + `plans/2026-08-08-referral-stats.md`.

---

## Форматы документов

- **Spec** — см. шаблон [`_templates/spec.md`](./_templates/spec.md). Разделы: Контекст и проблема ·
  Цели · Non-goals · Текущее состояние (со ссылками на код/ADR) · Предлагаемое решение ·
  Альтернативы · Влияние (файлы, миграции БД, безопасность, биллинг, retention) · Открытые вопросы ·
  Критерии приёмки.
- **Plan** — см. шаблон [`_templates/plan.md`](./_templates/plan.md). Разделы: Ссылка на spec ·
  Фазы · Задачи (file-refs + acceptance) · Стратегия тестирования · Риски/откат · Чек-лист перед PR.

---

## Интеграция с `/deliver`

SDD не отменяет Trunk-Based Development, а добавляет **фазу проектирования перед кодом**.
Одна фича = один spec + один plan + одна короткая ветка (`<type>/<scope>`) + один MR.

| Этап SDD | Куда в цикле `/deliver` |
|---|---|
| brainstorm → spec | **До ветки** или на шагах Branch/Read: понять задачу, оформить design-spec, согласовать |
| review + plan | Там же, перед кодом: план с задачами и acceptance |
| implement (задачи плана) | Шаг **Implement** `/deliver` — код строго по задачам плана |
| finish | Шаги **Validate → Commit → Push → MR → Merge** `/deliver` |

Правила веток/коммитов/CI из `CLAUDE.md` и `/deliver` остаются главными; SDD лишь фиксирует
«что и зачем» делается перед тем, как «как» делать.

---

## Специфика модели GLM-5.2

Команда работает в Claude Code-харнесе с моделью **GLM-5.2** (не Claude). Учитывай:

- **Ядро SDD модельно-независимо.** Написание spec → plan → реализация задач работает на любой
  модели, потому что весь цикл — это markdown-артефакты, которые грузит харнес. Этим и стоит
  пользоваться: веди spec/plan вручную по шаблонам.
- **Тяжёлые multi-subagent-фичи superpowers** (subagent-driven-development с проверкой
  spec-compliance после каждой задачи, визуальный brainstorming) оптимизированы под поведение
  Claude. На GLM-5.2 они могут работать менее надёжно. Используй их по возможности, но **не
  полагайся критически** — при сомнениях возвращайся к ручному ведению по шаблонам.
- **Всегда читай реальный код** перед тем, как писать spec (правило `CLAUDE.md`: CodeGraph первым,
  затем файлы). Не доверяй памяти/контексту для расположения символов.

---

## Шпаргалка

- Новый spec: скопируй `_templates/spec.md` → `specs/<дата>-<topic>-design.md`.
- Новый plan: скопируй `_templates/plan.md` → `plans/<дата>-<feature>.md`.
- Перед кодом: spec согласован, plan декомпозирован.
- Перед PR: `pytest -q` зелёный, `python scripts/run_mvp_repo_release_health_check.py` прошёл,
  ruff-линт чист (см. `CLAUDE.md`).
- Локальный скилл: `.claude/skills/sdd/SKILL.md` — ориентирует сессию на этот цикл.
