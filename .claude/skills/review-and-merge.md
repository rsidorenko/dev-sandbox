# /review-and-merge — Review PR and Merge

Review the current branch's PR, verify CI, and merge if everything is clean.

## Execution

### Step 1: Identify the PR

Find the open PR for the current branch:

```bash
gh pr list --head $(git branch --show-current) --state open --json number,title,headRefName,isDraft,url
```

If no PR found, stop and tell the user to create one first (use `/deliver` Step 10).

If multiple PRs found, ask which one to review.

### Step 2: Verify CI is green

Check CI status for this PR:

```bash
gh pr view <number> --json statusCheckRollup --jq '.statusCheckRollup[] | {name: .name, status: .status, conclusion: .conclusion}'
```

Rules:
- If CI is **in progress** — wait for completion (poll every 30s)
- If CI **failed** — stop. Report the failure and suggest `/fix-ci`. Do NOT merge.
- If CI **passed** — proceed to review.
- If CI was **not triggered** (e.g. skill-only changes) — note this and proceed.

### Step 3: Review the diff

Get the full PR diff:

```bash
gh pr diff <number>
```

Review the changes against these criteria:

#### Code quality
- No secrets, tokens, credentials, `.env` values committed
- No commented-out code or debug prints left behind
- No unnecessary abstractions or over-engineering
- Follows existing project patterns and conventions
- No dead code or unused imports

#### Safety boundaries (from CLAUDE.md)
- No real provider SDK / vendor integration
- No raw credential / config delivery to users
- No `TELEGRAM_ACCESS_RESEND_ENABLE` enabled by default
- No destructive retention without dry-run
- No short-circuiting UC-04 or UC-05
- No files edited under `.cursor/plans/`

#### Test coverage
- New behavior has corresponding tests
- Tests are meaningful, not just asserting true
- Edge cases covered where appropriate

#### Docs
- Behavior changes reflected in relevant docs/runbooks
- No stale references

### Step 4: Present review

Print the review summary:

```
## Review: PR #<number> — <title>

### CI
  <green/failed/not triggered> — <details>

### Changes (<N> files, <+added/-removed> lines)
  <file> — <what changed>
  ...

### Issues found
  **BLOCKER:** <issue> — <file:line> — must fix before merge
  **WARNING:** <issue> — <file:line> — should fix
  **NOTE:** <observation> — <file:line> — informational

### Verdict
  LGTM — ready to merge / NEEDS FIXES — do not merge
```

### Step 5: Merge (only if LGTM)

If the review passes with no blockers:

```bash
gh pr merge <number> --squash --delete-branch
```

Then sync local:

```bash
git checkout main
git pull origin main
git branch -d <feature-branch> 2>/dev/null || true
```

### Step 6: Report

```
## Merged

PR #<number> — <title>
Merge commit: <hash>
Branch <feature-branch> deleted.
Local synced to main @ <short-hash>
```

## Rules

- NEVER merge a PR with CI failures — fix first
- NEVER merge a PR with a BLOCKER issue — the user must approve or fix
- If there are only WARNINGs, ask the user before merging
- If there are only NOTEs, proceed with merge
- Always use `--squash` merge to keep main history clean
- If the user says "merge anyway" despite warnings, proceed but document the warnings
- Do NOT merge draft PRs without explicit user approval
- Always clean up the branch after merge (local + remote)
