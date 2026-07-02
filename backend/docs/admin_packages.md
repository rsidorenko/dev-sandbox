# Admin support packages: `admin_support/` vs `internal_admin/`

Two packages cooperate to serve the internal admin HTTP surface (ADM-01 user lookup, ADM-02 ensure-access
mutation). They are **not duplicates** — the split is an entrypoint/domain seam:

## `admin_support/` — domain logic & contracts
The substantive layer: request/response contracts, endpoint orchestration, adapters (postgres read/write),
authorization, audit sinks, the shared-secret auth helper (`internal_auth.py`), and wiring.
- `adm01_endpoint.py`, `adm02_endpoint.py` — endpoint orchestration
- `adm01_internal_http.py`, `adm02_internal_http.py` — thin Starlette ingress (JSON → endpoint → JSON),
  gated by `verify_internal_admin_secret`
- `internal_auth.py` — shared-secret auth (fail-closed in production `APP_ENV`)
- `authorization.py`, `principal_extraction.py`, `contracts.py`

## `internal_admin/` — process entrypoints & wiring bundles
The runnable shims that compose a `admin_support` ingress app with config/env and expose a `__main__`:
- `adm01_http_main.py`, `adm02_http_main.py` — start the Starlette app
- `adm01_http_config.py`, `adm01_bundle.py`, `adm02_bundle.py`, `adm02_mutation_opt_in_config.py`
- `__main__.py`

## Guidance
- New **logic/contracts/adapters** → `admin_support/`.
- New **runnable composition / env wiring / process bootstrap** → `internal_admin/`.
- The HTTP ingress (`adm0X_internal_http.py`) stays in `admin_support/` so it can be unit-tested in
  isolation from process bootstrap; `internal_admin` only assembles and runs it.

This boundary is load-bearing for testability (the ingress is tested without spawning a process). If the
two packages grow confusing again, prefer consolidating `internal_admin/` shims *into* `admin_support/`
rather than duplicating logic across both.
