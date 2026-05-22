# CodeGraph Skill

This repository uses the official CodeGraph package (`@colbymchenry/codegraph`) for semantic code intelligence.

## When to use

- Before exploring or searching the codebase
- When you need to find related files, symbols, or dependencies
- When you need to locate tests for a source file
- When the user asks "where is X?" or "what depends on Y?"

## MCP tools (preferred)

When `.codegraph/` exists, use these MCP tools directly for targeted lookups:

| Tool | Purpose |
|------|---------|
| `codegraph_search` | Find symbols by name across the codebase |
| `codegraph_callers` | Find what calls a function |
| `codegraph_callees` | Find what a function calls |
| `codegraph_impact` | Analyze what code is affected by changing a symbol |
| `codegraph_node` | Get details about a specific symbol |
| `codegraph_files` | Get indexed file structure |
| `codegraph_status` | Check index health and statistics |

For larger exploration tasks, spawn Explore agents with CodeGraph instructions rather than using `codegraph_context` or `codegraph_explore` in the main session.

## CLI commands

```bash
codegraph init -i                        # Initialize and index
codegraph index --force                  # Full rebuild
codegraph sync                           # Incremental update
codegraph status                         # Show index statistics
codegraph query "<term>" --json          # Search symbols
codegraph files --json                   # File structure from index
codegraph query "<term>" --kind function # Filter by kind
codegraph affected src/file.py --stdin   # Find affected test files
```

## Workflow

1. Start with `codegraph_status` to verify the index is healthy.
2. Use `codegraph_search` to find relevant symbols by name.
3. Use `codegraph_node` to see details for a specific symbol.
4. Use `codegraph_callers`/`codegraph_callees` to trace call flow.
5. Use `codegraph_files` for file structure instead of filesystem scanning.
6. Read full source files only after CodeGraph identifies the relevant files and symbols.

## Keeping context low

- Prefer exact symbols and line ranges from CodeGraph.
- Do not load broad directories when CodeGraph can narrow the search.
- Use `codegraph_search` with specific queries instead of reading many files.

## Stale graph

If the graph is stale or missing, run:

```bash
codegraph sync          # incremental update
codegraph index --force # full rebuild
```
