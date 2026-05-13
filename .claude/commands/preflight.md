# /preflight — Static Validation Before Commit

Run all static checks on the current codebase before committing. Safe, no Docker/DB/network required.

## What it runs

These scripts are read-only and require only the local repo (no Docker, no PostgreSQL, no network):

| Check | Script | What it validates |
|-------|--------|-------------------|
| Repo health | `run_mvp_repo_release_health_check.py` | File presence, structure, basic health |
| Release checklist | `run_mvp_release_checklist.py` | Artifact/doc presence check |
| Release preflight | `run_mvp_release_preflight.py` | Preflight gate validation |
| Final static handoff | `run_mvp_final_static_handoff_check.py` | Static handoff completeness |

Additionally, always run:

| Check | Command | What it validates |
|-------|---------|-------------------|
| Whitespace/errors | `git diff --check` | No whitespace errors in staged/unstaged changes |
| Tests | `python -m pytest -q` | Full test suite |

## Execution

Run all commands from `backend/` directory. Execute independent checks in parallel:

```bash
# Parallel group 1 — static scripts
cd backend && python scripts/run_mvp_repo_release_health_check.py 2>&1
cd backend && python scripts/run_mvp_release_checklist.py 2>&1
cd backend && python scripts/run_mvp_release_preflight.py 2>&1
cd backend && python scripts/run_mvp_final_static_handoff_check.py 2>&1

# Parallel group 2 — git + tests
git diff --check 2>&1
cd backend && python -m pytest -q 2>&1
```

## Output format

```
## Preflight Results

### Static checks
  [PASS] Repo health check
  [PASS] Release checklist
  [PASS] Release preflight
  [FAIL] Final static handoff — <reason>
  [PASS] Tests (N passed, N failed)

### Git
  [PASS] No whitespace errors
  Unstaged: <list or "none">
  Untracked: <list or "none">

### Verdict
  READY TO COMMIT / DO NOT COMMIT — <reason>
```

## Rules

- Report exact exit codes and error messages — do not summarize away failures
- If a script is missing, report it as `[SKIP] script not found` — do not invent results
- If a script fails with an import error or missing dependency, report `[ERROR] <message>` honestly
- Tests are part of preflight — if tests fail, the verdict is DO NOT COMMIT
- All checks must run even if one fails — report all results
- Do NOT modify any files — this is read-only validation
