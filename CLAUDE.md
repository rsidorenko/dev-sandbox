# CLAUDE.md — Claude Code Repository Workflow Guide

This file is the primary orientation document for Claude Code sessions operating in this repository.
Read it at the start of every session. Treat current repo state and GitHub/CI state as the authoritative
source of truth — this file provides workflow rules and context, not a substitute for reading the code.

---

## Project summary

**TelegramBotVPN** is the backend/control-plane for a Telegram subscription + VPN MVP.

- **GitHub remote:** `https://github.com/rsidorenko/startprojectformillion.git` (remote name: `origin`)
- **Local path:** `D:\TelegramBotVPN`
- **Primary language:** Python 3.12+ (CI uses 3.12; local may differ — check `python --version`)
- **Runtime stack:** httpx, starlette/uvicorn, asyncpg (PostgreSQL), Telegram Bot API

**Product path (high level):**

```
Telegram user interaction
  → checkout / payment / fulfillment decision path
    → billing ingestion (operator-normalized fact) → UC-05 subscription apply
      → subscription lifecycle state
        → access readiness / /status / /get_access / /resend_access
          → config issuance (abstraction, fake provider today)
            → durable issuance state (redacted, coarse delivery only)
              → operator validation → controlled release readiness
```

---

## Current stage: MVP / operator validation

The release package is **ready for operator validation, not full production certification**.

**Not yet in scope / not implemented:**
- Public billing ingress (design-only in ADR-31/32)
- Real access/config provider SDK (fake provider only)
- Raw credential / config delivery to users
- Full production SLO / alerting certification
- External observability pipeline validation
- Multi-tenant, public admin UI, marketing automation

**Primary status document:** `backend/RELEASE_STATUS.md`
**Primary handoff index:** `PROJECT_HANDOFF.md`

---

## Repository structure

```
TelegramBotVPN/
├── CLAUDE.md                         # this file
├── PROJECT_HANDOFF.md                # handoff index, primary status and commands
├── .github/workflows/                # CI workflow definitions (two active gates)
│   ├── backend-mvp-release-readiness.yml
│   └── backend-postgres-mvp-smoke-validation.yml
├── docs/architecture/                # 37 ADRs (system design, billing, issuance, security)
├── backend/
│   ├── RELEASE_STATUS.md             # MVP release status and gates
│   ├── pyproject.toml                # project metadata, deps, pytest config
│   ├── docs/                         # runbooks, ADRs, smoke docs
│   ├── migrations/                   # PostgreSQL migration SQL files
│   ├── scripts/                      # operator/validation/release scripts
│   └── src/app/
│       ├── application/              # use-case handlers, bootstrap, interfaces
│       ├── bot_transport/            # Telegram transport layer, dispatcher, message catalog
│       ├── persistence/              # PostgreSQL and in-memory repositories
│       ├── runtime/                  # polling, webhook, raw HTTP client
│       ├── security/                 # webhook policy, checkout reference, diagnostics
│       └── shared/                   # shared types
│   └── tests/                        # pytest test suite
└── .cursor/plans/                    # historical planning files — see below
```

---

## Source-of-truth priority

When information conflicts, resolve in this order:

1. **Current local repo state** — actual files, HEAD code, git history
2. **GitHub remote and CI state** — branch/PR state, workflow runs, CI results
3. **Current docs, ADRs, runbooks, and release docs** — `backend/docs/`, `docs/architecture/`, `PROJECT_HANDOFF.md`, `backend/RELEASE_STATUS.md`
4. **`.cursor/plans/`** — historical context only; see section below
5. **Old chat history or stale planning artifacts** — lowest priority; always verify against HEAD

**Practical rule:** before implementing anything, read the relevant source files and check CI. Docs and plans
describe past intentions; only HEAD reflects what was actually built.

---

## Historical context: `.cursor/plans/`

`.cursor/plans/` contains historical planning files from earlier design sessions (ADR outlines, slice plans,
next-slice choices). These are **read-only reference material**:

- Do **not** edit any file under `.cursor/plans/`.
- Do **not** treat `.cursor/plans/` content as an active backlog or implementation mandate.
- If a `.cursor/plans/` file conflicts with current HEAD code or current docs, **HEAD wins**.
- Plans reflect what was being considered at a point in time, not what was delivered.

---

## Main local commands

Run all commands from the `backend/` directory unless noted.

