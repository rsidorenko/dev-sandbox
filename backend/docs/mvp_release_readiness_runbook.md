# MVP Release Readiness Runbook

## Purpose
This runbook defines a single MVP release readiness go/no-go flow for targeted code contracts, runtime config
readiness, and local integration confidence checks.

This is **not** full production certification. It does not replace production SLO policy, transport controls,
or external observability platform validation.

## Go / no-go sequence
Run from `backend` with operator-safe handling of environment values (do not print raw values in logs/history).

Final blocking operator gate for customer-facing launch readiness:

`python scripts/validate_release_candidate.py`

This command is the release-candidate go/no-go boundary. Exit code `0` means all required checks passed.
Any non-zero exit code means launch is blocked until the failed check is remediated and the validator passes.

CI evidence location for this final gate:
- workflow: `.github/workflows/backend-postgres-mvp-smoke-validation.yml`
- job: `slice1-postgres-mvp-smoke`
- step: `Run release candidate validator (blocking final gate)`

CI failure interpretation:
- if this CI step fails, release-candidate evidence is not satisfied and launch remains blocked;
- rerun the same command locally with operator env (`python scripts/validate_release_candidate.py`) to reproduce and remediate;
- CI evidence is a test-safe isolated proof, local/operator run remains the operational go/no-go execution.
- this CI gate does not call Telegram Bot API `setWebhook`; webhook apply remains explicit operator action only.

Static repo sanity check (read-only):

`python scripts/run_mvp_repo_release_health_check.py`

This check validates release artifacts/docs/workflow references and safety markers only. It is read-only and does not run tests, Docker, DB, or network calls.

Static release checklist:

`python scripts/run_mvp_release_checklist.py`

This checklist validates artifact/doc presence and required documentation markers only. It does not replace preflight, config doctor, local Docker smoke, or live readiness checks.

Lightweight CI baseline (`backend-mvp-release-readiness`) runs only:
- `python scripts/run_mvp_repo_release_health_check.py`
- `python scripts/run_mvp_release_checklist.py`
- `python scripts/run_mvp_release_preflight.py`
- `python scripts/run_mvp_final_static_handoff_check.py`
- `python -m pytest -q tests/test_run_mvp_config_doctor.py`

This CI lane is intentionally static/contracts-only and does not require real operator environment values.
It is triggered by release/handoff docs/scripts/tests updates in `backend`.

Recommended local/operator wrapper command for safe default release readiness path:

`python scripts/run_mvp_release_readiness.py`

By default this wrapper runs repo release health check + checklist + preflight, and does not run config doctor automatically.

Optional config doctor profile through wrapper:
- `python scripts/run_mvp_release_readiness.py --config-profile polling`
- `python scripts/run_mvp_release_readiness.py --config-profile webhook`
- `python scripts/run_mvp_release_readiness.py --config-profile internal-admin`
- `python scripts/run_mvp_release_readiness.py --config-profile retention`
- `python scripts/run_mvp_release_readiness.py --config-profile all`

Config doctor with real operator env remains optional/manual through this wrapper and is not part of
lightweight CI release readiness gate.

1. Run targeted code-contract preflight:

   `python scripts/run_mvp_release_preflight.py`

2. Run config doctor profiles in real operator env:
   - polling or webhook runtime profile:
     - `python scripts/run_mvp_config_doctor.py --profile polling`
     - or `python scripts/run_mvp_config_doctor.py --profile webhook`
   - internal admin profile:
     - `python scripts/run_mvp_config_doctor.py --profile internal-admin`
   - retention profile:
     - `python scripts/run_mvp_config_doctor.py --profile retention`

`--profile all` remains a manual/operator go/no-go check when real environment configuration is available;
it is not a required CI gate in lightweight release readiness workflow.

3. Run local Docker smoke when local Docker/PostgreSQL is available:

   `python scripts/run_postgres_mvp_smoke_local.py`

4. If webhook profile is used, after deploy verify:
   - `GET /healthz` returns healthy liveness response;
   - `GET /readyz` returns ready status when dependencies are healthy.

