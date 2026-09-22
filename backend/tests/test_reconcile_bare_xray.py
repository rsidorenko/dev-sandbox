"""Unit tests for ``XuiVlessProvider.reconcile_bare_xray`` desired-state logic.

The real method fans out gRPC over SSH to bare_xray hosts; here we stub the pool
(active-user set) and the bare_xray clients, then assert the reconcile *behaviour*:
add-missing, remove-orphan, fill a wiped inbound, and skip-and-continue on a listing
failure. gRPC AlterInbound never restarts xray, so these tests also pin that the
method never reaches for a reload path (there is none here).
"""

from __future__ import annotations

import types
from unittest.mock import AsyncMock

from app.issuance.vless_provider import VlessProviderOutcome, VlessServerConfig
from app.issuance.xui_vless_provider import (
    XuiVlessProvider,
    _email_from_internal,
    _vless_uuid_for_transport,
)
from app.shared.test_helpers import run_async as _run


class _FakePool:
    """Minimal asyncpg-like pool: ``fetch`` returns a fixed list of user rows."""

    def __init__(self, users):
        self._users = users
        self.last_fetch_sql = ""

    async def fetch(self, sql, *args):
        if "WHERE vless_rotation_pending" in sql:
            return []
        self.last_fetch_sql = sql
        return self._users

    async def fetchrow(self, sql, *args):
        return None

    async def execute(self, sql, *args):
        return "OK"


class _FakeBareXrayClient:
    """Stand-in for ``XrayApiClient`` over one bare_xray inbound."""

    def __init__(self, *, server_id, tag, transport, present):
        self.server_id = server_id
        self.server_config = types.SimpleNamespace(
            transport_type=transport,
            xray_inbound_tag=tag,
            label=tag,
        )
        self._present = set(present)
        self.added: list[tuple[str, str]] = []
        self.removed: list[str] = []

    async def persist_accounts(self, accounts):
        self.persisted = dict(accounts)
        return True

    async def list_user_accounts(self):
        transport = self.server_config.transport_type
        prefix = "user-" if transport == "tcp" else "x-user-"
        return {
            email: (
                _vless_uuid_for_transport(email.removeprefix(prefix), transport),
                "xtls-rprx-vision" if transport == "tcp" else "",
            )
            for email in self._present
        }

    async def list_user_emails(self):
        return list(self._present)

    async def add_client(self, *, user_uuid, email):
        self._present.add(email)
        self.added.append((email, user_uuid))
        return True

    async def remove_client(self, *, email):
        self._present.discard(email)
        self.removed.append(email)
        return True


class _RaisingListClient(_FakeBareXrayClient):
    async def list_user_accounts(self):
        raise RuntimeError("grpcurl blew up")


def _provider(pool, clients):
    p = XuiVlessProvider(pool=pool)
    p._key_state = AsyncMock(return_value={})

    async def fake_get_bare_xray_clients():
        return clients

    p._get_bare_xray_clients = fake_get_bare_xray_clients  # type: ignore[method-assign]
    return p


def test_adds_missing_and_removes_orphans_per_inbound():
    users = [{"internal_user_id": "u1"}, {"internal_user_id": "u2"}]
    client = _FakeBareXrayClient(
        server_id=2,
        tag="cloudru-tcp",
        transport="tcp",
        present=["user-u1", "user-uGONE"],  # u1 present, u2 missing, uGONE orphan
    )
    p = _provider(_FakePool(users), [client])

    result = _run(p.reconcile_bare_xray())

    # desired = {user-u1, user-u2}; u2 added, uGONE removed, u1 untouched
    assert {e for e, _ in client.added} == {"user-u2"}
    assert client.removed == ["user-uGONE"]
    assert result["added"] == 1
    assert result["removed"] == 1
    assert result["users"] == 2
    assert result["failed_inbounds"] == 0
    # the added client carries the deterministic per-transport uuid
    assert ("user-u2", _vless_uuid_for_transport("u2", "tcp")) in client.added