### Static / handoff-only (safe, no Docker/DB/network required)

```bash
# Primary release readiness wrapper (health check + checklist + preflight, no config doctor by default)
cd backend && python scripts/run_mvp_release_readiness.py

# Static repo health check — read-only, does not run tests/Docker/DB/network
cd backend && python scripts/run_mvp_repo_release_health_check.py

# Final static handoff check — static/handoff-only; does not replace readiness/preflight/smoke
cd backend && python scripts/run_mvp_final_static_handoff_check.py

# Release checklist — artifact/doc presence check only
cd backend && python scripts/run_mvp_release_checklist.py

# Release preflight
cd backend && python scripts/run_mvp_release_preflight.py

# Optional bounded handoff summary (read-only/informational)
cd backend && python scripts/print_mvp_release_handoff_summary.py
```

### Requires real operator environment

```bash
# Config doctor — requires actual operator env values; use --profile to scope
cd backend && python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all
```

### Requires Docker and PostgreSQL

```bash
# Local Docker smoke
cd backend && python scripts/run_postgres_mvp_smoke_local.py

# Canonical smoke (used by CI)
cd backend && python scripts/run_postgres_mvp_smoke.py
```

### Release-candidate final gate (blocking)

```bash
# Go/no-go boundary — exit 0 = all required checks passed; non-zero = launch blocked
cd backend && python scripts/validate_release_candidate.py
```

### Test suite

```bash
cd backend && python -m pytest -q
# or a single file
cd backend && python -m pytest -q tests/test_<name>.py
```

### Command discovery

Always check these for additional or updated commands before assuming the list above is complete:
- `backend/pyproject.toml` — project metadata, test config
- `README.md` and `PROJECT_HANDOFF.md` — primary status and commands
- `backend/RELEASE_STATUS.md` — release gates and manual go/no-go steps
- `.github/workflows/` — CI steps reflect what commands are actually run in the gates

---

## Trunk Based Development workflow

This project follows **Trunk Based Development** (TBD). See `/deliver` skill for the full step-by-step cycle.

### Core TBD rules

- **Short-lived feature branches** — max 24 hours from creation to merge
- **One developer per branch** — one person owns one branch at a time
- **Small scope** — one task = one branch = one PR. Decompose if it doesn't fit in 24h
- **Pre-integration build** — tests MUST pass locally before pushing
- **CI green before merge** — never merge a red PR into `main`
- **Release from main** — feature branches never produce release artifacts
- **Commit early and often** — each commit should leave code in a working state

### Branch naming

```
<type>/<short-scope>

Types: feat  fix  refactor  docs  test  chore
```

### Commit messages

```
type(scope): imperative description
```

### Delivery cycle (summary)

1. **Sync** — `git checkout main && git pull --rebase origin main`
2. **Branch** — `git checkout -b <type>/<scope>`
3. **Read** — read relevant source files, do not rely on memory
4. **Implement** — small focused commits, each compilable
5. **Validate** — `cd backend && python -m pytest -q` (must pass before push)
6. **Commit** — stage only intended files, never `git add .` blindly
7. **Push** — `git push -u origin <branch>` (within 24h of branch creation)
8. **PR** — `gh pr create` with summary and test plan
9. **CI + review** — wait for green CI, get review approval
10. **Merge and cleanup** — delete branch after merge

### Branch by abstraction

For changes too large for a single 24h branch: introduce an abstraction layer first (no behavior change),
then implement behind a feature flag, then switch over. Each step is its own short-lived branch.

### Reporting

Return a concise delivery report using the format below. Do not invent output, commit hashes, PR links, or CI status.

**Docs-only changes** still require:
- `git diff --check` (no whitespace errors)
- Relevant static/lightweight readiness checks when available (at minimum `run_mvp_repo_release_health_check.py` and `run_mvp_final_static_handoff_check.py` if present and safe to run)

**Missing dependencies, missing environment, or unavailable services** must be reported honestly with the exact
error or skip reason. Never present a skipped check as passed.

---

## GitHub and CI workflow

**Repository:** `rsidorenko/startprojectformillion` on GitHub (remote `origin`)

**Active CI workflows:**

