# Admin/support internal read gate (ADM-01 / ADM-02)

Release go/no-go flow reference:
- `backend/docs/mvp_release_readiness_runbook.md`

## Purpose

**Advisory operator preflight:** fast, in-process checks that the internal Starlette bridges for **ADM-01** and **ADM-02** still enforce allowlists, principal extraction, and safe JSON shapes. Use after refactors touching `app.admin_support` or internal HTTP wiring.

This gate is **not** a substitute for **network boundary controls** on internal HTTP (private network, mTLS, sidecar identity, etc.) and **not** a replacement for production RBAC/transport policy.

For ADM-01 internal HTTP production boundary and deployment constraints, see the architecture decision record: [`docs/architecture/34-adm01-internal-http-production-boundary-adr.md`](../../docs/architecture/34-adm01-internal-http-production-boundary-adr.md).

## Shared-secret authentication (fail-closed in production)

Both ADM-01 and ADM-02 internal HTTP bridges gate every request with
`app.admin_support.internal_auth.verify_internal_admin_secret`, which compares the
`X-Admin-Secret` header to the `ADM_INTERNAL_SECRET` environment variable
(constant-time via `hmac.compare_digest`).

**Fail-closed by `APP_ENV`:**
- **Production** `APP_ENV` (any value outside `development`/`dev`/`local`/`test`):
  a missing/empty `ADM_INTERNAL_SECRET` **rejects every request with 401**.
  Authorization no longer falls back to "network isolation".
- **Local/test** `APP_ENV` (the default when `APP_ENV` is unset, matching
  `app.security.config`): the secret is optional — local-dev and the test suite
  keep working unchanged.

**Deploy requirement (breaking change):** deployments that previously reached
`/internal/admin/adm01/*` or `/internal/admin/adm02/*` without a secret must, on
this deploy:

1. set `ADM_INTERNAL_SECRET` to a strong random value in the production environment; and
2. send `X-Admin-Secret: <same value>` from every admin client.

Without both, admin endpoints return 401. Network-boundary controls (private
network, mTLS, trusted reverse proxy) remain required defense-in-depth and are
**not** a substitute for the shared secret.

## ADM-01 standalone internal HTTP entrypoint

Standalone ADM-01 process entrypoint:

```bash
cd backend
python -m app.internal_admin
```

Default behavior is **disabled** unless `ADM01_INTERNAL_HTTP_ENABLE` is truthy. In disabled mode the process prints:

```text
adm01_internal_http: disabled
```

and exits with code `0`.

Enabled mode requires runtime env plus ADM-01 HTTP env guards:

- `DATABASE_URL=<DATABASE_URL>`
- `BOT_TOKEN=<BOT_TOKEN>` (required when runtime config loading expects bot credentials)
- `ADM01_INTERNAL_HTTP_ENABLE=1`
- `ADM01_INTERNAL_HTTP_BIND_HOST=<bind-host>`
- `ADM01_INTERNAL_HTTP_BIND_PORT=<bind-port>`
- `ADM01_INTERNAL_HTTP_ALLOWLIST=<principal-id>,<principal-id>`
- `ADM01_INTERNAL_HTTP_TRUST_REVERSE_PROXY=1` (when transport trust is provided by a trusted reverse proxy)
- `ADM01_INTERNAL_HTTP_REQUIRE_MTLS=1` (when transport trust is provided by mTLS)
- `ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES=1` (explicit override for `0.0.0.0` / `::`)

Safety constraints:

- Default bind is loopback; do not expose ADM-01 HTTP on public interfaces by default.
- Do not use `0.0.0.0` / `::` unless explicit insecure-all-interfaces override is set and network controls are documented.
- Allowlist is defense-in-depth and does **not** replace transport trust (private network + trusted reverse proxy and/or mTLS).
- Do not log request bodies, provider references, DSN values, or token material.
- Do not expose this entrypoint to the public internet; run only behind private network controls as defined in ADR 34.

On enabled-mode failure, stderr categories are fixed and intentionally low-detail:

- `adm01_internal_http: config_error`
- `adm01_internal_http: failed`

## What it does / does not do

- **No external database:** does not read `DATABASE_URL`, does not open TCP to PostgreSQL, no Docker, no migrations.
- **No Telegram** or billing/config issuance paths.
- **ADM-02 success path:** uses the real `Adm02DiagnosticsHandler` stack with in-memory repositories; a successful diagnostics call may append a **fact-of-access record to the in-memory audit appender** (same handler semantics as in unit tests). Nothing is written to a real production database by this script.

## ADM-01 support readiness read model

Use the existing internal ADM-01 lookup path to triage Telegram access readiness for support/admin:

- Internal path: `POST /internal/admin/adm01/lookup` (same allowlist/internal principal gate as before).
- Input target: `telegram_user_id` (or `internal_user_id` where existing internal process already has it).
- Safe summary fields:
  - `telegram_identity_known`
  - `subscription_bucket` (`unknown|inactive|active|expired|cancelled`)
  - `access_readiness_bucket` (`not_applicable_no_active_subscription|active_access_not_ready|active_access_ready|unknown_due_to_internal_error`)
  - `recommended_next_action` (`ask_user_to_use_status|ask_user_to_use_get_access|investigate_billing_apply|investigate_issuance`)

Operator guidance:

- `ask_user_to_use_status`: confirm user-facing state via `/status`.
- `ask_user_to_use_get_access`: user should run `/get_access`.
- `investigate_billing_apply`: check billing ingestion/apply path.
- `investigate_issuance`: check issuance/readiness path.

Safety boundary:

- ADM-01 response is deterministic/minimal and does not return raw credentials, raw config, provider refs, billing refs, idempotency keys, DB ids, or internal user ids.
- `/status` and `/get_access` remain the user-facing commands.
- Canonical PostgreSQL MVP access fulfillment smoke also verifies ADM-01 readiness alignment with user-facing `/status` before and after fake issuance.

## ADM-02 support remediation (ensure access)

- ADM-01 remains read-only diagnostics.
- ADM-02 ensure-access is internal remediation for cases where identity is known, subscription is active, and access is not ready.
- Expected safe outcomes are bounded (`noop_identity_unknown`, `noop_no_active_subscription`, `noop_access_already_ready`, `issued_access`, `failed_safe`) and deterministic.
- ADM-02 ensure-access mutation attempts emit safe internal audit evidence with bounded buckets:
  - `denied_unauthorized`
  - `denied_mutation_opt_in_disabled`
  - `noop_identity_unknown`
  - `noop_no_active_subscription`
  - `noop_access_already_ready`
  - `issued_access`
  - `failed_safe`
- In internal admin runtime, ADM-02 ensure-access now writes redacted audit events to
  durable PostgreSQL storage (`adm02_ensure_access_audit_events`) and also emits structured logs
  as observability signal/fallback.
- Mutation path is fail-closed:
  - allowlist gate must pass for ADM-02 capability;
  - explicit mutation opt-in must be enabled by composition/runtime (no default global enablement);
  - runtime gate is env/config-driven via `ADM02_ENSURE_ACCESS_ENABLE` (`1|true|yes` after `strip().lower()`).
- Missing/falsey `ADM02_ENSURE_ACCESS_ENABLE` keeps ADM-02 ensure-access mutation disabled while ADM-01 diagnostics remain available.
- ADM-02 durable ensure-access audit evidence is available on internal admin read path:
  - lookup by exact `evidence_correlation_id`;
  - recent bounded list via `limit` (safe default and hard max enforced).
- Audit evidence read path is internal/admin gated and strictly read-only.
- Mutation opt-in (`ADM02_ENSURE_ACCESS_ENABLE`) is still only for ensure-access remediation route, not for audit evidence readback.
- Remediation is idempotent: repeated ensure-access calls for the same active user must not create duplicate issuance state.
- No raw credentials/config/provider refs/billing refs/idempotency keys/internal user IDs are returned.
- Ensure-access audit evidence is redacted and bounded; it does not include raw credentials/config/provider refs/billing refs/internal ids/idempotency keys.
- Runtime audit events intentionally omit raw user identifiers, internal user ids, provider/billing refs,
  credentials, DSN, and request/response payloads.
- Audit readback returns only bounded redacted fields from durable storage:
  `created_at`, `event_type`, `outcome_bucket`, `remediation_result`, `readiness_bucket`,
  `principal_marker`, `correlation_id`, optional `source_marker`.
- Audit readback does not return raw credentials/config material, provider refs, billing refs,
  internal user ids, DB ids, idempotency keys, tokens, or DSN.
- Durable ADM-02 audit table stores bounded fields only:
  - `event_type`
  - `outcome_bucket` (`denied_unauthorized|denied_mutation_opt_in_disabled|noop_identity_unknown|noop_no_active_subscription|noop_access_already_ready|issued_access|failed_safe|dependency_failure|invalid_input`)
  - `remediation_result`
  - `readiness_bucket`
  - `principal_marker`
  - `correlation_id`
  - optional `source_marker`
- Durable ADM-02 audit storage does not store raw credentials/config material, provider refs, billing refs,
  DB ids, internal user ids, idempotency keys, tokens, DSN, or request/response payloads.
- Retention/export: follow existing Postgres retention/export operational process; ADM-02 audit rows are
  bounded operational evidence and should be handled with the same internal redaction/export controls.
- Operational retention cleanup now includes `adm02_ensure_access_audit_events` age-based retention with
  configurable `ADM02_AUDIT_RETENTION_DAYS` (default conservative value) and dry-run-first behavior.