def test_wiped_inbound_is_refilled():
    """A runtime-empty inbound (Helsinki clr-tcp=0) must be filled with the desired set,
    not skipped — this is the whole point of the reconcile."""
    users = [{"internal_user_id": "u1"}, {"internal_user_id": "u2"}, {"internal_user_id": "u3"}]
    client = _FakeBareXrayClient(server_id=4, tag="clr-tcp", transport="tcp", present=[])
    p = _provider(_FakePool(users), [client])

    result = _run(p.reconcile_bare_xray())

    assert {e for e, _ in client.added} == {"user-u1", "user-u2", "user-u3"}
    assert client.removed == []
    assert result["added"] == 3
    assert result["failed_inbounds"] == 0


def test_xhttp_transport_uses_x_email_prefix():
    users = [{"internal_user_id": "u7"}]
    client = _FakeBareXrayClient(server_id=3, tag="cloudru-xhttp", transport="xhttp", present=[])
    p = _provider(_FakePool(users), [client])

    result = _run(p.reconcile_bare_xray())

    assert {e for e, _ in client.added} == {_email_from_internal("u7", transport_type="xhttp")}
    assert {e for e, _ in client.added} == {"x-user-u7"}
    assert result["added"] == 1


def test_listing_failure_skip_inbound_but_do_not_abort_pass():
    users = [{"internal_user_id": "u1"}]
    bad = _RaisingListClient(server_id=1, tag="yt-tcp", transport="tcp", present=[])
    good = _FakeBareXrayClient(server_id=2, tag="cloudru-tcp", transport="tcp", present=[])
    p = _provider(_FakePool(users), [bad, good])

    result = _run(p.reconcile_bare_xray())

    # bad inbound skipped + counted; good inbound still reconciled
    assert result["failed_inbounds"] == 1
    assert {e for e, _ in good.added} == {"user-u1"}
    assert bad.added == []
    assert result["added"] == 1


def test_no_bare_xray_clients_returns_clean_summary():
    p = _provider(_FakePool([{"internal_user_id": "u1"}]), [])
    result = _run(p.reconcile_bare_xray())
    assert result == {
        "users": 1,
        "inbounds": {},
        "added": 0,
        "removed": 0,
        "failed_inbounds": 0,
    }


def test_reconcile_bare_xray_desired_set_excludes_expired_users():
    """Regression (trial/subscription expiry leak): the desired-set query must NOT
    include expired users. bare_xray is raw xray — a client is either present (carries
    traffic) or removed, there is no 'disabled' state — so an expired user kept in the
    desired set is re-added every ~3 min right after revoke_user removes them on expiry,
    and an expired trial/subscription keeps working indefinitely. The query must require
    state='active' AND a not-yet-expired entitlement timestamp (so reconcile enforces
    expiry on its own ~3-min cadence, not only after the hourly scheduler flips state)."""
    pool = _FakePool([{"internal_user_id": "u1"}])
    client = _FakeBareXrayClient(server_id=2, tag="cloudru-tcp", transport="tcp", present=[])
    p = _provider(pool, [client])
    _run(p.reconcile_bare_xray())

    sql = pool.last_fetch_sql.lower()
    assert "expired" not in sql  # expired users must NOT be in the desired set
    assert "state_label = 'active'" in sql
    assert "trial_expires_at" in sql and "active_until_utc" in sql  # entitlement-timestamp guard
    assert "now()" in sql


