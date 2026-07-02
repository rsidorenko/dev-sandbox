# Migration safety conventions

Forward-only SQL migrations are applied by `app.persistence.postgres_migrations.apply_postgres_migrations`,
which records each file in `schema_migration_ledger` **only after its SQL runs successfully**, and
**refuses to apply a file whose checksum no longer matches the ledger** (drift → `MigrationLedgerDriftError`).
Two operational consequences drive these conventions:

1. A migration that errors stays un-recorded and is retried on every deploy until it succeeds — a single
   failing migration blocks the whole deploy.
2. An already-applied migration **cannot be edited** (checksum drift breaks the deploy). Fix forward by
   appending a new migration; never rewrite a shipped one.

## Rules for new migrations

- **Idempotent where possible.** Prefer `IF NOT EXISTS` / `IF EXISTS` / `WHERE NOT EXISTS` so a migration
  is safe to reason about even if partially applied or re-run after a ledger reset.
- **Unique indexes on existing data: dedup first.** Before `CREATE UNIQUE INDEX`, delete duplicate rows
  that would violate it (see `054_referral_tx_dedup_guard.sql` as the companion to `048`). Otherwise the
  index creation errors on pre-existing duplicates and blocks deploys.
- **Destructive changes (`DROP`/`DELETE`): guard them.**
  - Document *why* it is safe in a leading comment (FK references checked, rows decommissioned, etc.).
  - Prefer a row-count assertion when the expected impact is known (e.g. `SELECT count(*) ...` logged by
    the operator) so a surprise is caught before the destructive step.
  - Keep the change reversible-in-spirit: snapshot/backup the table first; destructive migrations are
    one-way.
- **Anchored string rewrites.** When rewriting labels/identifiers with `REPLACE(...)`, scope it tightly
  (`WHERE ... LIKE '<anchored pattern>'`) and prefer an exact-match `UPDATE ... WHERE label = '<literal>'`
  list over a bare substring `REPLACE`, which can corrupt unrelated occurrences of the substring.
- **One file = one logical change.** Multiple statements in a file execute as a single implicit
  transaction (atomic), but keep files focused so failures point at one cause.

## Reference

- Runner: `backend/src/app/persistence/postgres_migrations.py`
- Current destructive migrations (already applied, historical): `050_drop_lte_servers.sql`,
  `052_remove_cdn_transport.sql`. They cannot be edited; this doc captures the convention going forward.