- Deletion is opt-in only via `OPERATIONAL_RETENTION_DELETE_ENABLE` (`1|true|yes` after `strip().lower()`).
  Without this explicit flag, operational retention runs counts-only and does not delete ADM-02 audit rows.
- User-facing commands remain `/status` and `/get_access`; support remediation does not expose new public bot commands.
- Canonical PostgreSQL MVP access fulfillment smoke also exercises ADM-02 remediation when explicitly enabled in isolated smoke child env (`ADM02_ENSURE_ACCESS_ENABLE=1`), without enabling ADM-02 globally.
- Operator troubleshooting flow: use ADM-01 for diagnostics, ADM-02 for gated remediation, and ADM-02 audit evidence for outcome traceability.
- Canonical PostgreSQL MVP access fulfillment smoke validates ADM-02 audit evidence for both
  `issued_access` and idempotent `noop_access_already_ready` outcomes.
- Canonical PostgreSQL MVP access fulfillment smoke now verifies durable ADM-02 audit
  persistence/readback via internal read-only audit lookup scoped by smoke correlation id.

## Prerequisites

- Run from the `backend` directory (same as other scripts).
- Install the backend package with test dependencies so `httpx` and app imports resolve (`pip install -e .[test]`).

## Run

```bash
cd backend
python scripts/check_admin_support_internal_read_gate.py
```

### ADM-01 entrypoint smoke

```bash
cd backend
python scripts/check_adm01_internal_http_entrypoint_smoke.py
```

This smoke verifies only disabled/config-error behavior for `python -m app.internal_admin`
and is intentionally no-listener/no-DB; it does not prove production network safety.
In CI (`backend-postgres-mvp-smoke-validation`) it is a **blocking gate**: a failed run fails the job (still not a production network or transport guarantee).

Expected stdout on success (single line):

```text
admin_support_internal_read_gate: ok
```

On failure, the process exits non-zero and prints **exactly one fixed line** to stderr (no tracebacks, no exception messages, no request/response dumps):

- `admin_support_internal_read_gate: fail` — gate checks reported an expected violation (e.g. assertion-style `RuntimeError` from the check module).
- `admin_support_internal_read_gate: failed` — unexpected error inside the gate run (any other exception); details are intentionally suppressed so stderr cannot echo secrets from exception text.

## Safety notes

- Keep internal HTTP listeners on private networks only; this script does not replace transport-level controls (mTLS, VPC, etc.).
- Do not paste real `DATABASE_URL`, bot tokens, or customer identifiers into tickets, shell history, or issue reports when sharing script output.

## CI

In workflow `backend-postgres-mvp-smoke-validation` (after backend dependencies install, before blocking PostgreSQL MVP smoke gates): the **admin support internal read gate** still runs as **advisory evidence**; failures are written to `backend-admin-support-internal-read-gate-summary.txt` (`internal_read_gate_outcome=success|failure|unknown` only) and **do not block** the smoke helper regression or the real local isolated PostgreSQL MVP smoke gates.

The **ADM-01 internal HTTP entrypoint smoke** (`check_adm01_internal_http_entrypoint_smoke.py`) runs as a **blocking gate** on the same job; outcome is recorded in `backend-adm01-internal-http-entrypoint-smoke-summary.txt` (`adm01_entrypoint_smoke_outcome=success|failure|unknown`). A failure **blocks** the job (artifact upload still runs on `always()` steps where configured).

## Current delivery checkpoint

Documentation-only marker tying this runbook to **published CI artifacts** on a validated workflow run (no new runtime guarantees).

- **Branch / commit:** `main` @ `9dcd6b6` (full SHA `9dcd6b6eddf43e62c161435e92df770eb4135fb0`).
- **Workflow:** `backend-postgres-mvp-smoke-validation`; **run id** `25110893372`; **conclusion** `success` (jobs `slice1-postgres-mvp-smoke` and `slice1-postgres-retention-integration` succeeded).
- **ADM-01 internal HTTP entrypoint smoke** is a **blocking gate** on `slice1-postgres-mvp-smoke`; it checks only disabled/config-error paths with **no listener** and **no DB writes**, and **does not** prove production network safety. Artifact `backend-postgres-mvp-smoke-validation-reports` includes `backend-adm01-internal-http-entrypoint-smoke-summary.txt` with `adm01_entrypoint_smoke_outcome=success` on this run.
- **Admin support internal read gate** (and full backend regression on that job) remain **advisory** as in **CI** above; the same artifact bundle includes `backend-admin-support-internal-read-gate-summary.txt` with `internal_read_gate_outcome=success` on this run.
- **Reminder:** advisory checks and blocking entrypoint smoke are **not** substitutes for production network boundary, transport policy, or RBAC controls.

Local command remains:

```bash
cd backend
python scripts/check_admin_support_internal_read_gate.py
```

## Automation

Pytest coverage: `tests/test_internal_read_gate_checks.py` and `tests/test_run_admin_support_internal_read_gate.py`.
