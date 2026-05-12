# /fix-ci — Diagnose and Fix CI Failures

Investigate a failed CI run, identify the root cause, and offer to fix it.

## Workflow

### Step 1: Identify the failure

Find the most recent failed run:

```bash
gh run list --limit 10 --json status,conclusion,name,headBranch,headSha,databaseId,createdAt \
  --jq '.[] | select(.conclusion == "failure") | .databaseId' | head -1
```

If the user specified a run ID, use that instead.

### Step 2: Pull failure logs

```bash
gh run view <run-id> --log-failed
```

### Step 3: Analyze

From the logs, extract:

1. **Workflow name** and **branch**
2. **Failed job and step** — which step in the workflow failed
3. **Error type** — test failure, import error, syntax error, assertion error, timeout, etc.
4. **File and line** — the exact file and line number causing the failure
5. **Root cause** — distinguish between:
   - Caused by this branch's changes (fix it)
   - Pre-existing failure unrelated to current changes (report, do not fix)
   - Environment/infra issue (flake, timeout — suggest re-run)

### Step 4: Read the failing file

Read the file around the failing line to understand context:

```
Read the relevant source file(s) and test file(s) around the failure point
```

### Step 5: Propose a fix

Present the diagnosis to the user:

```
## CI Failure Diagnosis

**Workflow:** <name>
**Branch:** <branch>
**Run:** <id> (<url>)

**Failed step:** <step name>
**File:** <file:line>
**Error type:** <type>
**Root cause:** <1-2 sentence explanation>

**Classification:** caused by this branch / pre-existing / flake

### Suggested fix
<description of what to change>

<if caused by this branch>
Apply fix? (I can implement it directly)
</if>

<if pre-existing>
This failure exists on main and is not caused by current changes.
Recommendation: report it, do not fix in this PR.
</if>

<if flake/infra>
Recommendation: re-run the workflow with `gh run rerun <run-id>`
</if>
```

### Step 6: Fix (if user approves)

If the user approves and it's a fixable issue:

1. Create a fix on the current branch (or a new branch if on main)
2. Follow the `/deliver` skill workflow for the fix
3. Push and verify CI passes

## Rules

- NEVER fix a pre-existing failure in someone else's PR — only report it
- NEVER mark a failure as "fixed" without re-running CI and confirming green
- Always read the actual source file — do not guess the fix from the error message alone
- If the failure is in a test, check whether the test is correct or the code is correct before fixing either
- If multiple failures exist, address them one at a time — fix the first, push, check CI, then the next
- Do NOT modify CI workflow files to make tests pass — fix the actual code
- Do NOT skip tests, add `@pytest.skip`, or comment out failing assertions
