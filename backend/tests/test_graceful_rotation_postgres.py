"""Переход ключей и изменение лимита на реальных транзакциях PostgreSQL."""

import asyncio
import os
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.application.balance_access_jobs import recover_access_jobs
from app.application.device_limit_service import reduce_device_limit
from app.issuance.bare_xray_traffic import TrafficSample
from app.issuance.key_identity import key_email, vless_uuid_for_transport
from app.issuance.vless_provider import VlessProviderOutcome
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_traffic_stats import PostgresTrafficStatsRepository
from tests.test_key_rotation import Panel, provider_for


@pytest.fixture
async def rotation_account():
    dsn = os.environ.get("KEY_ROTATION_TEST_DSN")
    if not dsn:
        pytest.skip("Нужна тестовая БД KEY_ROTATION_TEST_DSN")
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=5)
    uid = f"rot-{uuid.uuid4()}"
    deadline = datetime.now(UTC) + timedelta(days=10)
    await apply_postgres_migrations(pool, migrations_directory=Path(__file__).parents[1] / "migrations")
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)",
        uid,
        -uuid.uuid4().int % -(2**60),
    )
    await pool.execute(
        "INSERT INTO subscription_snapshots (internal_user_id,state_label,plan_id,device_count,active_until_utc)"
        " VALUES ($1,'active','1m',9,$2)",
        uid,
        deadline,
    )
    try:
        yield pool, uid, deadline
    finally:
        await pool.execute(
            "DELETE FROM bare_xray_traffic_daily WHERE email IN (SELECT email FROM vless_key_emails WHERE internal_user_id=$1)",
            uid,
        )
        await pool.execute(
            "DELETE FROM bare_xray_traffic_state WHERE email IN (SELECT email FROM vless_key_emails WHERE internal_user_id=$1)",
            uid,
        )
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
        await pool.close()


