# CLAUDE.md — Claude Code Repository Workflow Guide

This file is the primary orientation document for Claude Code sessions operating in this repository.
Read it at the start of every session. Treat current repo state and GitHub/CI state as the authoritative
source of truth — this file provides workflow rules and context, not a substitute for reading the code.

---

## Project summary

**TelegramBotVPN** (branded "Bravada VPN") is a production Telegram bot + web backend for selling and managing VPN subscriptions via VLESS/Reality protocol through 3x-ui panels.

- **GitHub remote:** `https://github.com/rsidorenko/startprojectformillion.git` (remote name: `origin`)
- **Local path:** `D:\TelegramBotVPN`
- **Primary language:** Python 3.12+
- **Runtime stack:** httpx, starlette/uvicorn, asyncpg (PostgreSQL), Telegram Bot API (polling + webhook)
- **VPN infrastructure:** 3x-ui panels (VLESS + Reality TLS), multi-server across NL/DE/FI/US/JP
- **Web frontend:** Next.js site (separate repo) with payment, login, profile pages
- **Domain:** bravada-connect.ru

### What the system actually does

1. **User registration** — Telegram `/start` creates identity, auto-activates 3-day free trial with real VLESS keys
2. **Subscription plans** — 1 month (300 RUB), 3 months (750 RUB), 6 months (1350 RUB), with configurable device count
3. **Payment** — checkout via signed HMAC URLs, payment fulfillment ingress, balance payment from referral credits
4. **VPN key management** — real VLESS user creation across all active 3x-ui panels, subscription URL for auto-import into Karing/Happ/v2rayTune
5. **Key lifecycle** — automatic deactivation on expiry, deletion after 20-day grace period, reactivation on renewal, key reissue
6. **Notifications** — trial/subscription expiry warnings, key deletion notices via scheduled notification scheduler
7. **Referral program** — 2-level referral system (L1: 25-35%, L2: 2-5% depending on plan), referral balance usable for payments
8. **Email linking** — email verification with SMTP, account merge between Telegram and web accounts
9. **Web API** — subscription URL endpoint (`/sub/<token>`), payment processing, auth/login, profile management
10. **Admin support** — ADM-01 (read-only user lookup) and ADM-02 (ensure-access with opt-in and audit)

---

## Repository structure

```
TelegramBotVPN/
├── CLAUDE.md                         # this file
├── PROJECT_HANDOFF.md                # handoff index (legacy, may be stale)
├── .github/workflows/                # CI/CD: release readiness, smoke tests, deploy
├── docs/architecture/                # ADRs (system design, billing, issuance, security)
├── backend/
│   ├── RELEASE_STATUS.md             # release status (legacy, may reference old state)
│   ├── pyproject.toml                # project metadata, deps, pytest config
│   ├── docs/                         # runbooks
│   ├── migrations/                   # 30 PostgreSQL migrations
│   ├── scripts/                      # operator/validation/release scripts
│   └── src/app/
│       ├── application/              # use-case handlers, bootstrap, purchase, referral
│       ├── bot_transport/            # Telegram transport, dispatcher, storefront UI, message catalog
│       ├── domain/                   # plans, devices, trial, referral, billing rules
│       ├── persistence/              # PostgreSQL and in-memory repositories
│       ├── runtime/                  # polling, webhook (ASGI), raw HTTP runners
│       ├── security/                 # webhook policy, HMAC checkout, field encryption (AES-256-GCM)
│       ├── issuance/                 # VLESS provider (real 3x-ui + stub for tests)
│       ├── admin_support/            # ADM-01/ADM-02 internal admin endpoints
│       ├── web_api/                  # web-facing API: payment, auth, subscription, profile
│       ├── email/                    # SMTP email sender for verification codes
│       ├── observability/            # logging/telemetry hooks
│       └── shared/                   # shared types, correlation
│   └── tests/                        # pytest test suite
└── .cursor/plans/                    # historical planning files — read-only
```

---

## Source-of-truth priority

1. **Current local repo state** — actual files, HEAD code, git history
2. **GitHub remote and CI state** — branch/PR state, workflow runs, CI results
3. **Current code** — read actual source files before trusting any doc
4. **ADRs** — `docs/architecture/` for design decisions
5. **`.cursor/plans/`** — historical context only, read-only, HEAD always wins
6. **Old docs** — `RELEASE_STATUS.md`, `PROJECT_HANDOFF.md` may reference pre-production state

