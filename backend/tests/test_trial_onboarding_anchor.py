"""Исходный маршрут подключения не теряется при старте триала по трафику."""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.application.interfaces import SubscriptionSnapshot
from app.issuance.bare_xray_traffic import TrafficSample
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_subscription_snapshot import PostgresSubscriptionSnapshotReader
from app.persistence.postgres_traffic_stats import PostgresTrafficStatsRepository
from app.web_api.admin_stats import _DATA_HEALTH_CHAIN_ANOMALIES, _telegram_ads_campaigns


async def test_route_survives_first_traffic_and_legacy_stays_unknown():
    url = os.getenv("DATABASE_URL")
    if not url:
        pytest.skip("Нужна тестовая PostgreSQL")
    pool = await asyncpg.create_pool(url)
    uid = "anchor-regression-01"
    email = f"user-{uid[:16]}"
    now = datetime.now(UTC)
    issued = now - timedelta(hours=2)
    try:
        await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
        await pool.execute(
            "INSERT INTO user_identities (internal_user_id,telegram_user_id,created_at) VALUES ($1,998888880001,$2)",
            uid,
            issued,
        )
        repo = PostgresSubscriptionSnapshotReader(pool)
        snapshot = SubscriptionSnapshot(
            internal_user_id=uid,
            state_label="active",
            trial_started_at=issued,
            trial_expires_at=issued + timedelta(days=3),
            active_until_utc=issued + timedelta(days=3),
        )
        await repo.upsert_state(snapshot)
        await pool.execute(
            "INSERT INTO telegram_ads_attributions (internal_user_id,campaign_key,telegram_update_id,attributed_at) "
            "VALUES ($1,'anchor-regression',998888880001,$2)",
            uid,
            issued + timedelta(minutes=7),
        )
        await pool.execute(
            "INSERT INTO funnel_interactions "
            "(internal_user_id,event_name,surface,platform,connection_route,dedup_key,occurred_at) "
            "VALUES ($1,'platform_selected','telegram','ios','direct','anchor-regression',$2)",
            uid,
            issued + timedelta(minutes=10),
        )
        for index, when in enumerate((issued - timedelta(minutes=1), issued + timedelta(minutes=3))):
            await pool.execute(
                "INSERT INTO funnel_interactions "
                "(internal_user_id,event_name,surface,platform,connection_route,dedup_key,occurred_at) "
                "VALUES ($1,'platform_selected','telegram','android','guided',$2,$3)",
                uid,
                f"anchor-negative-{index}",
                when,
            )
        migration = (Path(__file__).resolve().parents[1] / "migrations/105_trial_provisioned_at.sql").read_text()
        await pool.execute("UPDATE subscription_snapshots SET trial_provisioned_at=NULL WHERE internal_user_id=$1", uid)
        await pool.execute(migration)
        await pool.execute(migration)
        assert (
            await pool.fetchval(
                "SELECT trial_provisioned_at FROM subscription_snapshots WHERE internal_user_id=$1", uid
            )
            == issued
        )
        traffic = PostgresTrafficStatsRepository(pool)
        sample = TrafficSample(998888, email, 1024, 2097152, 1024, 2097152)
        assert await traffic.apply_batch([sample]) == 1
        row = await pool.fetchrow("SELECT * FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        assert row["trial_provisioned_at"] == issued
        assert row["trial_started_at"] > issued
        assert row["trial_expires_at"] == row["trial_started_at"] + timedelta(days=3)
        assert await traffic.apply_batch([TrafficSample(998888, email, 1024, 2097152, 0, 0)]) == 0
        # Последующая запись актуального snapshot сохраняет исходное время.
        await repo.upsert_state(await repo.get_for_user(uid))
        assert (
            await pool.fetchval(
                "SELECT trial_provisioned_at FROM subscription_snapshots WHERE internal_user_id=$1", uid
            )
            == issued
        )
        end = datetime.now(UTC) + timedelta(seconds=1)
        report = await _telegram_ads_campaigns(pool, issued, end, issued.date(), end.date())
        campaign = next(r for r in report["campaigns"] if r["campaign_key"] == "anchor-regression")
        assert campaign["platform_selected_users"] == 1
        assert campaign["platform_users"]["ios"] == 1
        assert campaign["platform_users"]["android"] == 0
        # Проверяем настоящий диагностический SQL на изолированном пользователе.
        query = _DATA_HEALTH_CHAIN_ANOMALIES.replace(
            "WHERE s.trial_started_at IS NOT NULL",
            "WHERE s.internal_user_id = 'anchor-regression-01' AND s.trial_started_at IS NOT NULL",
        )
        anomalies = await pool.fetchrow(query, issued, end, 1048576, end.date())
        assert anomalies["meaningful_without_route"] == 0
        assert anomalies["trials_without_setup"] == 0
        assert anomalies["onboarding_start_unknown"] == 0
        await pool.execute("UPDATE subscription_snapshots SET trial_provisioned_at=NULL WHERE internal_user_id=$1", uid)
        await repo.upsert_state(await repo.get_for_user(uid))
        assert (
            await pool.fetchval(
                "SELECT trial_provisioned_at FROM subscription_snapshots WHERE internal_user_id=$1", uid
            )
            is None
        )
        anomalies = await pool.fetchrow(query, issued, end, 1048576, end.date())
        assert anomalies["onboarding_start_unknown"] == 1
    finally:
        await pool.execute("DELETE FROM bare_xray_traffic_daily WHERE email=$1", email)
        await pool.execute("DELETE FROM bare_xray_traffic_state WHERE email=$1", email)
        await pool.execute("DELETE FROM funnel_interactions WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM telegram_ads_attributions WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
        await pool.close()
