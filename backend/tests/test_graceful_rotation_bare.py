"""Два поколения на bare Xray: live, durable snapshot, трафик и отзыв."""

import pytest

from app.issuance.key_identity import key_email, vless_uuid_for_transport
from app.issuance.vless_provider import VlessProviderOutcome
from tests.test_key_rotation import KeyPool, Panel, provider_for


class Bare:
    def __init__(self):
        self.server_config = Panel().server_config
        self.server_config.server_host = "test-host"
        self.server_config.xray_inbound_tag = "test-inbound"
        self.server_id = 1
        self.api_port = 10085
        self.accounts = {"user-rotation-test": (vless_uuid_for_transport("rotation-test", "tcp"), "xtls-rprx-vision")}
        self.durable = dict(self.accounts)
        self.traffic = {}
        self.remove_ok = True
        self.persist_ok = True

    async def add_client(self, *, email, user_uuid):
        self.accounts[email] = self.durable[email] = (user_uuid, "xtls-rprx-vision")
        return True

    async def remove_client(self, *, email):
        if not self.remove_ok:
            return False
        self.accounts.pop(email, None)
        self.durable.pop(email, None)
        return True

    async def persist_accounts(self, accounts):
        if not self.persist_ok:
            return False
        self.durable = dict(accounts)
        return True

    async def list_user_accounts(self):
        return dict(self.accounts)

    async def query_stats(self):
        return self.traffic


def bare_provider(pool, bare):
    provider = provider_for(pool, [])
    provider._bare_xray_configs = (bare.server_config,)
    provider._get_bare_xray_clients.return_value = (bare,)
    return provider


async def test_bare_restart_keeps_both_until_new_traffic_then_only_current():
    pool, bare = KeyPool(), Bare()
    provider = bare_provider(pool, bare)
    original = dict(bare.accounts)
    assert (await provider.reissue_user(internal_user_id="rotation-test")).outcome == VlessProviderOutcome.SUCCESS
    new_email = key_email("rotation-test", "tcp", pool.state["vless_email_seed"])
    assert len(bare.accounts) == 2
    bare.traffic = {"user-rotation-test": {"up": 300, "down": 0}}
    await provider.reconcile_bare_xray()
    assert len(bare.durable) == 2
    bare.accounts = dict(bare.durable)
    restarted = bare_provider(pool, bare)
    await restarted.process_key_rotations()
    assert len(bare.accounts) == 2
    bare.traffic[new_email] = {"up": 0, "down": 1}
    await restarted.process_key_rotations()
    assert set(bare.accounts) == {new_email}
    assert not set(bare.accounts) & set(original)
    bare.accounts = dict(bare.durable)
    await restarted.reconcile_bare_xray()
    assert set(bare.accounts) == {new_email}


@pytest.mark.parametrize("stats", [None, {}, {"user-rotation-test": {"up": 9999}}, {"garbage": {"up": 9999}}])
async def test_missing_or_old_stats_never_retire(stats):
    pool, bare = KeyPool(), Bare()
    provider = bare_provider(pool, bare)
    await provider.reissue_user(internal_user_id="rotation-test")
    bare.traffic = stats
    await provider.process_key_rotations()
    assert len(bare.accounts) == 2
    assert pool.state["vless_rotation_phase"] == "waiting"


async def test_stale_traffic_observation_cannot_retire_another_generation():
    pool, bare = KeyPool(), Bare()
    provider = bare_provider(pool, bare)
    await provider.reissue_user(internal_user_id="rotation-test")
    await provider._advance_rotation_locked("rotation-test", "stale-seed", True)
    assert len(bare.accounts) == 2
    assert pool.state["vless_rotation_phase"] == "waiting"


async def test_stats_probe_does_not_hold_fleet_mutation_lock():
    pool, bare = KeyPool(), Bare()
    provider = bare_provider(pool, bare)
    await provider.reissue_user(internal_user_id="rotation-test")

    async def query():
        assert not provider._mutation_lock.locked()
        return {}

    bare.query_stats = query
    await provider.process_key_rotations()


async def test_standard_panel_updates_existing_keys_instead_of_duplicate_add():
    pool, panel = KeyPool(), Panel()
    original = panel.add_client

    async def strict_add(**kwargs):
        assert kwargs["email"] not in panel.accounts, "Нельзя addClient для существующего email"
        return await original(**kwargs)

    panel.add_client = strict_add
    panel.update_client = original
    provider = provider_for(pool, [panel])
    assert (await provider.reissue_user(internal_user_id="rotation-test")).outcome == VlessProviderOutcome.SUCCESS
    assert (await provider.sync_user_access(internal_user_id="rotation-test")).outcome == VlessProviderOutcome.SUCCESS
    assert len(panel.credentials) == 2


async def test_actual_legacy_panel_uuid_is_kept_until_new_traffic():
    pool, panel = KeyPool(), Panel()
    panel.accounts[panel.email] = "actual-old-uuid"
    provider = provider_for(pool, [panel])
    await provider.reissue_user(internal_user_id="rotation-test")
    assert "actual-old-uuid" in panel.credentials
    panel.traffic[key_email("rotation-test", "tcp", pool.state["vless_email_seed"])] = (1, 0)
    await provider.process_key_rotations()
    assert "actual-old-uuid" not in panel.credentials
