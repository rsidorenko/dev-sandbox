# CodeGraph

This directory contains the CodeGraph index for TelegramBotVPN (Bravada VPN).

## What is this?

CodeGraph (`@colbymchenry/codegraph`) builds a semantic knowledge graph of the codebase using tree-sitter. It provides instant symbol search, call graphs, impact analysis, and code structure — all stored locally in a SQLite database.

## Setup

```bash
# Install CodeGraph (one-time)
npm i -g @colbymchenry/codegraph          # macOS/Linux/Windows
# Or on Windows PowerShell:
irm https://raw.githubusercontent.com/colbymchenry/codegraph/main/install.ps1 | iex

# Initialize and index the project
codegraph init -i
```

## Developer workflow

1. Make code changes.
2. Run: `codegraph sync` (incremental) or `codegraph index --force` (full rebuild).
3. Run: `codegraph status` to verify the index is healthy.
4. No need to commit the database — it is gitignored and rebuilt locally.

## Files tracked in Git

| File | Purpose |
|------|---------|
| `.gitignore` | Excludes `*.db`, `*.db-wal`, `*.db-shm`, `cache/`, `*.log`, `.dirty` |
| `README.md` | This documentation file |

The SQLite database (`codegraph.db`) is **not committed** — it is local-only and rebuilt by each developer.

## Files excluded from Git

| File | Why excluded |
|------|-------------|
| `codegraph.db` | Binary database, ~17MB, rebuilt locally |
| `codegraph.db-wal` | SQLite WAL journal |
| `codegraph.db-shm` | SQLite shared memory |
| `cache/` | Internal cache |
| `.dirty` | Change tracking marker |

## Deployment isolation

`.codegraph/`, `.claude/`, and `scripts/codegraph/` are tracked in Git for developer use but must not reach the production server.

**Mechanism**: Deploy uses artifact-based deployment (not `git pull` on the server). GitHub Actions builds a clean tarball via `rsync` that excludes these paths, uploads it to the server via `scp-action`, and the server swaps it into `/opt/bravada`.

**Verification**: After deploy, the workflow asserts:
```
test ! -e /opt/bravada/.codegraph
test ! -e /opt/bravada/.claude
test ! -e /opt/bravada/scripts/codegraph
```
If any excluded path exists, the deploy fails.

## Commands

| Command | Description |
|---------|-------------|
| `codegraph init -i` | Initialize and index the project |
| `codegraph index --force` | Full rebuild |
| `codegraph sync` | Incremental update |
| `codegraph status` | Show index statistics |
| `codegraph query "<term>" --json` | Search symbols |
| `codegraph files --json` | File structure from index |
| `codegraph affected <files>` | Find affected test files |

## MCP tools (for Claude Code)

When running as an MCP server, CodeGraph exposes: `codegraph_search`, `codegraph_context`, `codegraph_callers`, `codegraph_callees`, `codegraph_impact`, `codegraph_node`, `codegraph_files`, `codegraph_status`.

## Do not modify manually

The `codegraph.db` is auto-generated. Do not edit it by hand. Rebuild with `codegraph index --force` if corrupted.
