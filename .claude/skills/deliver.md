# /deliver — Trunk Based Development Delivery Cycle

Implement a scoped change using Trunk Based Development (TBD) workflow for this project.

## TBD Core Rules

- **Short-lived branches**: feature branches live no longer than **24 hours**
- **One developer per branch**: one person owns one branch at a time
- **Small scope**: one task = one branch = one PR. If it doesn't fit in 24h, decompose it
- **Pre-integration build**: tests MUST pass locally BEFORE pushing
- **CI must be green BEFORE merge**: never merge a red PR
- **Release from main**: feature branches never produce release artifacts
- **Commit early and often**: each commit should be compilable, but doesn't need to be "done"
- **Branch by abstraction**: for large refactors, use abstraction layers and feature flags instead of long-lived branches

## Branch Naming

```
<type>/<short-scope>

Types: feat  fix  refactor  docs  test  chore
```

Examples: `feat/checkout-hmac`, `fix/rate-limit-reset`, `docs/runbook-update`

## Commit Messages

```
type(scope): description in imperative mood

Co-Authored-By: Claude <noreply@anthropic.com>
```

Examples: `feat(billing): add HMAC checkout reference signing`, `fix(dispatcher): reset rate limiter on cooldown expiry`

## Delivery Steps

Execute these steps in order. If any step fails, fix it before proceeding.

### Step 1: Sync with main

```bash
git checkout main
git pull --rebase origin main
```

### Step 2: Create short-lived feature branch

```bash
git checkout -b <type>/<scope>
```

Ask the user for the scope if not obvious from the task.

### Step 3: Read relevant source files

Before writing any code, read the files that will be affected. Understand existing patterns and conventions. Check:

- `backend/src/app/` for source code patterns
- `backend/tests/` for test conventions
- `CLAUDE.md` for project constraints and safety boundaries

### Step 4: Implement scoped changes

- Make small, focused commits
- Each commit should leave the codebase in a working state
- Follow existing code patterns — don't introduce new abstractions unless necessary
- Do NOT touch `.cursor/plans/`
- Do NOT commit secrets, `.env` files, or credentials

### Step 5: Local validation

Run tests before pushing. If environment is missing, report honestly.

```bash
cd backend && python -m pytest -q
```

If the task touches release/docs, also run:

```bash
cd backend && python scripts/run_mvp_repo_release_health_check.py
```

### Step 6: Update docs if behavior changes

If the change affects operator-visible behavior, update relevant runbooks and release docs. Docs-only changes still need `git diff --check`.

### Step 7: Verify clean state

```bash
git diff --check
git status --short
```

### Step 8: Commit

Stage only the intended files. Never `git add .` blindly.

```bash
git add <specific files>
git commit
```

### Step 9: Push

```bash
git push -u origin <branch>
```

Push within 24 hours of branch creation. If the branch is older, rebase onto current main first:

```bash
git fetch origin
git rebase origin/main
git push
```

### Step 10: Create PR

```bash
gh pr create --title "type(scope): short description" --body "$(cat <<'EOF'
## Summary
- 1-3 bullet points

## Test plan
- [ ] checklist items

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

PR title should be under 70 characters.

### Step 11: Wait for CI and review

```bash
gh run list --limit 5
```

Two CI workflows may trigger depending on changed paths:
- `backend-mvp-release-readiness` — for release/handoff docs
- `backend-postgres-mvp-smoke-validation` — for src/tests/migrations

If CI fails because of this PR's changes, fix immediately. If CI fails due to a pre-existing issue, report it — do not hide it.

### Step 12: Merge and cleanup

After CI is green and review is approved:

```bash
# Delete local branch
git checkout main
git pull --rebase origin main
git branch -d <branch>

# Delete remote branch (if not auto-deleted by GitHub)
git push origin --delete <branch>
```

## Safety Boundaries

These are hard stops. If any would be required, STOP and report to the operator:

- No real provider SDK / vendor integration
- No raw credential / config delivery to users
- No committing secrets (tokens, keys, .env)
- No pushing directly to `main`
- No force-pushing any branch
- No disabling CI workflows
- No editing files under `.cursor/plans/`
- No enabling `TELEGRAM_ACCESS_RESEND_ENABLE` by default
- No destructive retention without operator dry-run approval
- No short-circuiting UC-04 (ingestion) or UC-05 (subscription apply)

Full list of constraints is in `CLAUDE.md` — read it at the start of every session.

## Branch by Abstraction

For changes too large to fit in 24 hours:

1. Create an abstraction layer or interface in the first branch
2. Merge the abstraction (working, no behavior change)
3. Implement the new behavior behind a feature flag in the next branch
4. Switch over and remove the old path in a follow-up branch
5. Each branch: < 24 hours, reviewed, CI green

## Troubleshooting

- **Merge conflict on rebase**: resolve manually, `git rebase --continue`, never `--force`
- **CI red on pre-existing failure**: report with evidence, do not fix unrelated issues
- **Branch older than 24h**: rebase onto main, push, get it merged today
- **Scope too large**: stop, decompose into smaller tasks, start a new branch
