# /sync — Bidirectional Local ↔ GitHub Sync

Synchronize the local repository with GitHub: pull missing remote changes, push local-only work, run CI, clean up stale branches.

## Safety Rules

- Never push directly to `main`
- Never force-push any branch
- Never commit files matching: `.env`, `*.pem`, `*.key`, `settings.local.json`, `image.png`, binary artifacts, secrets
- CI must be green before merging any PR
- Ask the user before deleting any branch
- Stop and report if anything unexpected is found

## Execution

### Phase 1: Discover

Run these in parallel to collect the full picture:

```bash
# Fetch latest from remote
git fetch origin 2>&1

# Ahead/behind count vs origin/main
git rev-list --left-right --count origin/main...HEAD 2>/dev/null

# Commits on remote not in local
git log --oneline HEAD..origin/main 2>/dev/null

# Commits on local not in remote
git log --oneline origin/main..HEAD 2>/dev/null

# Working tree state
git status --short

# Open PRs
gh pr list --state open --json number,title,headRefName,statusCheckRollup,isDraft 2>&1

# Local branches with merge status
git branch --merged main
git branch --no-merged main

# Remote branches with merge status
git branch -r --merged origin/main
git branch -r --no-merged origin/main
```

Print a compact discovery summary:

```
## Sync Discovery

### Divergence
Local main: <N> commits behind, <M> commits ahead of origin/main

### Remote commits to pull
<list or "none">

### Local commits to push
<list or "none">

### Uncommitted local changes
<list or "clean">

### Open PRs
<list or "none">

### Merged local branches (candidates for cleanup)
<list or "none">

### Unmerged remote branches
<list or "none">
```

Ask the user to confirm before proceeding to Phase 2.

---

### Phase 2: Pull Remote Changes

If origin/main is ahead of local main:

```bash
git checkout main
git pull --rebase origin main
```

Report what was pulled:
```
## Pulled <N> commits from origin/main
<one-line-per-commit summary>
```

If there are merge conflicts: STOP, report the conflicts, and ask the user how to proceed. Do NOT auto-resolve blindly.

If already up to date: say so and move on.

---

### Phase 3: Push Local-Only Work

Analyze uncommitted and untracked files. Separate into two groups:

**Skip list** (never commit these):
- `.env`, `.env.*` files
- `settings.local.json`
- `*.pem`, `*.key` — any secret files
- `image.png` and other binary artifacts
- Anything inside `.claude/` that is local-only config

**Commit list** (files that should go to GitHub):
- Source code, tests, configs, docs, scripts
- `.claude/commands/*.md` — project skills/commands (these ARE tracked)

If there are files to push:

1. Create a feature branch:
   ```bash
   git checkout main
   git checkout -b chore/sync-local-changes
   ```

2. Stage only the intended files (never `git add .`):
   ```bash
   git add <specific files>
   ```

3. Commit:
   ```bash
   git commit -m "$(cat <<'EOF'
   chore: sync local changes to remote

   Co-Authored-By: Claude <noreply@anthropic.com>
   EOF
   )"
   ```

4. Push and create PR:
   ```bash
   git push -u origin chore/sync-local-changes
   gh pr create --title "chore: sync local changes to remote" --body "$(cat <<'EOF'
   ## Summary
   - Bidirectional sync: pushing local-only files to GitHub

   ## Test plan
   - [ ] CI passes on this PR

   🤖 Generated with [Claude Code](https://claude.com/claude-code)
   EOF
   )"
   ```

If there's nothing to push: say so and move on.

---

### Phase 4: CI Validation

If any PR was created or any workflow was triggered:

```bash
# List recent runs
gh run list --limit 5 --json status,conclusion,name,headBranch,event,createdAt,databaseId
```

If any run is `in_progress` or `queued`:
- Poll every 30 seconds: `gh run view <run-id> --json status,conclusion`
- Report progress to the user while waiting
- Do NOT merge until all runs are `completed` with `conclusion: success`

If any run fails:
- Pull failed logs: `gh run view <run-id> --log-failed`
- Report the failure with details
- Do NOT merge — ask the user how to proceed

If CI is green (or no CI was triggered):
- Report success

---

### Phase 5: Cleanup

**Delete merged local branches** (ask user first):

```bash
# List branches merged into main that are safe to delete
git branch --merged main | grep -v '^\* main$'
```

For each: ask the user before running `git branch -d <branch>`.

**Report unmerged remote branches:**

```bash
git branch -r --no-merged origin/main | grep -v 'origin/main' | grep -v 'origin/HEAD'
```

List these for the user — do NOT delete them, just report. The user decides if they should be PR'd, rebased, or abandoned.

---

### Final Summary

```
## Sync Complete

- Pulled: <N> commits from origin/main
- Pushed: <branch> → <PR URL or "nothing to push">
- CI: <green/red/not triggered>
- Local branches cleaned: <list or "none">
- Unmerged remote branches remaining: <list or "none">
```
