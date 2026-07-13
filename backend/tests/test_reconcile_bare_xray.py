"""Unit tests for ``XuiVlessProvider.reconcile_bare_xray`` desired-state logic.

The real method fans out gRPC over SSH to bare_xray hosts; here we stub the pool
(active-user set) and the bare_xray clients, then assert the reconcile *behaviour*:
add-missing, remove-orphan, fill a wiped inbound, and skip-and-continue on a listing
failure. gRPC AlterInbound never restarts xray, so these tests also pin that the
method never reaches for a reload path (there is none here).
"""

from __future__ import annotations

import types

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

    async def fetch(self, sql, *args):
        return self._users


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
    async def list_user_emails(self):
        raise RuntimeError("grpcurl blew up")


def _provider(pool, clients):
    p = XuiVlessProvider(pool=pool)

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
