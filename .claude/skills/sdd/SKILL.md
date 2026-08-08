# SDD Skill

Этот репозиторий ведёт разработку по паттерну **Spec-Driven Development (SDD)** через фреймворк
**superpowers** (вместо OpenSpec). Полный гайд: [`docs/superpowers/README.md`](../../../docs/superpowers/README.md).
Шаблоны: `docs/superpowers/_templates/{spec,plan}.md`. Живой эталон:
`docs/superpowers/specs/2026-08-08-referral-stats-design.md`.

## Когда использовать

- В начале **нетривиальной** фичи/рефакторинга/фикса (несколько файлов, несколько шагов,
  архитектурное решение).
- До того, как писать код — сначала зафиксировать, **что** и **зачем** делаем.

Не требуется для тривиальных правок (опечатка, однострочный фикс, ренейм).

## Жизненный цикл

```
brainstorm  →  spec  →  review  →  plan  →  tasks  →  implement  →  finish
```

1. **brainstorm** — уточнить задачу и ограничения с пользователем. Читать реальный код
   (CodeGraph первым, затем Read) и ADR — не полагаться на память.
2. **spec** — `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` из шаблона `_templates/spec.md`.
3. **review** — проверить spec на полноту/противоречия, согласовать с пользователем.
4. **plan** — `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` из шаблона `_templates/plan.md`:
   фазы + задачи, каждая с file-refs и acceptance.
5. **tasks/implement** — код строго по задачам плана; после каждой — рабочее состояние.
6. **finish** — `pytest -q`, `python scripts/run_mvp_repo_release_health_check.py`, ruff, PR.

## Где лежат артефакты

| Артефакт | Путь |
|---|---|
| Гайд | `docs/superpowers/README.md` |
| Шаблон spec | `docs/superpowers/_templates/spec.md` |
| Шаблон plan | `docs/superpowers/_templates/plan.md` |
| Спеки | `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md` |
| Планы | `docs/superpowers/plans/YYYY-MM-DD-<feature>.md` |

Имя файла: дата ISO + kebab-case slug **на английском**, проза — **на русском**.

## Интеграция с /deliver

SDD добавляет фазу проектирования перед кодом, не отменяя TBD-цикл `/deliver`:
spec/plan пишутся **до/в начале ветки** (`<type>/<scope>`), затем `/deliver` реализует задачи
плана. Одна фича = один spec + один plan + одна ветка + один PR.

## Специфика модели (GLM-5.2)

- Ядро SDD (spec→plan→задачи) — это markdown, работает на любой модели. **Веди spec/plan
  вручную по шаблонам** — это надёжно.
- Тяжёлые multi-subagent-фичи полного плагина superpowers оптимизированы под Claude; на GLM-5.2
  используй их по возможности, но не полагайся критически.
- Всегда читай реальный код перед spec'ом; не доверяй памяти о расположении символов.

## Установка полного плагина (опционально)

```
/plugin marketplace add obra/superpowers
/plugin
```

Пути артефактов совпадают с конвенцией superpowers v5 (`docs/superpowers/specs|plans`).
