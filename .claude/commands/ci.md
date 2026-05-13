# /ci — Check CI Status and Diagnose Failures

Check CI workflow runs, wait for in-progress ones, and diagnose failures.

## Workflow detection

Two active CI workflows exist in this project:

| Workflow | Triggers on paths |
|----------|-------------------|
| `backend-postgres-mvp-smoke-validation` | `backend/src/**`, `backend/tests/**`, `backend/migrations/**`, smoke scripts |
| `backend-mvp-release-readiness` | `PROJECT_HANDOFF.md`, `backend/RELEASE_STATUS.md`, release docs/scripts/tests |

Both also run on `workflow_dispatch`.

## Execution

### Step 1: List recent runs

```bash
gh run list --limit 10 --json status,conclusion,name,headBranch,event,createdAt,databaseId,headSha
```

### Step 2: If any run is `in_progress` or `queued`

Wait for it. Poll every 30 seconds:

```bash
gh run view <run-id> --json status,conclusion
```

Continue polling until `status` is `completed`. Report progress to the user while waiting.

### Step 3: If any run has `conclusion: failure`

Pull the failed logs:

```bash
gh run view <run-id> --log-failed
```

Parse the output and report:
- Which workflow failed
- Which job/step failed
- The error message and traceback
- The file and line number (if available)
- A suggestion for what to fix

### Step 4: Summary

Print a compact summary:

```
## CI Status

### Recent runs
<status icon> <workflow> — <branch> — <conclusion> — <time>
...

### Failures (if any)
**<workflow>** on <branch> (run <id>)
  Step: <step name>
  File: <file:line>
  Error: <message>
  Suggestion: <what to fix>

To auto-fix, run: /fix-ci
```

## Rules

- If no runs exist at all (empty repo or no pushes), say so clearly
- If a run is in progress, tell the user the estimated wait and keep polling
- Do NOT modify any files — this is read-only
- If `gh` auth fails, report it immediately
- Do not fabricate CI results — only report what the API returns