| Workflow | Trigger scope | What it runs |
|---|---|---|
| `backend-mvp-release-readiness` | `PROJECT_HANDOFF.md`, `backend/RELEASE_STATUS.md`, release/handoff docs/scripts/tests | Static: health check, checklist, preflight, final static handoff check, config doctor unit tests |
| `backend-postgres-mvp-smoke-validation` | `backend/src/**`, `backend/tests/**`, `backend/migrations/**`, smoke scripts, relevant runbooks | PostgreSQL smoke, integration tests, retention, billing, access, reconcile, ADM checks, release candidate validator |

**Important CI rules:**
- Never disable workflows, remove CI checks, or alter branch protection.
- Never push directly to `main`.
- Never force-push.
- A CI failure caused by this batch's changes must be fixed before the batch is considered complete.
- A pre-existing CI failure must be reported with evidence — do not hide it and do not make unrelated changes to fix it.
- `CLAUDE.md` at repo root does **not** trigger either active workflow (neither watches root `CLAUDE.md`); a PR touching only `CLAUDE.md` will not produce a CI run unless `workflow_dispatch` is used.

**Useful GitHub commands:**
```bash
gh pr create --title "..." --body "..."
gh pr view
gh run list --limit 10
gh run view <run-id>
gh run view <run-id> --log-failed
```

---

## Tests and local validation

### Test suite (unit + integration, no Docker required for most)
```bash
cd backend && python -m pytest -q
```

### Static validation (always safe to run)
```bash
cd backend && python scripts/run_mvp_repo_release_health_check.py
cd backend && python scripts/run_mvp_final_static_handoff_check.py
cd backend && python scripts/run_mvp_release_readiness.py
```

### Integration tests (PostgreSQL + Docker required)
```bash
# Needs Docker + running postgres service
cd backend && python scripts/run_postgres_mvp_smoke_local.py
```

### Manual go/no-go gates (operator-only, not automated)
- Config doctor with real operator env: `python scripts/run_mvp_config_doctor.py --profile <profile>`
- Local Docker smoke: `python scripts/run_postgres_mvp_smoke_local.py`
- Deployed webhook `/healthz` and `/readyz` verification
- Telegram `setWebhook` and webhook secret rotation (explicit operator step only)
- Retention delete dry-run before any delete opt-in

---

## Delivery report format

After each delivery, return a concise report. Do not invent output, commit hashes, PR links, or CI status.

```
## Delivery Report

- Branch: <branch> → merged into main
- Commit: <short hash>
- Files changed: <list>
- PR: <URL or "not created">
- Local validation: <pass/fail/skip with reason>
- CI status: <green/red/pending/not triggered>
- CI failures: <description or "none">
- Risks/blockers: <list or "none">
```

---

## Safety boundaries and hard stops

The following are **hard stops** for all Claude Code delivery batches in this repository.
If any of these would be required to complete a task, stop and report to the operator.

**Provider and secret constraints:**
- No real provider SDK / vendor integration (current slice uses fake provider only)
- No provider-specific public webhook implementation
- No raw credential / config delivery to users or in code
- No private keys, full provider configs, or reconstructable secrets in any committed file
- No Telegram instruction-class or full-config delivery in user-facing messages
- `TELEGRAM_ACCESS_RESEND_ENABLE` must NOT be enabled by default in any config or code change
- Do not commit secrets; do not print secret values in logs, tests, or reports

**Billing and ingestion constraints:**
- Do not treat operator billing ingest, payment fulfillment ingress, public billing ingress, and provider webhook as interchangeable — they are distinct paths with different trust boundaries (see ADR-31, ADR-32, ADR-37, and the UC-04 / UC-05 separation)
- Do not implement public billing webhook code or a production billing listener (design-only per ADR-31/32)
- Do not short-circuit UC-04 (ingestion/ledger) or UC-05 (subscription apply) in any new code path

**Retention constraints:**
- No destructive retention expansion without an explicit operator dry-run approval
- Retention deletes require explicit operator opt-in; do not add a default-enabled delete path

**Git / CI constraints:**
- Do not push directly to `main` or `master`
- Do not force-push any branch
- Do not disable CI workflows or remove CI checks
- Do not bypass tests with `--no-verify` (unless a strong documented reason exists and is stated in the commit)
- Do not hide CI failures

**File constraints:**
- Do not edit any file under `.cursor/plans/`
- Do not open or print values from `.env` files, private key files, or generated VPN configs; report only that sensitive files exist if relevant

---

## Billing / payment / fulfillment terminology

These are **distinct** concepts with different trust levels. Do not conflate them.

