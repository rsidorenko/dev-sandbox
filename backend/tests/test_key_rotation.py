"""Ротация реального провайдера на управляемых заменах сетевых клиентов."""

import asyncio
import os
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import asyncpg
import pytest

from app.issuance.key_identity import key_email, vless_uuid_for_transport
from app.issuance.vless_provider import VlessProviderOutcome
from app.issuance.xui_client import XuiClientResult, XuiOutcome
from app.issuance.xui_vless_provider import XuiVlessProvider, _email_from_internal
from app.persistence.postgres_migrations import apply_postgres_migrations


class KeyPool:
    def __init__(self):
        self.state = {"vless_key_seed": None, "vless_previous_key_seed": None, "vless_rotation_pending": False}
        self.deadline = datetime.now(UTC) + timedelta(hours=2)

    async def fetchrow(self, sql, *args):
        if "subscription_snapshots" in sql:
            return {
                "state_label": "active",
                "active_until_utc": self.deadline,
                "trial_expires_at": None,
                "keys_deleted_at": None,
                "device_count": 9,
            }
        if "subscription_token" in sql:
            return {"subscription_token": "test-token"}
        return dict(self.state)

    async def execute(self, sql, *args):
        if "vless_rotation_pending=TRUE" in sql and not self.state["vless_rotation_pending"]:
            self.state.update(
                vless_previous_key_seed=self.state["vless_key_seed"],
                vless_key_seed=args[1],
                vless_rotation_pending=True,
                vless_rotation_phase="provisioning" if "vless_rotation_phase" in sql else None,
                vless_previous_email_seed=self.state.get("vless_email_seed"),
                vless_email_seed=args[1] if "vless_email_seed" in sql else None,
            )
        elif "vless_rotation_pending=FALSE" in sql:
            self.state.update(
                vless_rotation_pending=False,
                vless_previous_key_seed=None,
                vless_previous_email_seed=None,
                vless_rotation_phase=None,
            )
        elif "vless_rotation_phase='waiting'" in sql:
            self.state["vless_rotation_phase"] = "waiting"
        elif "vless_rotation_phase='retiring'" in sql:
            self.state.update(vless_rotation_phase="retiring", vless_first_traffic_at=datetime.now(UTC))

    async def fetch(self, sql, *args):
        if "vless_rotation_phase IS NULL" in sql and self.state.get("vless_rotation_phase"):
            return []
        if "WHERE vless_rotation_pending" in sql:
            return [{"internal_user_id": "rotation-test"}] if self.state["vless_rotation_pending"] else []
        return [{"internal_user_id": "rotation-test"}]


class Panel:
    def __init__(self, uid="rotation-test", transport="tcp"):
        self.server_id = 1 if transport == "tcp" else 2
        self.server_config = SimpleNamespace(
            transport_type=transport,
            panel_url="https://panel.test",
            label=transport,
            country_code="DE",
            country_flag="",
            client_surface="default",
            link_template="vless://{UUID}@test:443",
            auto_selection_route=0,
        )
        self.email = _email_from_internal(uid, transport_type=transport)
        self.accounts = {self.email: vless_uuid_for_transport(uid, transport)}
        self.traffic = {}
        self.disabled = set()
        self.remove_ok = True
        self.add_ok = True
        self.last_expiry = None
        self.last_limit = None

    @property
    def credentials(self):
        return set(self.accounts.values())

    async def resolve_client_uuid(self, *, email):
        return self.accounts.get(email)

    async def get_client_usage(self, *, email):
        return self.traffic.get(email, (0, 0))

    async def disable_client(self, *, user_uuid, **kwargs):
        if not self.remove_ok:
            return XuiClientResult(XuiOutcome.UNAVAILABLE)
        self.disabled.add(user_uuid)
        return XuiClientResult(XuiOutcome.SUCCESS)

    async def delete_client(self, *, user_uuid):
        if not self.remove_ok:
            return XuiClientResult(XuiOutcome.UNAVAILABLE)
        self.accounts = {email: value for email, value in self.accounts.items() if value != user_uuid}
        return XuiClientResult(XuiOutcome.SUCCESS)

    async def add_client(self, *, user_uuid, expiry_ts, limit_ip, **kwargs):
        if not self.add_ok:
            return XuiClientResult(XuiOutcome.UNAVAILABLE)
        self.accounts[kwargs["email"]] = user_uuid
        self.disabled.discard(user_uuid)
        self.last_expiry, self.last_limit = expiry_ts, limit_ip
        return XuiClientResult(XuiOutcome.SUCCESS)

    async def update_client(self, **kwargs):
        return await self.add_client(**kwargs)


def provider_for(pool, panels):
    provider = XuiVlessProvider(pool=pool)
    provider._get_clients = AsyncMock(return_value=panels)
    provider._get_bare_xray_clients = AsyncMock(return_value=())
    return provider


def test_legacy_keys_unchanged_and_rotated_transports_distinct():
    for transport in ("tcp", "xhttp", "grpc"):
        old = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"vpn.bravada.internal.u1.{transport}"))
        assert vless_uuid_for_transport("u1", transport) == old
        assert vless_uuid_for_transport("u1", transport, "seed") != old
    assert vless_uuid_for_transport("u1", "tcp", "seed") != vless_uuid_for_transport("u1", "xhttp", "seed")