5. Validate ADM readiness and policy:
   - ADM-01 diagnostics path is available for safe support triage;
   - ADM-02 mutation opt-in remains disabled unless explicitly required for remediation.

6. Confirm retention posture:
   - dry-run path first;
   - retention delete opt-in enabled only for approved cleanup windows.

## Expected safe outputs
- Preflight success line: `mvp_release_preflight: ok`
- Launch preflight success line: `launch_readiness_preflight: ok`
- Config doctor success line: `mvp_config_doctor: ok`
- Local smoke success: subprocess chain exits `0` with no failures
- Release candidate validator success line: `release_candidate_validation: ok`
- Release candidate validator failure line: `release_candidate_validation: failed`
- Release candidate validator per-check markers: `check=<name> status=pass|fail`

## Release candidate validation gate v1
Run from `backend`:

`python scripts/validate_release_candidate.py`

Blocking check order:
1. `python scripts/run_mvp_release_preflight.py` (includes migration readiness contract checks)
2. `python scripts/check_launch_readiness.py --strict`
3. `python scripts/configure_telegram_webhook.py --dry-run` (validates webhook URL, secret presence, and `TELEGRAM_WEBHOOK_ALLOWED_UPDATES` policy; no Telegram network calls)
4. `python scripts/run_postgres_mvp_smoke.py` (canonical blocking smoke gate)
5. `python scripts/check_reconcile_health.py`

The release validator never runs `configure_telegram_webhook.py --verify` or `--apply`; those are explicit operator network actions outside CI.

Required environment checklist for strict release-candidate validation:
- storefront checkout/support and storefront plan markers (or explicit strict fallback acknowledgements)
- payment fulfillment secret (`PAYMENT_FULFILLMENT_WEBHOOK_SECRET`)
- checkout reference signing secret + TTL (`TELEGRAM_CHECKOUT_REFERENCE_SECRET`, `TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS` or default TTL acknowledge marker)
- subscription lifecycle period marker (`SUBSCRIPTION_DEFAULT_PERIOD_DAYS`)
- Telegram webhook secret when webhook mode is enabled (`TELEGRAM_WEBHOOK_SECRET_TOKEN`)
- Telegram webhook public HTTPS URL when webhook mode is enabled (`TELEGRAM_WEBHOOK_PUBLIC_URL`)
- optional webhook update filter: `TELEGRAM_WEBHOOK_ALLOWED_UPDATES` (comma-separated; default when unset is `message` only for this command-only bot surface)
- access reconcile schedule acknowledgement + interval (`ACCESS_RECONCILE_SCHEDULE_ACK`, `ACCESS_RECONCILE_MAX_INTERVAL_SECONDS`)
- database URL marker (`DATABASE_URL`) for DB-backed checks

Failure interpretation:
- `check=<name> status=fail` means that check is a launch-blocker.
- `release_candidate_validation: failed` means no-go for launch.
- Remediate failed check and rerun the exact command.

Provider integration boundary:
- Provider-specific checkout/payment SDK integration stays outside this validator.
- The validator only runs existing internal safety checks and does not introduce provider SDK calls.

## Launch readiness preflight v1
Run from `backend` and pass environment through secure operator tooling only.

Default/local mode (allows safe fallbacks with warnings):

`python scripts/check_launch_readiness.py`

Strict launch mode (customer-facing launch gate):

`python scripts/check_launch_readiness.py --strict`

Alternative strict flag through env:

`LAUNCH_PREFLIGHT_STRICT=1 python scripts/check_launch_readiness.py`

