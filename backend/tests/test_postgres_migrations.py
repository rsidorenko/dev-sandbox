"""Unit tests for postgres_migrations helpers (no real database)."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import re
import textwrap
from pathlib import Path

import pytest

from app.persistence import postgres_migrations as pm


def _utf8_sha256_hex(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _squash_ws(text: str) -> str:
    """Схлопнуть множественные пробелы/переводы строк в один пробел — для сопоставления
    SQL с выравниванием колонок (CREATE TABLE с padding)."""
    return re.sub(r"\s+", " ", text).strip()


def test_sorted_migration_sql_paths_orders_by_filename(tmp_path: Path) -> None:
    (tmp_path / "003_third.sql").write_text("c", encoding="utf-8")
    (tmp_path / "001_first.sql").write_text("a", encoding="utf-8")
    (tmp_path / "002_second.sql").write_text("b", encoding="utf-8")
    (tmp_path / "readme.txt").write_text("x", encoding="utf-8")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "005.sql").write_text("n", encoding="utf-8")

    paths = pm.sorted_migration_sql_paths(tmp_path)
    assert [p.name for p in paths] == ["001_first.sql", "002_second.sql", "003_third.sql"]


def test_default_migrations_directory_matches_backend_layout() -> None:
    backend_root = Path(__file__).resolve().parents[1]
    assert pm.default_migrations_directory() == backend_root / "migrations"


def test_sorted_migration_sql_paths_repo_migrations_order() -> None:
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    paths = pm.sorted_migration_sql_paths(migrations_dir)
    assert [p.name for p in paths] == [
        "001_user_identities.sql",
        "002_idempotency_records.sql",
        "003_subscription_snapshots.sql",
        "004_slice1_audit_events.sql",
        "005_retention_timestamps.sql",
        "006_slice1_uc01_outbound_deliveries.sql",
        "007_slice1_uc01_outbound_deliveries_sent_retention_index.sql",
        "008_billing_events_ledger.sql",
        "009_billing_ingestion_audit_events.sql",
        "010_billing_subscription_apply.sql",
        "011_issuance_state.sql",
        "012_adm02_ensure_access_audit_events.sql",
        "013_telegram_update_dedup.sql",
        "014_subscription_lifecycle_v1.sql",
        "015_access_reconcile_runs.sql",
        "016_subscription_plans.sql",
        "017_referral_codes.sql",
        "018_referral_relationships.sql",
        "019_referral_balances.sql",
        "020_subscription_snapshots_extend.sql",
        "021_referral_relationships_fix_unique.sql",
        "022_user_emails.sql",
        "023_email_verification_codes.sql",
        "024_vpn_servers.sql",
        "025_trial_period.sql",
        "026_notification_state.sql",
        "027_vpn_servers_reality.sql",
        "028_subscription_tokens.sql",
        "029_vpn_servers_encrypted_password.sql",
        "030_user_vless_uuid.sql",
        "031_subscription_token_ttl.sql",
        "032_jwt_revocation.sql",
        "033_notification_indexes.sql",
        "034_subscription_plans_days.sql",
        "035_update_plan_prices.sql",
        "036_vpn_servers_transport.sql",
        "037_vpn_servers_cdn_helsinki.sql",
        "038_vpn_servers_remove_grpc.sql",
        "039_vpn_servers_rename_labels.sql",
        "040_vpn_servers_fix_tcp_label_emoji.sql",
        "041_vpn_servers_remove_flag_prefix.sql",
        "042_vpn_servers_frankfurt.sql",
        "043_vpn_servers_api_token.sql",
        "044_vpn_servers_yandex_relay.sql",
        "045_vpn_servers_yandex_ip_update.sql",
        "046_vpn_servers_lte_sni_max_ru.sql",
        "047_vpn_servers_reality_fp.sql",
        "048_referral_transactions_unique_desc.sql",
        "049_drop_empty_notification_state.sql",
        "050_drop_lte_servers.sql",
        "051_sync_subscription_plans_to_code.sql",
        "052_remove_cdn_transport.sql",
        "053_vpn_servers_link_template.sql",
        "054_referral_tx_dedup_guard.sql",
        "055_vpn_servers_provider_kind.sql",
        "056_vpn_servers_xray_inbound_tag.sql",
        "057_vpn_servers_client_surface.sql",
        "058_vpn_servers_panel_flavor.sql",
        "059_vpn_servers_bare_xray_ssh.sql",
        "060_vpn_servers_cdn_bypass_lt.sql",
        "061_promo_codes.sql",
        "062_promo_redemptions.sql",
        "063_promo_applied.sql",
        "064_admin_subscription_actions.sql",
        "065_promo_codes_per_plan_discounts.sql",
        "066_user_identities_created_at.sql",
        "067_promo_codes_audience_all_users.sql",
        "068_broadcast_jobs.sql",
        "069_broadcast_jobs_buttons.sql",
        "070_referral_transactions_idempotency_key.sql",
        "071_referral_bonus_awards.sql",
        "072_broadcast_bonus.sql",
        "073_broadcast_promo.sql",
        "074_promo_per_user_limit.sql",
        "075_subscription_token_permanent.sql",
        "076_pricing_admin.sql",
        "077_web_notifications.sql",
        "078_certificate_expiry_state.sql",
        "079_bare_xray_traffic.sql",
        "080_vpn_servers_auto_selection_alias.sql",
        "081_funnel_foundation.sql",
        "082_telegram_ads_attribution.sql",
        "083_telegram_ads_spend.sql",
        "084_admin_user_reset_actions.sql",
        "085_notification_log_delivery_status.sql",
        "086_trial_first_traffic.sql",
        "087_checkout_recovery_context.sql",
        "088_two_sided_referral.sql",
        "089_experiment_assignments.sql",
        "090_analytics_eligible_users.sql",
        "091_funnel_diagnostics_v2.sql",
        "092_trial_feedback.sql",
        "093_connection_checks.sql",
        "094_trial_feedback_web_route.sql",
        "095_notification_holdout_status.sql",
        "096_vpn_servers_connection_check_host.sql",
        "097_trial_feedback_surface.sql",
        "098_drop_user_emails.sql",
    ]


def test_apply_postgres_migrations_calls_execute_per_file_in_order(tmp_path: Path) -> None:
    (tmp_path / "002_b.sql").write_text("B", encoding="utf-8")
    (tmp_path / "001_a.sql").write_text("A", encoding="utf-8")

    calls: list[tuple[str, str | None]] = []

    class FakeExecutor:
        async def execute(self, query: str, *args: object, **kwargs: object) -> str:
            if query.startswith("CREATE TABLE IF NOT EXISTS schema_migration_ledger"):
                calls.append(("create", None))
                return "OK"
            if query.startswith("ALTER TABLE schema_migration_ledger ADD COLUMN IF NOT EXISTS checksum"):
                calls.append(("alter_checksum", None))
                return "OK"
            if query.startswith("INSERT INTO schema_migration_ledger(filename, checksum)"):
                calls.append(("insert", f"{args[0]}:{args[1]}"))
                return "INSERT 0 1"
            calls.append(("migration", query))
            return "OK"

        async def fetch(self, query: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            assert "SELECT filename, checksum FROM schema_migration_ledger" in query.replace("\n", " ")
            calls.append(("fetch", None))
            return []

    async def main() -> None:
        await pm.apply_postgres_migrations(FakeExecutor(), migrations_directory=tmp_path)

    asyncio.run(main())
    assert calls == [
        ("create", None),
        ("alter_checksum", None),
        ("fetch", None),
        ("migration", "A"),
        ("insert", f"001_a.sql:{_utf8_sha256_hex('A')}"),
        ("migration", "B"),
        ("insert", f"002_b.sql:{_utf8_sha256_hex('B')}"),
    ]


def test_apply_postgres_migrations_empty_directory(tmp_path: Path) -> None:
    calls: list[str] = []

    class FakeExecutor:
        async def execute(self, query: str, *args: object, **kwargs: object) -> str:
            if query.startswith("CREATE TABLE IF NOT EXISTS schema_migration_ledger"):
                calls.append("create")
                return "OK"
            if query.startswith("ALTER TABLE schema_migration_ledger ADD COLUMN IF NOT EXISTS checksum"):
                calls.append("alter_checksum")
                return "OK"
            if query.startswith("INSERT INTO schema_migration_ledger(filename, checksum)"):
                raise AssertionError("insert should not be called for an empty directory")
            raise AssertionError("no migration SQL should be executed for an empty directory")

        async def fetch(self, query: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            assert "SELECT filename, checksum FROM schema_migration_ledger" in query.replace("\n", " ")
            calls.append("fetch")
            return []

    async def main() -> None:
        await pm.apply_postgres_migrations(FakeExecutor(), migrations_directory=tmp_path)

    asyncio.run(main())
    assert calls == ["create", "alter_checksum", "fetch"]


def test_apply_postgres_migrations_ledger_skips_applied_and_prevents_rerun(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("A", encoding="utf-8")
    (tmp_path / "002_b.sql").write_text("B", encoding="utf-8")

    ops: list[tuple[str, str | None]] = []
    ledger: dict[str, str | None] = {"001_a.sql": _utf8_sha256_hex("A")}

    class FakeExecutor:
        async def execute(self, query: str, *args: object, **kwargs: object) -> str:
            if query.startswith("CREATE TABLE IF NOT EXISTS schema_migration_ledger"):
                ops.append(("create", None))
                return "OK"
            if query.startswith("ALTER TABLE schema_migration_ledger ADD COLUMN IF NOT EXISTS checksum"):
                ops.append(("alter_checksum", None))
                return "OK"
            if query.startswith("INSERT INTO schema_migration_ledger(filename, checksum)"):
                filename, checksum = str(args[0]), str(args[1])
                ops.append(("insert", f"{filename}:{checksum}"))
                ledger[filename] = checksum
                return "INSERT 0 1"
            ops.append(("migration", query))
            return "OK"

        async def fetch(self, query: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            assert "SELECT filename, checksum FROM schema_migration_ledger" in query.replace("\n", " ")
            ops.append(("fetch", None))
            return [{"filename": name, "checksum": ledger.get(name)} for name in sorted(ledger)]

    async def main() -> None:
        executor = FakeExecutor()
        await pm.apply_postgres_migrations(executor, migrations_directory=tmp_path)
        await pm.apply_postgres_migrations(executor, migrations_directory=tmp_path)

    asyncio.run(main())

    assert ops[0:3] == [("create", None), ("alter_checksum", None), ("fetch", None)]
    assert ("migration", "A") not in ops
    assert ops.count(("migration", "B")) == 1
    assert ops.count(("insert", f"002_b.sql:{_utf8_sha256_hex('B')}")) == 1
    second_run_fetch_idx = ops.index(("fetch", None), 3)
    tail = ops[second_run_fetch_idx:]
    assert not any(op[0] == "migration" for op in tail)
    assert not any(op[0] == "insert" for op in tail)


def test_apply_postgres_migrations_rerun_same_checksum_skips(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("A", encoding="utf-8")
    chk = _utf8_sha256_hex("A")
    ledger: dict[str, str | None] = {"001_a.sql": chk}
    ops: list[str] = []

    class FakeExecutor:
        async def execute(self, query: str, *args: object, **kwargs: object) -> str:
            if query.startswith("CREATE TABLE IF NOT EXISTS schema_migration_ledger"):
                ops.append("create")
                return "OK"
            if query.startswith("ALTER TABLE schema_migration_ledger ADD COLUMN IF NOT EXISTS checksum"):
                ops.append("alter_checksum")
                return "OK"
            if query.startswith("INSERT INTO schema_migration_ledger"):
                raise AssertionError("insert should not run when checksum matches")
            raise AssertionError("migration SQL should not run when checksum matches")

        async def fetch(self, query: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            ops.append("fetch")
            return [{"filename": name, "checksum": ledger[name]} for name in sorted(ledger)]

    async def main() -> None:
        ex = FakeExecutor()
        await pm.apply_postgres_migrations(ex, migrations_directory=tmp_path)
        await pm.apply_postgres_migrations(ex, migrations_directory=tmp_path)

    asyncio.run(main())
    assert ops == ["create", "alter_checksum", "fetch", "create", "alter_checksum", "fetch"]


def test_apply_postgres_migrations_checksum_mismatch_raises(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("A", encoding="utf-8")
    wrong = "0" * 64
    ledger: dict[str, str | None] = {"001_a.sql": wrong}

    class FakeExecutor:
        async def execute(self, query: str, *args: object, **kwargs: object) -> str:
            return "OK"

        async def fetch(self, query: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            return [{"filename": name, "checksum": ledger[name]} for name in sorted(ledger)]

    async def main() -> None:
        await pm.apply_postgres_migrations(FakeExecutor(), migrations_directory=tmp_path)

    with pytest.raises(pm.MigrationLedgerDriftError, match="checksum mismatch"):
        asyncio.run(main())


def test_apply_postgres_migrations_legacy_null_checksum_backfills(tmp_path: Path) -> None:
    (tmp_path / "001_a.sql").write_text("A", encoding="utf-8")
    ledger_rows: list[dict[str, object]] = [{"filename": "001_a.sql", "checksum": None}]
    updates: list[tuple[str, str]] = []

    class FakeExecutor:
        async def execute(self, query: str, *args: object, **kwargs: object) -> str:
            if "UPDATE schema_migration_ledger" in query and "SET checksum" in query:
                updates.append((str(args[0]), str(args[1])))
                ledger_rows[0]["checksum"] = str(args[1])
                return "UPDATE 1"
            if query.startswith("INSERT INTO schema_migration_ledger"):
                raise AssertionError("legacy row should not insert again")
            return "OK"

        async def fetch(self, query: str, *args: object, **kwargs: object) -> list[dict[str, object]]:
            return list(ledger_rows)

    async def main() -> None:
        await pm.apply_postgres_migrations(FakeExecutor(), migrations_directory=tmp_path)

    asyncio.run(main())
    assert updates == [("001_a.sql", _utf8_sha256_hex("A"))]


def test_postgres_migrations_module_avoids_env_and_runtime_wiring() -> None:
    source = textwrap.dedent(inspect.getsource(pm))
    banned = (
        "os.environ",
        "getenv",
        "DATABASE_URL",
        "asyncpg.create_pool",
        "create_pool",
        "asyncpg.connect",
    )
    lowered = source.lower()
    for token in banned:
        assert token.lower() not in lowered, f"unexpected dependency token: {token!r}"


def test_sorted_migration_sql_paths_rejects_non_directory(tmp_path: Path) -> None:
    file_path = tmp_path / "not_a_dir.sql"
    file_path.write_text("--", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        pm.sorted_migration_sql_paths(file_path)


def test_telegram_update_dedup_migration_uses_only_safe_bounded_columns() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "013_telegram_update_dedup.sql").read_text(
        encoding="utf-8"
    )
    lowered = migration.lower()
    for required in (
        "dedup_key_hash",
        "command_bucket",
        "first_seen_at",
        "expires_at",
    ):
        assert required in lowered
    for forbidden in (
        "telegram_user_id",
        "telegram_update_id",
        "chat_id",
        "message_text",
        "payload",
        "jsonb",
        "bytea",
        "internal_user_id",
        "provider_issuance_ref",
        "issue_idempotency_key",
        "provider_ref",
        "customer_ref",
        "checkout_attempt_id",
        "schema_version",
        "token",
    ):
        assert forbidden not in lowered


def test_subscription_lifecycle_migration_contract_safe_additive_columns() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "014_subscription_lifecycle_v1.sql").read_text(
        encoding="utf-8"
    )
    lowered = migration.lower()

    assert "alter table subscription_snapshots" in lowered
    assert "add column if not exists active_until_utc timestamptz null" in lowered
    assert "add column if not exists updated_at timestamptz not null default now()" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered


def test_access_reconcile_runs_migration_contract_safe_additive_table() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "015_access_reconcile_runs.sql").read_text(
        encoding="utf-8"
    )
    lowered = migration.lower()

    assert "create table if not exists access_reconcile_runs" in lowered
    assert "run_id uuid primary key" in lowered
    assert "task_name text not null" in lowered
    assert "started_at timestamptz not null" in lowered
    assert "status text not null" in lowered
    assert "reconciled_rows integer not null default 0" in lowered
    assert "error_class text null" in lowered
    assert "error_message text null" in lowered
    assert "create index if not exists idx_access_reconcile_runs_task_started_at" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter table issuance_state",
        "alter table subscription_snapshots",
    ):
        assert forbidden not in lowered


def test_broadcast_bonus_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "072_broadcast_bonus.sql").read_text(
        encoding="utf-8"
    )
    # Нормализуем whitespace (выравнивание колонок в SQL даёт множественные пробелы).
    lowered = _squash_ws(migration).lower()

    # additive: новая nullable-колонка + новая таблица
    assert "alter table broadcast_jobs add column if not exists bonus jsonb null" in lowered
    assert "create table if not exists broadcast_bonus_claims" in lowered
    assert "job_id uuid not null references broadcast_jobs(job_id) on delete cascade" in lowered
    assert "telegram_user_id bigint not null" in lowered
    assert "status text not null default 'eligible' check (status in ('eligible', 'claimed'))" in lowered
    assert "days integer not null check (days between 3 and 30)" in lowered
    assert "expires_at timestamptz not null" in lowered
    assert "primary key (job_id, telegram_user_id)" in lowered
    assert "create index if not exists idx_broadcast_bonus_claims_status" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered


def test_promo_per_user_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "074_promo_per_user_limit.sql").read_text(
        encoding="utf-8"
    )
    # Нормализуем whitespace (выравнивание колонок в SQL даёт множественные пробелы).
    lowered = _squash_ws(migration).lower()

    # additive: новая nullable-колонка per-user лимита + backfill «единожды» для старых кодов.
    assert "alter table promo_codes add column if not exists max_redemptions_per_user integer null" in lowered
    assert "check (max_redemptions_per_user is null or max_redemptions_per_user >= 1)" in lowered
    assert "update promo_codes set max_redemptions_per_user = 1" in lowered
    # UNIQUE(code, internal_user_id) заменён кодовым enforced-лимитом (см. _redeem_atomic).
    assert "alter table promo_redemptions drop constraint if exists promo_redemptions_user_code_uniq" in lowered
    assert "create index if not exists idx_promo_redemptions_code_user" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered


def test_broadcast_promo_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "073_broadcast_promo.sql").read_text(
        encoding="utf-8"
    )
    # Нормализуем whitespace (выравнивание колонок в SQL даёт множественные пробелы).
    lowered = _squash_ws(migration).lower()

    # additive: новая таблица реестра получателей промокода (без status-машины — apply идемпотентен).
    assert "create table if not exists broadcast_promo_claims" in lowered
    assert "job_id uuid not null references broadcast_jobs(job_id) on delete cascade" in lowered
    assert "telegram_user_id bigint not null" in lowered
    assert "code text not null" in lowered
    assert "expires_at timestamptz not null" in lowered
    assert "primary key (job_id, telegram_user_id)" in lowered
    assert "create index if not exists idx_broadcast_promo_claims_expires" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered


def test_certificate_expiry_state_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "078_certificate_expiry_state.sql").read_text(
        encoding="utf-8"
    )
    # Нормализуем whitespace (выравнивание колонок в SQL даёт множественные пробелы).
    lowered = _squash_ws(migration).lower()

    # additive: одна новая таблица состояния сертификатного мониторинга, ничего не удаляем.
    assert "create table if not exists certificate_expiry_state" in lowered
    assert "name text primary key" in lowered
    assert "mode text not null check (mode in ('tls', 'ssh-file'))" in lowered
    assert "not_after timestamptz null" in lowered
    assert "last_alert_bucket integer null" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered


def test_bare_xray_traffic_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "079_bare_xray_traffic.sql").read_text(
        encoding="utf-8"
    )
    # Нормализуем whitespace (выравнивание колонок в SQL даёт множественные пробелы).
    lowered = _squash_ws(migration).lower()

    # additive: две новые таблицы учёта трафика + индекс, ничего не удаляем.
    assert "create table if not exists bare_xray_traffic_state" in lowered
    assert "primary key (server_id, email)" in lowered
    assert "create table if not exists bare_xray_traffic_daily" in lowered
    assert "primary key (stat_date, server_id, email)" in lowered
    assert "create index if not exists idx_bare_xray_traffic_daily_email_date" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered


def test_notification_delivery_status_migration_contract_safe_additive() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "085_notification_log_delivery_status.sql"
    ).read_text(encoding="utf-8")
    lowered = _squash_ws(migration).lower()

    assert "add column if not exists delivery_status text not null default 'delivered'" in lowered
    assert "check (delivery_status in ('delivered', 'terminal_failure'))" in lowered
    assert "notification_log_delivery_status_check" in lowered

    for forbidden in ("drop table", "drop column", "truncate", " delete from "):
        assert forbidden not in lowered


def test_trial_first_traffic_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "086_trial_first_traffic.sql").read_text(
        encoding="utf-8"
    )
    lowered = _squash_ws(migration).lower()

    assert "add column if not exists trial_first_traffic_at timestamptz null" in lowered
    assert "set trial_first_traffic_at = s.trial_started_at" in lowered
    assert "bare_xray_traffic_daily" in lowered
    assert "t.uplink_bytes + t.downlink_bytes > 0" in lowered
    assert "create index if not exists idx_subscription_trial_waiting_first_traffic" in lowered

    for forbidden in ("drop table", "drop column", "truncate", " delete from ", "alter column"):
        assert forbidden not in lowered


def test_checkout_recovery_context_migration_contract_safe_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "087_checkout_recovery_context.sql").read_text(
        encoding="utf-8"
    )
    lowered = _squash_ws(migration).lower()

    assert "add column if not exists plan_id text null" in lowered
    assert "add column if not exists device_count integer null" in lowered
    assert "add column if not exists payment_method text null" in lowered
    assert "device_count between 5 and 20" in lowered
    assert "payment_method in ('card', 'crypto')" in lowered
    assert "create index if not exists idx_funnel_checkout_recovery_latest" in lowered

    for forbidden in ("drop table", "drop column", "truncate", " delete from "):
        assert forbidden not in lowered


def test_two_sided_referral_migration_preserves_legacy_and_is_additive() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "088_two_sided_referral.sql").read_text(
        encoding="utf-8"
    )
    lowered = _squash_ws(migration).lower()

    assert "add column if not exists reward_policy text" in lowered
    assert "set reward_policy = 'legacy_stack'" in lowered
    assert "alter column reward_policy set default 'balanced_v1'" in lowered
    assert "create table if not exists referral_invitee_bonus_awards" in lowered
    assert "unique (referred_user_id)" in lowered

    for forbidden in ("drop table", "drop column", "truncate", " delete from "):
        assert forbidden not in lowered


def test_experiment_assignments_migration_is_additive_and_idempotent() -> None:
    migration = (Path(__file__).resolve().parents[1] / "migrations" / "089_experiment_assignments.sql").read_text(
        encoding="utf-8"
    )
    lowered = _squash_ws(migration).lower()

    assert "create table if not exists experiment_assignments" in lowered
    assert "primary key (internal_user_id, experiment_key)" in lowered
    assert "create index if not exists idx_experiment_assignments_key_time" in lowered

    for forbidden in ("drop table", "drop column", "truncate", " delete from "):
        assert forbidden not in lowered


def test_auto_selection_alias_migration_contract_safe_additive() -> None:
    migration = (
        Path(__file__).resolve().parents[1] / "migrations" / "080_vpn_servers_auto_selection_alias.sql"
    ).read_text(encoding="utf-8")
    lowered = _squash_ws(migration).lower()

    assert "add column if not exists auto_selection_route integer not null default 0" in lowered
    assert "add column if not exists auto_selection_label text not null default ''" in lowered
    assert "check (auto_selection_route = 0 or auto_selection_route between 61440 and 65535)" in lowered
    assert "create unique index if not exists uq_vpn_servers_auto_selection_route_per_host" in lowered

    for forbidden in (
        "drop table",
        "drop column",
        "truncate",
        " delete from ",
        "alter column",
    ):
        assert forbidden not in lowered