async def test_reissue_keeps_old_until_new_traffic_and_preserves_entitlement():
    pool = KeyPool()
    panels = [Panel(), Panel(transport="xhttp")]
    old = [set(p.credentials) for p in panels]
    provider = provider_for(pool, panels)
    result = await provider.reissue_user(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.SUCCESS
    assert pool.state["vless_rotation_phase"] == "waiting"
    for panel, old_keys in zip(panels, old, strict=True):
        assert old_keys < panel.credentials
        assert panel.last_expiry == int(pool.deadline.timestamp() * 1000)
        assert panel.last_limit == 9
        panel.traffic[panel.email] = (1000, 1000)
    await provider.process_key_rotations()
    assert pool.state["vless_rotation_phase"] == "waiting"
    panels[0].traffic[key_email("rotation-test", "tcp", pool.state["vless_email_seed"])] = (0, 1)
    await provider.process_key_rotations()
    assert not pool.state["vless_rotation_pending"]
    for panel, old_keys in zip(panels, old, strict=True):
        assert not panel.credentials & old_keys
        assert len(panel.credentials) == 1


async def test_other_provider_cache_observes_new_generation():
    pool, panel = KeyPool(), Panel()
    reader = provider_for(pool, [panel])
    before = await reader.get_user_config(internal_user_id="rotation-test")
    await provider_for(pool, [panel]).reissue_user(internal_user_id="rotation-test")
    after = await reader.get_user_config(internal_user_id="rotation-test")
    assert before.config.servers[0].vless_link != after.config.servers[0].vless_link


async def test_provisioning_failure_and_restart_keep_old_then_finish_same_generation():
    pool, panel = KeyPool(), Panel()
    old = panel.credentials
    panel.add_ok = False
    first = provider_for(pool, [panel])
    result = await first.reissue_user(internal_user_id="rotation-test")
    assert result.outcome != VlessProviderOutcome.SUCCESS
    seed = pool.state["vless_key_seed"]
    assert panel.credentials == old
    assert (await first.get_user_config(internal_user_id="rotation-test")).outcome == VlessProviderOutcome.UNAVAILABLE
    panel.add_ok = True
    restarted = provider_for(pool, [panel])
    await restarted.process_key_rotations()
    assert pool.state["vless_key_seed"] == seed
    assert pool.state["vless_rotation_phase"] == "waiting"
    assert old < panel.credentials


async def test_retire_failure_retries_after_restart_even_when_stats_reset():
    pool, panel = KeyPool(), Panel()
    first = provider_for(pool, [panel])
    await first.reissue_user(internal_user_id="rotation-test")
    seed = pool.state["vless_key_seed"]
    panel.traffic[key_email("rotation-test", "tcp", seed)] = (1, 0)
    panel.remove_ok = False
    await first.process_key_rotations()
    assert pool.state["vless_rotation_phase"] == "retiring"
    panel.traffic.clear()
    panel.remove_ok = True
    await provider_for(pool, [panel]).process_key_rotations()
    assert not pool.state["vless_rotation_pending"]
    assert panel.credentials == {vless_uuid_for_transport("rotation-test", "tcp", seed)}


async def test_expired_user_cannot_rotate():
    pool, panel = KeyPool(), Panel()
    pool.deadline = datetime.now(UTC) - timedelta(seconds=1)
    original = set(panel.credentials)
    result = await provider_for(pool, [panel]).reissue_user(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.NOT_FOUND
    assert pool.state["vless_key_seed"] is None
    assert panel.credentials == original


@pytest.mark.parametrize("operation", ["revoke_user", "delete_user"])
async def test_lifecycle_after_failed_rotation_removes_both_generations(operation):
    pool, panel = KeyPool(), Panel()
    panel.remove_ok = False
    provider = provider_for(pool, [panel])
    await provider.reissue_user(internal_user_id="rotation-test")
    assert pool.state["vless_rotation_pending"]
    assert panel.credentials
    result = await getattr(provider, operation)(internal_user_id="rotation-test")
    assert result.outcome != VlessProviderOutcome.SUCCESS
    panel.remove_ok = True
    result = await getattr(provider, operation)(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.SUCCESS
    if operation == "delete_user":
        assert not panel.credentials
        assert not pool.state["vless_rotation_pending"]
    else:
        assert panel.disabled == panel.credentials
        assert pool.state["vless_rotation_pending"]


async def test_bare_failure_is_not_successful_revocation():
    pool = KeyPool()
    pool.state.update(vless_key_seed="new", vless_rotation_pending=True)
    provider = provider_for(pool, [])
    config = SimpleNamespace(transport_type="tcp", server_id=1)
    provider._bare_xray_configs = (config,)
    bare = SimpleNamespace(server_id=1, server_config=config, remove_client=AsyncMock(return_value=False))
    provider._get_bare_xray_clients.return_value = (bare,)
    result = await provider.reissue_user(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.UNAVAILABLE
    assert pool.state["vless_rotation_pending"]


async def test_expired_pending_rotation_finishes_without_recreating():
    pool, panel = KeyPool(), Panel()
    panel.remove_ok = False
    provider = provider_for(pool, [panel])
    await provider.reissue_user(internal_user_id="rotation-test")
    pool.deadline = datetime.now(UTC) - timedelta(seconds=1)
    panel.remove_ok = True
    await provider.process_key_rotations()
    assert pool.state["vless_rotation_pending"]
    assert panel.disabled == panel.credentials


async def test_cancelled_bare_mutation_waits_for_ssh_thread():
    from app.issuance.xray_api_client import XrayApiClient

    entered, release = threading.Event(), threading.Event()
    finished = []

    def rpc(*args):
        entered.set()
        assert release.wait(timeout=3)
        finished.append(True)
        return True, ""

    client = object.__new__(XrayApiClient)
    client._config = SimpleNamespace(xray_inbound_tag="test")
    client._api_port = 10085
    client._run_durable_mutation = lambda request: rpc("AlterInbound", "test-payload")
    task = asyncio.create_task(client._mutating_rpc("{}", durable={"action": "remove", "email": "test"}))
    assert await asyncio.to_thread(entered.wait, 2)
    task.cancel()
    await asyncio.sleep(0.05)
    assert not task.done()
    release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished == [True]


async def test_two_provider_instances_serialize_rotation_in_postgres():
    url = os.environ.get("DATABASE_URL")
    if not url:
        pytest.skip("Нужен локальный PostgreSQL")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    uid = f"rotation-{uuid.uuid4()}"
    try:
        await apply_postgres_migrations(pool, migrations_directory=Path(__file__).resolve().parents[1] / "migrations")
        await pool.execute(
            "INSERT INTO user_identities (internal_user_id, telegram_user_id) VALUES ($1, $2)",
            uid,
            -uuid.uuid4().int % -(2**60),
        )
        deadline = datetime.now(UTC) + timedelta(hours=2)
        await pool.execute(
            "INSERT INTO subscription_snapshots (internal_user_id,state_label,active_until_utc,device_count)"
            " VALUES ($1,'active',$2,9)",
            uid,
            deadline,
        )
        panel = Panel(uid)
        entered, release = asyncio.Event(), asyncio.Event()
        original_add = panel.add_client
        first_call = True

        async def delayed_add(**kwargs):
            nonlocal first_call
            if first_call:
                first_call = False
                entered.set()
                await release.wait()
            return await original_add(**kwargs)

        panel.add_client = delayed_add
        first, second = provider_for(pool, [panel]), provider_for(pool, [panel])
        task1 = asyncio.create_task(first.reissue_user(internal_user_id=uid))
        await asyncio.wait_for(entered.wait(), timeout=3)
        task2 = asyncio.create_task(second.reissue_user(internal_user_id=uid))
        await asyncio.sleep(0.2)
        assert not task2.done()
        release.set()
        results = await asyncio.wait_for(asyncio.gather(task1, task2), timeout=5)
        assert all(result.outcome == VlessProviderOutcome.SUCCESS for result in results)
        seed = await pool.fetchval("SELECT vless_key_seed FROM user_identities WHERE internal_user_id=$1", uid)
        assert panel.credentials == {vless_uuid_for_transport(uid, "tcp", seed), vless_uuid_for_transport(uid, "tcp")}
    finally:
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id=$1", uid)
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id=$1", uid)
        await pool.close()


async def test_paid_sync_uses_current_limit_and_deadline():
    pool, panel = KeyPool(), Panel()
    result = await provider_for(pool, (panel,)).sync_user_access(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.SUCCESS
    assert panel.last_limit == 9
    assert panel.last_expiry == int(pool.deadline.timestamp() * 1000)


async def test_paid_sync_keeps_partial_failure_pending():
    pool, first, second = KeyPool(), Panel(), Panel(transport="ws")
    second.add_ok = False
    provider = provider_for(pool, (first, second))
    assert (
        await provider.sync_user_access(internal_user_id="rotation-test")
    ).outcome == VlessProviderOutcome.UNAVAILABLE
    second.add_ok = True
    assert (await provider.sync_user_access(internal_user_id="rotation-test")).outcome == VlessProviderOutcome.SUCCESS


async def test_paid_sync_does_not_activate_expired_subscription():
    pool, panel = KeyPool(), Panel()
    pool.deadline = datetime.now(UTC) - timedelta(seconds=1)
    result = await provider_for(pool, (panel,)).sync_user_access(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.NOT_FOUND
    assert panel.last_expiry is None


async def test_paid_sync_resumes_rotation_before_creating_new_keys():
    pool, panel = KeyPool(), Panel()
    old = set(panel.credentials)
    pool.state.update(vless_key_seed="new-generation", vless_rotation_pending=True)
    panel.remove_ok = False
    result = await provider_for(pool, (panel,)).sync_user_access(internal_user_id="rotation-test")
    assert result.outcome == VlessProviderOutcome.UNAVAILABLE
    assert panel.credentials == old
    assert pool.state["vless_rotation_pending"]