Required environment keys in strict mode:
- `BOT_TOKEN`
- `DATABASE_URL`
- `TELEGRAM_STOREFRONT_CHECKOUT_URL`
- `TELEGRAM_STOREFRONT_PLAN_NAME` or explicit fallback acknowledge `TELEGRAM_STOREFRONT_ALLOW_PLAN_FALLBACK=1`
- `TELEGRAM_STOREFRONT_PLAN_PRICE` or explicit fallback acknowledge `TELEGRAM_STOREFRONT_ALLOW_PLAN_FALLBACK=1`
- `TELEGRAM_STOREFRONT_SUPPORT_URL` or `TELEGRAM_STOREFRONT_SUPPORT_HANDLE` or explicit fallback acknowledge `TELEGRAM_STOREFRONT_ALLOW_SUPPORT_FALLBACK=1`
- `PAYMENT_FULFILLMENT_HTTP_ENABLE=1`
- `PAYMENT_FULFILLMENT_WEBHOOK_SECRET`
- `TELEGRAM_CHECKOUT_REFERENCE_SECRET`
- `TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS` (recommended production: `86400..604800`; strict hard bounds: `600..2592000`)
- optional but recommended lifecycle default: `SUBSCRIPTION_DEFAULT_PERIOD_DAYS` (`1..3660`)
- if using default TTL in strict mode: `TELEGRAM_CHECKOUT_REFERENCE_DEFAULT_TTL_ACCEPTED=1`
- `TELEGRAM_ACCESS_RESEND_ENABLE=1`
- `ACCESS_RECONCILE_SCHEDULE_ACK=1` (explicit operator acknowledgment that periodic reconcile scheduling exists in production)
- `ACCESS_RECONCILE_MAX_INTERVAL_SECONDS` within safe bounds `300..86400`
- if `TELEGRAM_WEBHOOK_HTTP_ENABLE=1`, also `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- if `TELEGRAM_WEBHOOK_HTTP_ENABLE=1`, also `TELEGRAM_WEBHOOK_PUBLIC_URL` (public HTTPS URL only; no localhost/private/test host in strict launch gate)
- if `TELEGRAM_WEBHOOK_ALLOWED_UPDATES` is set in strict mode, it must list only update types supported for the current command-only bot (today: `message` only)
- if webhook is used in strict/production-like mode, enable durable repos: `SLICE1_USE_POSTGRES_REPOS=1`

Expected safe output markers:
- `mode=default` or `mode=strict`
- `database=<redacted_dsn>`
- `checkout=https://<host>/<redacted>`
- `support=https://<host>/<redacted>` or `support=<missing>`
- `warn_code=<...>` and `issue_code=<...>` markers only (no secret values)
- `checkout_reference_ttl_seconds=<int>`
- `checkout_reference_ttl_classification=recommended|too_small|too_large|invalid`
- `access_reconcile_schedule_ack=acknowledged|missing`
- `access_reconcile_max_interval_seconds=<int|<missing>>`
- `access_reconcile_interval_classification=recommended|too_small|too_large|not_set|invalid`
- `access_reconcile_operator_command=python scripts/reconcile_expired_access.py`
- `subscription_default_period_days=<int|<missing>>`
- `subscription_default_period_classification=recommended|too_small|too_large|not_set`
- when webhook HTTP is enabled: `telegram_webhook_allowed_updates_items=<comma-separated names>`

Must never appear in logs/output:
- raw bot token value
- webhook secret values (`TELEGRAM_WEBHOOK_SECRET_TOKEN`, `PAYMENT_FULFILLMENT_WEBHOOK_SECRET`)
- checkout reference signing secret values (`TELEGRAM_CHECKOUT_REFERENCE_SECRET`)
- DSN credentials or full DSN with username/password
- checkout/support URL query parameters
- raw signature/access credential values

Reconcile scheduling launch contract interpretation:
- strict mode fails closed when reconcile schedule acknowledgment is absent;
- strict mode fails closed when `ACCESS_RECONCILE_MAX_INTERVAL_SECONDS` is missing/invalid/out of bounds;
- default/local mode warns for missing scheduling markers but does not fail launch preflight.
- runtime freshness evidence is validated separately with DB-backed read-only check:
  `python scripts/check_reconcile_health.py`.

