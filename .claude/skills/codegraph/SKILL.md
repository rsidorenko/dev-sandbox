# Code Graph Skill

This repository has a tracked code graph at `.codegraph/`. Use it before reading many files.

## When to use

- Before exploring or searching the codebase
- When you need to find related files, symbols, or dependencies
- When you need to locate tests for a source file
- When the user asks "where is X?" or "what depends on Y?"

## Commands

Prefer these commands over reading full files or broad directory listings:

```
python scripts/codegraph/index.py summary
python scripts/codegraph/index.py search "<query>"
python scripts/codegraph/index.py related "<path>" --depth 1
python scripts/codegraph/index.py file "<path>"
```

## Workflow

1. Start with `summary` to understand project structure.
2. Use `search` to find relevant files by name, symbol, import, or concept.
3. Use `file` to see metadata for a specific file (imports, exports, symbols with line ranges).
4. Use `related` to find neighboring files and test relationships.
5. Read full source files only after the graph identifies the relevant files and line ranges.

## Keeping context low

- Prefer exact file paths and line ranges from the graph.
- Do not load broad directories when the graph can narrow the search.
- Use `search` with specific queries instead of reading many files.

## Stale graph

If the graph is stale or missing, suggest running:

```
python scripts/codegraph/index.py build
```

Then re-run the query.