def test_reconcile_all_users_provisions_bare_xray_with_no_xui_panels():
    """Regression: when there are zero xui panels (fleet 100% bare_xray),
    ``reconcile_all_users`` must still call ``reconcile_bare_xray()``. Before the
    fix it early-returned on ``not clients`` (xui-only) before reaching the
    bare_xray reconcile, so startup/scheduler provisioning of bare_xray stopped
    and only the standalone 3-min timer covered it."""
    users = [
        {
            "internal_user_id": "u1",
            "state_label": "active",
            "device_count": 1,
            "active_until_utc": None,
        }
    ]
    client = _FakeBareXrayClient(server_id=8, tag="in-443-tcp", transport="tcp", present=[])
    p = _provider(_FakePool(users), [client])

    async def fake_get_clients():
        return []  # no xui panels

    p._get_clients = fake_get_clients  # type: ignore[method-assign]

    added, failed, total = _run(p.reconcile_all_users())

    assert (added, failed, total) == (0, 0, 1)
    # bare_xray reconcile still ran despite no xui panels -> user provisioned
    assert {e for e, _ in client.added} == {"user-u1"}


def test_probe_user_config_serves_bare_xray_with_no_xui_panels():
    """Regression (READ path / /sub/): when there are zero xui panels (fleet 100%
    bare_xray), ``_probe_user_config`` must still serve the deterministic bare_xray
    links — NOT bail with ``None``. ``None`` becomes UNAVAILABLE in _compute_user_config,
    which makes /sub/ return 503 for EVERY user. Before the fix the method early-returned
    on ``not clients`` (xui-only) before appending the bare_xray servers."""
    import app.issuance.xui_vless_provider as mod

    p = XuiVlessProvider(pool=_FakePool([]))

    p._key_state = AsyncMock(return_value={})

    async def fake_get_clients():
        return []  # 100% bare_xray fleet — no xui panels

    p._get_clients = fake_get_clients  # type: ignore[method-assign]
    # bare_xray rows exist (truthy) so the "no servers at all" guard does not fire;
    # _bare_xray_servers is stubbed to isolate the early-return regression from
    # link-building internals.
    p._bare_xray_configs = (types.SimpleNamespace(),)  # type: ignore[assignment]
    fake_link = VlessServerConfig(
        server_label="cloudru-tcp",
        country_code="RU",
        country_flag="🇷🇺",
        vless_link="vless://fake@cloudru:443#Cloud.ru",
        surface="happ",
    )

    async def fake_bare_servers(_uid):
        return [fake_link]

    p._bare_xray_servers = fake_bare_servers  # type: ignore[method-assign]

    async def fake_token(_pool, _uid):
        return "fake-sub-token"

    orig = mod._ensure_subscription_token
    mod._ensure_subscription_token = fake_token  # type: ignore[assignment]
    try:
        result = _run(p._probe_user_config("u1"))
    finally:
        mod._ensure_subscription_token = orig  # type: ignore[assignment]

    assert result is not None
    assert result.outcome == VlessProviderOutcome.SUCCESS
    assert result.config is not None
    assert [s.server_label for s in result.config.servers] == ["cloudru-tcp"]


def test_probe_user_config_none_only_when_no_servers_at_all():
    """Guard for the regression fix: ``None`` (→ UNAVAILABLE → /sub/ 503) must happen
    ONLY when there are zero xui panels AND zero bare_xray rows. An empty xui list with
    bare_xray rows present must NOT yield None (that was the 503-for-everyone bug)."""
    p = XuiVlessProvider(pool=_FakePool([]))
    p._key_state = AsyncMock(return_value={})

    async def fake_get_clients():
        return []

    p._get_clients = fake_get_clients  # type: ignore[method-assign]
    p._bare_xray_configs = ()  # truly nothing configured

    assert _run(p._probe_user_config("u1")) is None


def _bare_xray_only_provider():
    """Provider with zero xui panels but bare_xray rows configured — the 100% bare_xray
    fleet state. Returns (provider, lifecycle_calls) so a test can assert _bare_xray_lifecycle ran."""
    p = XuiVlessProvider(pool=_FakePool([]))
    p._key_state = AsyncMock(return_value={})

    async def fake_get_clients():
        return []

    p._get_clients = fake_get_clients  # type: ignore[method-assign]
    p._bare_xray_configs = (types.SimpleNamespace(),)  # type: ignore[assignment]

    lifecycle: list[tuple[str, str]] = []

    async def fake_lifecycle(uid, action):
        lifecycle.append((uid, action))

    p._bare_xray_lifecycle = fake_lifecycle  # type: ignore[method-assign]

    async def fake_servers(_uid):
        return [
            VlessServerConfig(
                server_label="cloudru-tcp",
                country_code="RU",
                country_flag="🇷🇺",
                vless_link="vless://fake@cloudru:443#C",
                surface="happ",
            )
        ]

    p._bare_xray_servers = fake_servers  # type: ignore[method-assign]
    return p, lifecycle