| Term | What it means | Trust level | ADR reference |
|---|---|---|---|
| **Operator billing ingest** | Operator-provided pre-built normalized JSON via `billing_ingestion_main` / `IngestNormalizedBillingFactHandler` (UC-04) | Trusted operator path | ADR-08, UC-04 |
| **UC-05 subscription apply** | Separate step: `billing_subscription_apply_main` / `ApplyAcceptedBillingFactHandler`; not auto-chained after ingest | Internal, controlled | ADR-09, UC-05 |
| **Payment fulfillment ingress** | Payment-side event path (distinct from operator ingest and public webhook) | Controlled ingress | `payment_fulfillment_ingress.py` |
| **Public billing ingress** | Future Internet-facing webhook from payment provider — design-only, NOT implemented | Untrusted until authenticated | ADR-31, ADR-32 |
| **Provider webhook** | Raw event from a payment/billing provider — must be authenticated, normalized, bounded before any domain mutation | Untrusted | ADR-31 |

**Key UC-04 / UC-05 invariant:** ingestion of a billing fact (UC-04) does NOT automatically make a user
paid/active. Subscription state change requires a controlled UC-05 apply step. Do not short-circuit this.

---

## Telegram access delivery constraints

- `/status`, `/resend_access`, `/get_access` commands exist but are gated by `TELEGRAM_ACCESS_RESEND_ENABLE`
- Feature flag default: **disabled** — explicit operator opt-in per deployment only
- When disabled: commands are parsed and routed but return a safe unavailable response; no entitlement, cooldown, or issuance state lookup occurs
- Telegram responses must only use **`redacted_reference`**, **`support_handoff`**, **`not_eligible`**, **`not_ready`**, or **`temporarily_unavailable`** delivery classes (see ADR-35)
- **`instruction` class is forbidden** in the current slice — enabling it requires an explicit future product + security decision
- No full secrets, private keys, raw provider refs, idempotency keys, credentials, DSN, or instructional config payload in any Telegram message
- `/status` never exposes raw provider refs, billing refs, internal IDs, or tokens
- Rate limiting and dedup are enforced at the transport boundary for `/status`, `/get_access`, `/resend_access`

**Runbook:** `backend/docs/telegram_access_resend_runbook.md`
**ADR:** `docs/architecture/35-user-facing-safe-access-delivery-envelope.md`

---

## Provider / issuance constraints

- Current implementation uses a **fake provider** and **redacted outputs** only
- No real access/config provider is selected or integrated (design-only in ADR-33, ADR-36)
- Provider selection criteria are documented in ADR-36 — do not implement a real provider without satisfying those criteria and the product + security decision checklist
- Issuance v1 design (ADR-33) is policy/design; do not treat it as an implementation mandate
- No concrete secrets, tokens, PEM blocks, VPN configs, hostnames, or ports in any committed file
- Provider adapter isolation rule: provider-specific logic must be isolated; do not leak provider exceptions verbatim to users or logs

**Runbook:** `backend/docs/issuance_operator_runbook.md`
**ADR:** `docs/architecture/33-config-issuance-v1-design.md`, `docs/architecture/36-access-config-provider-and-storage-policy.md`

---

## Retention constraints

- Retention delete operations require explicit operator opt-in (dry-run first, then delete-phase separately)
- Do not add a default-enabled delete path
- Do not expand destructive retention behavior without explicit operator dry-run approval and sign-off
- Retention dry-run script: `cd backend && python scripts/run_slice1_retention_dry_run.py`
- Retention delete (when approved): `cd backend && python scripts/reconcile_expired_access.py`

**Runbook:** `backend/docs/slice1_retention_dry_run_runbook.md`, `backend/docs/slice1_retention_scheduled_runbook.md`

---

## Secrets handling

- Secrets are provided exclusively via environment variables; never hardcoded
- Webhook secret is fail-closed — if not configured, the webhook endpoint rejects all requests
- Do not log, print, or commit secret values (tokens, DSNs, signing keys, provider keys)
- If `.env` files or private key files exist in the working tree, do not open or print their values; report only existence if relevant
- Config doctor (`run_mvp_config_doctor.py`) validates operator env with bounded, redacted output — this is the safe way to check secret presence
- Secret rotation is an explicit operator step; do not automate or bypass it

---

*Last updated: 2026-05-07. Maintained by: rsidorenko / Claude Code delivery batches.*