---

## Implemented features

### Telegram bot commands
- `/start` — register + auto-activate 3-day trial (or welcome for existing users)
- `/menu` — main menu with inline keyboard
- `/plans` — show available subscription plans
- `/buy` `/checkout` — purchase flow with plan/device selection and checkout URL
- `/success` — post-payment status check
- `/my_subscription` `/status` — subscription status with remaining days
- `/renew` — renewal with signed checkout URL
- `/support` `/support_contact` — FAQ and support contacts
- `/resend_access` `/get_access` — re-send access info (feature-gated)
- `/help` — command reference

### Telegram inline UI (storefront_ui.py)
- Main menu, buy VPN, plan selection, device count selector, payment confirmation
- My subscription, my keys (per-server view, all keys list), subscription URL
- Connect device flow (Windows/Android/iOS/macOS instructions)
- Referral program, balance display, settings (add/remove devices)
- Email linking flow, key reissue with confirmation
- Trial activation with instant VLESS key delivery

### VPN provider integration (real)
- `XuiVlessProvider` — real VLESS provider managing users across all active 3x-ui panels
- `XuiApiClient` — HTTP client for 3x-ui panel API (login, add/get/update/delete/disable/enable client)
- VLESS links use Reality TLS (`security=reality`, `pbk`, `sid`, `sni`, `flow=xtls-rprx-vision`)
- Subscription URL (`/sub/<token>`) for auto-import into VPN clients
- Server configs loaded from `vpn_servers` table, panel passwords encrypted (AES-256-GCM)
- VLESS UUIDs: random UUIDs stored per-user in `user_identities.vless_uuid`
- `StubVlessProvider` — fake provider for tests

### Database schema (30 migrations)
- `user_identities` — Telegram user mapping, VLESS UUID, subscription token, trial tracking
- `subscription_snapshots` — subscription state, plan, device count, trial/lifecycle timestamps
- `subscription_plans` — plan definitions
- `vpn_servers` — active VPN servers with panel credentials (encrypted passwords)
- `billing_events_ledger` — billing event audit trail
- `referral_codes`, `referral_relationships`, `referral_balances` — 2-level referral system
- `user_emails`, `email_verification_codes` — email linking with verification
- `notification_state`, `notification_log` — notification scheduler tracking
- `issuance_state` — VPN key issuance state
- Plus: idempotency, audit events, outbound delivery, telegram update dedup, access reconcile runs

### Security
- AES-256-GCM field encryption for panel passwords (`FIELD_ENCRYPTION_KEY`)
- HMAC-signed checkout references with TTL
- Webhook secret verification (fail-closed)
- Rate limiting per command per user
- Telegram update dedup
- `TELEGRAM_ACCESS_RESEND_ENABLE` feature flag (disabled by default)

### CI/CD
- `backend-mvp-release-readiness` — static release validation
- `backend-postgres-mvp-smoke-validation` — PostgreSQL integration tests
- Deploy workflow with Docker, nginx, SSL, post-deploy hardening, Xray restart, DB cleanup

---

## Historical context: `.cursor/plans/`

`.cursor/plans/` contains historical planning files from earlier design sessions. These are **read-only reference material**:
- Do **not** edit any file under `.cursor/plans/`.
- Do **not** treat `.cursor/plans/` content as an active backlog.
- If a `.cursor/plans/` file conflicts with HEAD code, **HEAD wins**.

---

## Main local commands

Run all commands from the `backend/` directory unless noted.

### Test suite
```bash
cd backend && python -m pytest -q
cd backend && python -m pytest -q tests/test_<name>.py
```

### Static validation
```bash
cd backend && python scripts/run_mvp_repo_release_health_check.py
cd backend && python scripts/run_mvp_final_static_handoff_check.py
cd backend && python scripts/run_mvp_release_readiness.py
```

### Integration tests (PostgreSQL required)
```bash
cd backend && python scripts/run_postgres_mvp_smoke_local.py
cd backend && python scripts/run_postgres_mvp_smoke.py
```

### Config / operator tools
```bash
cd backend && python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all
cd backend && python scripts/validate_release_candidate.py
cd backend && python scripts/configure_telegram_webhook.py --dry-run
```