Recommended production cadence:
- schedule `python scripts/reconcile_expired_access.py` at least every 15 minutes (`900` seconds) or faster;
- keep `ACCESS_RECONCILE_MAX_INTERVAL_SECONDS` aligned with configured scheduler cadence and never above `86400`.
- treat `check_reconcile_health.py` as operator go/no-go freshness check:
  - missing heartbeat: reconcile has not produced durable evidence;
  - stale heartbeat: schedule is not meeting configured interval;
  - last run failed: reconcile execution is unhealthy and needs remediation.

Webhook secret operational notes:
- Configure `TELEGRAM_WEBHOOK_SECRET_TOKEN` via secure env injection only (never inline in logs/history/tickets).
- Configure Telegram webhook with matching secret token through Bot API `setWebhook` using secure tooling only.
- In strict launch mode (`--strict` or `LAUNCH_PREFLIGHT_STRICT=1`), webhook HTTP path requires present + minimally strong secret.
- If webhook mode is disabled (`TELEGRAM_WEBHOOK_HTTP_ENABLE` falsey), polling/local path remains valid without webhook secret.

Webhook setup + verification operator commands (`backend`):
- dry-run config validation (default, no network): `python scripts/configure_telegram_webhook.py`
- explicit apply (calls Telegram Bot API `setWebhook`): `python scripts/configure_telegram_webhook.py --apply`
- read-only semantic verify (`getWebhookInfo` vs expected env): `python scripts/configure_telegram_webhook.py --verify`
- optional explicit delete (`deleteWebhook`): `python scripts/configure_telegram_webhook.py --delete`

Semantic verify behavior (`--verify`):
- compares Telegram `url` to `TELEGRAM_WEBHOOK_PUBLIC_URL` after safe normalization (trailing slash differences are ignored; query strings are not accepted on the expected URL in strict policy);
- compares `allowed_updates` when Telegram returns a list; if Telegram omits the field, prints `allowed_updates_match=unknown` (cannot prove policy match from API);
- prints `secret_token_status_match=unknown` because Telegram does not echo the configured `secret_token` in `getWebhookInfo`; treat local `TELEGRAM_WEBHOOK_SECRET_TOKEN` presence plus runtime ingress tests as the practical secret gate;
- prints `pending_update_count=<n>` as a safe backlog marker;
- fails with `reason=telegram_webhook_verify_last_error_present` when Telegram reports a recent error slot (`last_error_message` / `last_error_date` present); raw error text is never printed.

Webhook setup failure interpretation:
- `telegram_webhook_configure: failed` means no-go for webhook apply/verification until env/URL policy is fixed.
- safe output markers only are expected: action, host/path class markers, configured yes/no markers.
- never print raw `BOT_TOKEN`, `TELEGRAM_WEBHOOK_SECRET_TOKEN`, or full webhook URL query parameters.

Safe failure interpretation for webhook ingress:
- `401` from `/telegram/webhook` means secret header missing/invalid and request is rejected before update parsing/dispatch.
- `400` with `{"ok": false, "error": "invalid_update_id"}` means webhook payload was rejected fail-closed before dispatch (missing/malformed `update_id`).
- duplicate mutating updates (`/get_access`, `/resend_access`, `/success`) are accepted as safe no-op (`200 {"ok": true}`) and do not re-dispatch mutation.
- `403` from edge/proxy/WAF in front of webhook should be treated as upstream boundary rejection before app ingress; verify proxy secret/header forwarding.
- For both `401/403`, inspect deployment/config mismatch first (webhook secret, header forwarding, setWebhook config), not business logic.

Targeted verification commands (from `backend`):
- `python -m pytest -q tests/test_telegram_webhook_ingress.py tests/test_telegram_webhook_main.py tests/test_launch_readiness_preflight.py tests/test_runtime_polling.py tests/test_runtime_telegram_httpx_raw_client.py tests/test_payment_fulfillment_ingress.py`
- `python scripts/check_launch_readiness.py --strict`

## Go criteria
- Preflight returns `mvp_release_preflight: ok`
- Required config-doctor profiles return `mvp_config_doctor: ok`
- Local Docker smoke succeeds when that gate is available in the release lane
- Webhook `healthz/readyz` checks are healthy when webhook runtime is enabled
- ADM-02 mutation remains explicitly gated (disabled by default unless approved)
- Retention delete path remains disabled unless approved cleanup is active