async def test_restart_partial_retirement_and_generation_history(rotation_account):
    pool, uid, deadline = rotation_account
    panels = [Panel(uid), Panel(uid, "xhttp")]
    first = provider_for(pool, panels)
    initial = [p.credentials for p in panels]
    result = await first.reissue_user(internal_user_id=uid)
    assert result.outcome == VlessProviderOutcome.SUCCESS
    seed = await pool.fetchval("SELECT vless_key_seed FROM user_identities WHERE internal_user_id=$1", uid)
    for panel, old in zip(panels, initial, strict=True):
        assert old < panel.credentials
        panel.traffic[panel.email] = (900, 10)
    restarted = provider_for(pool, panels)
    await restarted.process_key_rotations()
    assert await restarted.rotation_status(uid) == "waiting"
    panels[0].traffic[key_email(uid, "tcp", seed)] = (1, 0)
    panels[1].remove_ok = False
    await restarted.process_key_rotations()
    assert await restarted.rotation_status(uid) == "retiring"
    assert not panels[0].credentials & initial[0]
    assert panels[1].credentials & initial[1]
    panels[0].traffic.clear()
    panels[1].remove_ok = True
    await provider_for(pool, panels).process_key_rotations()
    assert await restarted.rotation_status(uid) == "completed"
    assert (
        await pool.fetchval("SELECT active_until_utc FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        == deadline
    )
    for panel in panels:
        assert panel.credentials == {vless_uuid_for_transport(uid, panel.server_config.transport_type, seed)}
    # Счётчики прежних поколений остаются атрибутированы после следующего перевыпуска.
    server_id = await pool.fetchval("SELECT id FROM vpn_servers ORDER BY id LIMIT 1")
    assert server_id is not None
    repo = PostgresTrafficStatsRepository(pool)
    await repo.apply_batch([TrafficSample(server_id, key_email(uid, "tcp", seed), 11, 12, 11, 12)])
    await restarted.reissue_user(internal_user_id=uid)
    next_seed = await pool.fetchval("SELECT vless_key_seed FROM user_identities WHERE internal_user_id=$1", uid)
    assert next_seed != seed
    await repo.apply_batch([TrafficSample(server_id, key_email(uid, "tcp", next_seed), 3, 4, 3, 4)])
    assert await repo.monthly_total_for_user(uid) == 30
    assert await repo.monthly_usage_for_user(uid) == (14, 16)
    assert sum(r["uplink_bytes"] + r["downlink_bytes"] for r in await repo.per_server_for_user(uid)) == 30


async def test_trial_clock_recognizes_new_generation(rotation_account):
    pool, uid, _ = rotation_account
    await pool.execute(
        "UPDATE subscription_snapshots SET plan_id=NULL, trial_started_at=NOW()-INTERVAL '1 day',"
        " trial_expires_at=NOW()+INTERVAL '2 days' WHERE internal_user_id=$1",
        uid,
    )
    await provider_for(pool, [Panel(uid)]).reissue_user(internal_user_id=uid)
    seed = await pool.fetchval("SELECT vless_email_seed FROM user_identities WHERE internal_user_id=$1", uid)
    server_id = await pool.fetchval("SELECT id FROM vpn_servers ORDER BY id LIMIT 1")
    sample = TrafficSample(server_id, key_email(uid, "tcp", seed), 1, 0, 1, 0)
    repo = PostgresTrafficStatsRepository(pool)
    assert await repo.apply_batch([sample]) == 1
    assert await repo.apply_batch([sample]) == 0
    assert await pool.fetchval(
        "SELECT trial_first_traffic_at FROM subscription_snapshots WHERE internal_user_id=$1", uid
    )


async def test_device_reduction_retries_without_erasing_concurrent_renewal(rotation_account):
    pool, uid, deadline = rotation_account
    entered, release = asyncio.Event(), asyncio.Event()

    class OfflineProvider:
        async def sync_user_access(self, **kwargs):
            entered.set()
            await release.wait()
            raise RuntimeError("Тестовый сбой сервера")

    reducing = asyncio.create_task(
        reduce_device_limit(
            pool=pool,
            snapshots=None,
            provider=OfflineProvider(),
            internal_user_id=uid,
            new_count=7,
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=3)
    # Платёж завершается после сохранения лимита, пока сетевое применение ещё ожидается.
    await pool.execute(
        "UPDATE subscription_snapshots SET active_until_utc=$2 WHERE internal_user_id=$1",
        uid,
        deadline + timedelta(days=30),
    )
    release.set()
    assert await reducing == "pending"
    row = await pool.fetchrow("SELECT * FROM subscription_snapshots WHERE internal_user_id=$1", uid)
    assert row["active_until_utc"] == deadline + timedelta(days=30)
    assert row["plan_id"] == "1m" and row["device_count"] == 7
    await pool.execute("UPDATE balance_access_jobs SET next_attempt_at=NOW() WHERE internal_user_id=$1", uid)
    panel = Panel(uid)
    assert await recover_access_jobs(pool, provider_for(pool, [panel])) == 1
    assert panel.last_limit == 7
    assert panel.last_expiry == int(row["active_until_utc"].timestamp() * 1000)


async def test_device_limit_applies_to_both_generations(rotation_account):
    pool, uid, _ = rotation_account
    panel = Panel(uid)
    provider = provider_for(pool, [panel])
    await provider.reissue_user(internal_user_id=uid)
    observed = {}
    original = panel.add_client

    async def capture(**kwargs):
        observed[kwargs["user_uuid"]] = kwargs["limit_ip"]
        return await original(**kwargs)

    panel.add_client = capture
    assert (
        await reduce_device_limit(pool=pool, snapshots=None, provider=provider, internal_user_id=uid, new_count=6)
        == "done"
    )
    assert len(observed) == 2 and set(observed.values()) == {6}
    assert await provider.rotation_status(uid) == "waiting"


async def test_expired_device_reduction_is_rejected(rotation_account):
    pool, uid, _ = rotation_account
    await pool.execute(
        "UPDATE subscription_snapshots SET active_until_utc=NOW()-INTERVAL '1 day' WHERE internal_user_id=$1", uid
    )
    assert (
        await reduce_device_limit(pool=pool, snapshots=None, provider=None, internal_user_id=uid, new_count=6)
        == "no_active"
    )
    assert await pool.fetchval("SELECT device_count FROM subscription_snapshots WHERE internal_user_id=$1", uid) == 9
