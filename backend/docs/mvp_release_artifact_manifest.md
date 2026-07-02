# MVP Release Artifact Manifest

Short operator/reviewer checklist for MVP release package completeness.

## Release readiness commands
- `python scripts/run_mvp_release_readiness.py`
- Default local/CI baseline static check: `python scripts/run_mvp_repo_release_health_check.py`
- Final static handoff-only check before transfer: `python scripts/run_mvp_final_static_handoff_check.py` (static/handoff-only; does not run Docker/DB/runtime)
- `python scripts/run_mvp_release_checklist.py`
- `python scripts/run_mvp_release_preflight.py`
- `python scripts/run_mvp_config_doctor.py --profile polling|webhook|internal-admin|retention|all`
- `python scripts/run_postgres_mvp_smoke_local.py`

The static repository health check is read-only, does not run tests, Docker, DB, or network calls, and complements checklist/preflight/config doctor.
The final static handoff check is static/handoff-only and does not replace readiness/preflight/config doctor/local smoke.
The optional handoff summary command is read-only and informational; it does not replace readiness/preflight/config doctor/local smoke.
Release helper scripts are additionally covered by a bounded-output safety contract test.
Final MVP release package completeness is covered by `tests/test_mvp_release_package_complete_contract.py`.
Final release gate contract target: `tests/test_mvp_final_release_gate_contract.py`.
Workflow structure contract target: `tests/test_mvp_release_readiness_workflow_structure_contract.py`.

## Canonical smoke commands
- `python scripts/run_postgres_mvp_smoke.py`
- Local Docker wrapper command remains separate: `python scripts/run_postgres_mvp_smoke_local.py`

## CI gates
- `backend-mvp-release-readiness`: static repo health check + static checklist + targeted preflight contracts + config doctor unit tests.
  - Trigger scope includes backend release/handoff docs/scripts/tests.
- `backend-postgres-mvp-smoke-validation`: canonical PostgreSQL smoke gate and PostgreSQL integration evidence lane.

## Runtime surfaces
- Telegram polling runtime lane.
- Telegram webhook ASGI entrypoint.
- `/healthz` liveness endpoint.
- `/readyz` readiness endpoint.
- ADM-01 diagnostics surface.
- ADM-02 ensure-access remediation path.
- ADM-02 audit readback path.

## Operator env template
- `backend/.env.example` — committed safe template with placeholders only.
- Copy to `backend/.env`, fill real staging/test values, load into shell.
- `backend/.env` is local-only and must not be committed.

## Security toggles (names only)
- `TELEGRAM_WEBHOOK_SECRET_TOKEN`
- `TELEGRAM_WEBHOOK_ALLOW_INSECURE_LOCAL`
- `TELEGRAM_WEBHOOK_HTTP_ENABLE`
- `TELEGRAM_WEBHOOK_PUBLIC_URL`
- `TELEGRAM_WEBHOOK_ALLOWED_UPDATES`
- `ADM02_ENSURE_ACCESS_ENABLE`
- `OPERATIONAL_RETENTION_DELETE_ENABLE`
- `ADM02_AUDIT_RETENTION_DAYS`
- `ISSUANCE_OPERATOR_ENABLE`
- `TELEGRAM_ACCESS_RESEND_ENABLE`
- `PAYMENT_FULFILLMENT_HTTP_ENABLE`
- `PAYMENT_FULFILLMENT_WEBHOOK_SECRET`
- `TELEGRAM_CHECKOUT_REFERENCE_SECRET`
- `TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS`
- `ACCESS_RECONCILE_SCHEDULE_ACK`
- `ACCESS_RECONCILE_MAX_INTERVAL_SECONDS`
- `SUBSCRIPTION_DEFAULT_PERIOD_DAYS`
- `DATABASE_URL`
- `BOT_TOKEN`

## Known manual gates
- Real operator config doctor run with actual environment.
- Local Docker smoke execution.
- Live deployment `/healthz` and `/readyz` verification.
- Telegram webhook secret rotation and `setWebhook` operations.
- Retention delete approval gate before enabling delete path.

## Cross-reference
- Readiness runbook: `backend/docs/mvp_release_readiness_runbook.md`
- Operator env bootstrap: `backend/docs/operator_environment_validation_bootstrap.md`

## Explicit out-of-scope
- public billing ingress
- real provider SDK
- raw credential/config delivery
- full production SLO/alerting certification