def test_create_user_provisions_bare_xray_with_no_xui_panels():
    """Regression (PROVISIONING / trial / payment / add-days): ``create_user`` must add the
    user to bare_xray inbounds and return SUCCESS when there are zero xui panels. Before the
    fix it early-returned UNAVAILABLE on ``not clients`` BEFORE calling _bare_xray_lifecycle,
    so in a 100% bare_xray fleet NO user could ever get keys (date extended, VPN dead)."""
    p, lifecycle = _bare_xray_only_provider()
    res = _run(p.create_user(internal_user_id="u1", device_count=5, expiry_days=14))
    assert res.outcome == VlessProviderOutcome.SUCCESS, res.outcome
    assert res.config is not None and len(res.config.servers) == 1
    # the whole point: bare_xray was live-provisioned (not skipped)
    assert lifecycle == [("u1", "add")], lifecycle


def test_activate_user_provisions_bare_xray_with_no_xui_panels():
    """Regression (PROVISIONING / re-enable on renewal): ``activate_user`` must re-add the
    user to bare_xray inbounds and return SUCCESS with a config (server count) even with zero
    xui panels. Without the config, the add-days handler reports "0 servers" despite success."""
    p, lifecycle = _bare_xray_only_provider()
    res = _run(p.activate_user(internal_user_id="u2", device_count=5, expiry_days=14))
    assert res.outcome == VlessProviderOutcome.SUCCESS, res.outcome
    assert res.config is not None and len(res.config.servers) == 1, "activate must return config"
    assert lifecycle == [("u2", "add")], lifecycle


def test_revoke_and_delete_bare_xray_with_no_xui_panels():
    """Regression (lifecycle cleanup): revoke_user / delete_user must remove the user from
    bare_xray inbounds even with zero xui panels (same bug class — early return skipped it)."""
    p_rev, life_rev = _bare_xray_only_provider()
    r1 = _run(p_rev.revoke_user(internal_user_id="u3"))
    assert r1.outcome == VlessProviderOutcome.SUCCESS, r1.outcome
    assert life_rev == [("u3", "remove")], life_rev

    p_del, life_del = _bare_xray_only_provider()
    r2 = _run(p_del.delete_user(internal_user_id="u4"))
    assert r2.outcome == VlessProviderOutcome.SUCCESS, r2.outcome
    assert life_del == [("u4", "remove")], life_del


def test_delete_user_reports_error_when_bare_xray_remove_failed():
    """Destructive reset не должен удалять БД, если хотя бы один inbound не очищен."""
    provider, _ = _bare_xray_only_provider()

    async def failed_lifecycle(_uid, _action):
        return 1

    provider._bare_xray_lifecycle = failed_lifecycle  # type: ignore[method-assign]

    result = _run(provider.delete_user(internal_user_id="u5"))

    assert result.outcome == VlessProviderOutcome.ERROR


def test_reconcile_repairs_rotated_uuid_restored_by_restart():
    client = _FakeBareXrayClient(server_id=2, tag="tcp", transport="tcp", present=["user-u1"])
    client.list_user_accounts = AsyncMock(return_value={"user-u1": ("old-uuid", "xtls-rprx-vision")})
    p = _provider(_FakePool([{"internal_user_id": "u1"}]), [client])
    p._uuid_for_transport = AsyncMock(return_value="rotated-uuid")

    result = _run(p.reconcile_bare_xray())

    assert client.removed == ["user-u1"]
    assert client.added == [("user-u1", "rotated-uuid")]
    assert result["failed_inbounds"] == 0