## No-go criteria
- Any required command exits non-zero
- Preflight returns `mvp_release_preflight: fail`
- Config doctor returns `mvp_config_doctor: fail` for required profile(s)
- Webhook runtime used but `readyz` is not ready after deploy
- ADM policy deviates from expected gate behavior (unexpected mutation enablement)
- Retention delete opt-in enabled without approved cleanup scope

## Security checklist
- No raw secrets/tokens/DSN values in logs, tickets, or CI summaries
- Webhook secret is configured for production-like webhook runtime
- `TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL` is off outside local/test
- `ADM02_ENSURE_ACCESS_ENABLE` is off unless remediation is explicitly required
- `OPERATIONAL_RETENTION_DELETE_ENABLE` stays off unless approved cleanup is active
- `DATABASE_URL` always points to intended isolated/dev/stage target for smoke paths; never run smoke against shared/production DB by accident

## Payment fulfillment ingress v1 (provider-agnostic)
- Enable only when DB and secret are configured:
  - `PAYMENT_FULFILLMENT_HTTP_ENABLE=1`
  - `PAYMENT_FULFILLMENT_WEBHOOK_SECRET=<shared secret>`
  - `TELEGRAM_CHECKOUT_REFERENCE_SECRET=<hmac checkout reference secret>`
  - optional: `PAYMENT_FULFILLMENT_PROVIDER_KEY=provider_agnostic_v1`
  - optional: `PAYMENT_FULFILLMENT_MAX_AGE_SECONDS=300`
- Safe payload shape (signed JSON body, no provider SDK fields required):
  - version marker field (must be current supported version)
  - `external_event_id`
  - `external_payment_id`
  - `telegram_user_id`
  - `client_reference_id` (base64url payload generated by `/buy` or `/checkout`)
  - `client_reference_proof` (HMAC SHA-256 proof for `client_reference_id`)
  - `paid_at` (ISO-8601 with timezone)
- Provider metadata requirement:
  - preserve checkout metadata from provider callback into ingress event as `client_reference_id` and `client_reference_proof`
  - equivalent nested metadata object is supported if these fields are passed through unchanged
- Signature contract:
  - headers: `x-payment-timestamp`, `x-payment-signature`
  - signature format: `sha256=<hex(hmac_sha256(secret, "<timestamp>.<raw_body>"))>`
  - stale/missing/invalid signatures are rejected fail-closed and do not mutate state.
- checkout reference replay window:
  - signed checkout reference includes `issued_at`;
  - ingress validates `issued_at` TTL using server-side time;
  - expired or too-far-future references are rejected fail-closed (no billing/subscription/access mutation).
- Targeted verification:
  - `python -m pytest -q tests/test_payment_fulfillment_ingress.py tests/test_run_customer_journey_e2e.py tests/test_launch_readiness_preflight.py tests/test_bot_transport_storefront_config.py`

## Known out-of-scope
- public billing ingress provider SDK implementation details
- provider-specific billing SDK ingress
- real provider SDK or real credential/config delivery
- full production SLO/alerting validation
- external log pipeline validation
- full Docker/live provider end-to-end certification

## Incident rollback / safe disable basics
- Webhook incidents:
  - disable webhook HTTP runtime flag, and/or
  - remove Telegram webhook operationally (`setWebhook` rollback) and keep polling path separate.
- Admin remediation incidents:
  - disable ADM-02 mutation opt-in (`ADM02_ENSURE_ACCESS_ENABLE` falsey).
- Polling runtime remains a separate operational lane and can continue independently of webhook mode.

## Cross-references
- Release package manifest: `backend/docs/mvp_release_artifact_manifest.md`
- Smoke and preflight details: `backend/docs/postgres_mvp_smoke_runbook.md`
- Telegram access + webhook behavior: `backend/docs/telegram_access_resend_runbook.md`
- ADM-01/ADM-02 internal gate details: `backend/docs/admin_support_internal_read_gate_runbook.md`