### Retention
```bash
cd backend && python scripts/run_slice1_retention_dry_run.py
cd backend && python scripts/reconcile_expired_access.py  # destructive — requires operator approval
```

---

## Trunk Based Development workflow

This project follows **Trunk Based Development** (TBD). See `/deliver` skill for the full step-by-step cycle.

### Core TBD rules

- **Short-lived feature branches** — max 24 hours from creation to merge
- **One developer per branch** — one person owns one branch at a time
- **Small scope** — one task = one branch = one PR
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

### Reporting

Return a concise delivery report using the format below. Do not invent output, commit hashes, PR links, or CI status.

---

## GitHub and CI workflow

**Repository:** `rsidorenko/startprojectformillion` on GitHub (remote `origin`)

**Useful commands:**
```bash
gh pr create --title "..." --body "..."
gh pr view
gh run list --limit 10
gh run view <run-id>
gh run view <run-id> --log-failed
```

**CI rules:**
- Never disable workflows, remove CI checks, or alter branch protection
- Never push directly to `main`
- Never force-push
- A CI failure caused by this batch's changes must be fixed before reporting done
- A pre-existing CI failure must be reported with evidence
- `CLAUDE.md` at repo root does **not** trigger CI workflows

---

## Delivery report format

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

**Secret constraints:**
- Do not commit secrets; do not print secret values in logs, tests, or reports
- Do not open or print values from `.env` files or private key files
- Panel passwords must use AES-256-GCM encryption (`FIELD_ENCRYPTION_KEY`)
- Secret rotation is an explicit operator step; do not automate or bypass it

**VPN provider constraints:**
- Real 3x-ui integration is live — do not break user CRUD operations
- VLESS links must use Reality TLS (`security=reality`) with correct flow (`xtls-rprx-vision`)
- Device limits are enforced via `limitIp` in 3x-ui — respect this when adding/modifying users
- Key reissue must clear the stored VLESS UUID and revoke old keys on all panels before creating new ones
- Server config changes affect production — validate carefully

**Billing constraints:**
- UC-04 (ingestion) and UC-05 (subscription apply) are separate steps — do not short-circuit
- Balance payments deduct kopecks — always use `*_kopecks` fields for money arithmetic
- Referral commissions are idempotent by description — maintain this pattern

**Retention constraints:**
- Retention deletes require explicit operator opt-in (dry-run first, then delete-phase separately)
- Do not add a default-enabled delete path
- Key lifecycle: deactivate on expiry → delete after 20 days → do not skip stages

**Git / CI constraints:**
- Do not push directly to `main` or `master`
- Do not force-push any branch
- Do not disable CI workflows or remove CI checks
- Do not bypass tests with `--no-verify`
- Do not hide CI failures

**File constraints:**
- Do not edit any file under `.cursor/plans/`

---

## Billing / payment terminology

| Term | What it means | Trust level |
|---|---|---|
| **Operator billing ingest** (UC-04) | Pre-built normalized JSON via `billing_ingestion_main` | Trusted operator |
| **UC-05 subscription apply** | Separate apply step, not auto-chained after ingest | Internal, controlled |
| **Payment fulfillment ingress** | Signed HTTP path, provider-agnostic, feature-gated | Controlled ingress |
| **Balance payment** | Pay from referral balance, direct debit + credit | Internal |
| **Checkout reference** | HMAC-signed URL with TTL for payment provider redirect | User-facing |

---

## Key environment variables

| Variable | Purpose |
|---|---|
| `DATABASE_URL` | PostgreSQL connection string |
| `TELEGRAM_BOT_TOKEN` | Telegram Bot API token |
| `WEBHOOK_SECRET` | Telegram webhook verification |
| `FIELD_ENCRYPTION_KEY` | AES-256-GCM key (32 bytes, base64) for panel passwords |
| `CHECKOUT_REFERENCE_SECRET` | HMAC key for checkout URLs |
| `BOT_USERNAME` | Bot username for referral links |
| `TELEGRAM_ACCESS_RESEND_ENABLE` | Feature flag for access resend commands |
| `PAYMENT_FULFILLMENT_HTTP_ENABLE` | Feature flag for payment ingress |
| `SUBSCRIPTION_BASE_URL` / `NEXT_PUBLIC_SITE_URL` | Base URL for subscription links |

---

*Last updated: 2026-05-21. Maintained by: rsidorenko / Claude Code delivery batches.*