def test_reconcile_repairs_flow_and_reports_remove_failure():
    for transport, email, wrong_flow in [("tcp", "user-u1", ""), ("xhttp", "x-user-u1", "xtls-rprx-vision")]:
        client = _FakeBareXrayClient(server_id=2, tag="test", transport=transport, present=[email])
        client.list_user_accounts = AsyncMock(
            return_value={email: (_vless_uuid_for_transport("u1", transport), wrong_flow)}
        )
        p = _provider(_FakePool([{"internal_user_id": "u1"}]), [client])
        assert _run(p.reconcile_bare_xray())["added"] == 1
        assert client.removed == [email]
        client.remove_client = AsyncMock(return_value=False)
        client.added.clear()
        result = _run(p.reconcile_bare_xray())
        assert result["failed_inbounds"] == 1
        assert result["inbounds"]["2/test"]["status"] == "mutation_failed"
        assert client.added == []


def test_reconcile_retries_after_replacement_add_failure():
    client = _FakeBareXrayClient(server_id=2, tag="tcp", transport="tcp", present=["user-u1"])

    async def accounts():
        return dict.fromkeys(client._present, ("old", "xtls-rprx-vision"))

    client.list_user_accounts = accounts
    p = _provider(_FakePool([{"internal_user_id": "u1"}]), [client])
    original_add = client.add_client
    client.add_client = AsyncMock(return_value=False)
    assert _run(p.reconcile_bare_xray())["failed_inbounds"] == 1
    assert client._present == set()
    client.add_client = original_add
    assert _run(p.reconcile_bare_xray())["added"] == 1
    assert client.added == [("user-u1", _vless_uuid_for_transport("u1", "tcp"))]


def test_malformed_account_in_snapshot_prevents_all_mutations():
    import base64
    import json
    from unittest.mock import Mock

    from app.issuance.xray_api_client import XrayApiClient, encode_vless_account

    client = _FakeBareXrayClient(server_id=2, tag="tcp", transport="tcp", present=["user-u1", "orphan"])
    client._config = client.server_config
    client._run_grpcurl = Mock(
        return_value=(
            True,
            json.dumps(
                {
                    "users": [
                        {
                            "email": "user-u1",
                            "account": {
                                "type": "xray.proxy.vless.Account",
                                "value": base64.b64encode(encode_vless_account(id="old")).decode(),
                            },
                        },
                        {"email": "orphan", "account": {"type": "xray.proxy.vless.Account", "value": "broken"}},
                    ]
                }
            ),
        )
    )
    client.list_user_accounts = types.MethodType(XrayApiClient.list_user_accounts, client)
    p = _provider(_FakePool([{"internal_user_id": "u1"}]), [client])
    assert _run(p.reconcile_bare_xray())["failed_inbounds"] == 1
    assert client.added == client.removed == []


def test_reconcile_persistence_failure_prevents_live_changes():
    client = _FakeBareXrayClient(server_id=2, tag="tcp", transport="tcp", present=["orphan"])
    client.persist_accounts = AsyncMock(return_value=False)
    p = _provider(_FakePool([{"internal_user_id": "u1"}]), [client])
    result = _run(p.reconcile_bare_xray())
    assert result["failed_inbounds"] == 1
    assert result["inbounds"]["2/tcp"]["status"] == "persist_failed"
    assert client.added == client.removed == []


def test_reconcile_persists_accounts_even_when_live_already_matches():
    client = _FakeBareXrayClient(server_id=2, tag="tcp", transport="tcp", present=["user-u1"])
    p = _provider(_FakePool([{"internal_user_id": "u1"}]), [client])
    assert _run(p.reconcile_bare_xray())["failed_inbounds"] == 0
    assert client.persisted == {"user-u1": (_vless_uuid_for_transport("u1", "tcp"), "xtls-rprx-vision")}
    assert client.added == client.removed == []
