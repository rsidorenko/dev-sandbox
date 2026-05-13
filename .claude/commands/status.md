# /status — Project Health Dashboard

Show a compact project health dashboard. Read-only, fast (~30 seconds).

## What it checks

1. **Git state** — current branch, HEAD, uncommitted changes, untracked files
2. **GitHub sync** — local vs remote divergence, auth status
3. **Open PRs** — list with status (open/draft, CI status)
4. **Recent CI** — last 5 workflow runs with pass/fail
5. **Test suite** — quick `pytest --co -q` to count tests (does NOT run them)
6. **Release readiness** — one-line summary from `run_mvp_repo_release_health_check.py`

## Execution

Run these commands in parallel where possible. Collect results, then print a compact dashboard.

```bash
# Git state
git status --short
git branch --show-current
git log --oneline -1

# GitHub sync
git fetch origin 2>&1
git rev-list --left-right --count origin/main...HEAD

# Open PRs
gh pr list --state open --json number,title,headRefName,statusCheckRollup,isDraft

# Recent CI
gh run list --limit 5 --json status,conclusion,name,headBranch,event,createdAt,databaseId

# Test count (collect only, do not run)
cd backend && python -m pytest --co -q 2>&1 | tail -1

# Release health (static, safe)
cd backend && python scripts/run_mvp_repo_release_health_check.py 2>&1
```

## Output format

Print a dashboard like this:

```
## Project Status

### Git
Branch: <branch> @ <short-hash>
Sync: <ahead/behind/clean> vs origin/main
Dirty: <clean / list of modified files>
Untracked: <list or "none">

### PRs
<#number> <title> — <CI: passing/failing/pending> <draft?>

### CI (last 5)
<status icon> <workflow-name> — <branch> — <time>
<status icon> <workflow-name> — <branch> — <time>
...

### Tests
< count > tests collected

### Release Health
< pass / fail — one-line summary >
```

## Rules

- Do NOT run actual tests (too slow for a dashboard)
- Do NOT modify any files
- If GitHub auth fails, report it clearly — do not skip silently
- If a command fails, report the error inline rather than hiding it
- Keep the entire output compact — this is a quick glance, not a full report
