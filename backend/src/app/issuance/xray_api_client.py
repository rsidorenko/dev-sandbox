"""Bare-xray client: manages users on a ``provider_kind='bare_xray'`` server (the
Cloud.ru RU-relay entry) via xray's gRPC HandlerService, reached over SSH + grpcurl.

No 3x-ui panel, no exposed management port: the xray API listens on 127.0.0.1 and the
bot reaches it through an SSH-authenticated session that runs ``grpcurl`` on the box.

xray 26.x ``HandlerService`` exposes no ``AddUser``/``RemoveUser`` RPC. User changes go
through ``AlterInbound(tag, operation)`` where ``operation`` is a ``TypedMessage``
wrapping an ``AddUserOperation{user}`` / ``RemoveUserOperation{email}``. Via server
reflection, grpcurl accepts the outer ``TypedMessage`` as JSON
``{"type": ..., "value": <base64 protobuf>}``, so the inner operation protobuf is
hand-encoded here (the message shapes are tiny and stable — Account/User/Operation) —
no protobuf stubs and no extra dependencies. Verified end-to-end on Cloud.ru.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import shlex

from app.issuance.xui_client import REALITY_TCP_FLOW, XuiServerConfig

_LOGGER = logging.getLogger(__name__)

DEFAULT_API_PORT = 10085
_GRPCURL_TIMEOUT = 15

_HANDLER_SERVICE = "xray.app.proxyman.command.HandlerService"
_STATS_SERVICE = "xray.app.stats.command.StatsService"

# xray protobuf type URLs (stable, reflection-confirmed on xray 26.3.27).
_VLESS_ACCOUNT_TYPE = "xray.proxy.vless.Account"
_ADD_USER_OP_TYPE = "xray.app.proxyman.command.AddUserOperation"
_REMOVE_USER_OP_TYPE = "xray.app.proxyman.command.RemoveUserOperation"


# ---------------------------------------------------------------------------
# Minimal protobuf encoder for the fixed AddUserOperation / RemoveUserOperation
# shapes. Pure functions — unit-tested without SSH/grpcurl/grpcio.
# ---------------------------------------------------------------------------


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        n, rem = divmod(n, 128)
        out.append(rem | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _tag(field: int, wire: int) -> bytes:
    return _varint((field << 3) | wire)


def _enc_string(field: int, value: str) -> bytes:
    raw = value.encode("utf-8")
    return _tag(field, 2) + _varint(len(raw)) + raw


def _enc_bytes(field: int, value: bytes) -> bytes:
    return _tag(field, 2) + _varint(len(value)) + value


def _enc_uint32(field: int, value: int) -> bytes:
    return _tag(field, 0) + _varint(value)


def encode_vless_account(*, id: str, flow: str = "", encryption: str = "none") -> bytes:
    """xray.proxy.vless.Account { string id=1; string flow=2; string encryption=3; ... }."""
    return _enc_string(1, id) + _enc_string(2, flow) + _enc_string(3, encryption)


def encode_typed_message(type_url: str, value: bytes) -> bytes:
    """xray.common.serial.TypedMessage { string type=1; bytes value=2 }."""
    return _enc_string(1, type_url) + _enc_bytes(2, value)


def encode_user(*, email: str, account: bytes, level: int = 0) -> bytes:
    """xray.common.protocol.User { uint32 level=1; string email=2; TypedMessage account=3 }."""
    return _enc_uint32(1, level) + _enc_string(2, email) + _enc_bytes(3, account)


def encode_add_user_operation(*, email: str, uuid: str, flow: str = "") -> bytes:
    """xray.app.proxyman.command.AddUserOperation { User user=1 }."""
    account = encode_vless_account(id=uuid, flow=flow)
    typed_account = encode_typed_message(_VLESS_ACCOUNT_TYPE, account)
    user = encode_user(email=email, account=typed_account)
    return _enc_bytes(1, user)


def encode_remove_user_operation(*, email: str) -> bytes:
    """xray.app.proxyman.command.RemoveUserOperation { string email=1 }."""
    return _enc_string(1, email)


def alter_add_payload(*, tag: str, email: str, uuid: str, flow: str = "") -> str:
    """JSON request body for HandlerService/AlterInbound carrying an AddUserOperation."""
    op = encode_add_user_operation(email=email, uuid=uuid, flow=flow)
    return json.dumps({"tag": tag, "operation": {"type": _ADD_USER_OP_TYPE, "value": base64.b64encode(op).decode()}})


def alter_remove_payload(*, tag: str, email: str) -> str:
    """JSON request body for HandlerService/AlterInbound carrying a RemoveUserOperation."""
    op = encode_remove_user_operation(email=email)
    return json.dumps({"tag": tag, "operation": {"type": _REMOVE_USER_OP_TYPE, "value": base64.b64encode(op).decode()}})


def get_inbound_users_payload(*, tag: str) -> str:
    return json.dumps({"tag": tag})


def query_stats_payload(*, pattern: str = "user>>>", reset: bool = False) -> str:
    """JSON request body for StatsService/QueryStats.

    Per-user traffic counters are named ``user>>><email>>>traffic>>>{uplink,downlink}``;
    the ``user>>>`` prefix pattern matches exactly those (inbound/outbound/system
    counters are filtered out server-side).
    """
    return json.dumps({"pattern": [pattern], "reset": reset})


def parse_query_stats_response(out: str) -> dict[str, dict[str, int]]:
    """Parse StatsService/QueryStats JSON into ``{email: {"up": n, "down": n}}``.

    Traps handled:
    - proto3 JSON serializes int64 as a **string** — coerce via ``int()`` (a plain
      int is also accepted defensively);
    - counters absent from the response (no traffic yet) are simply missing — the
      caller treats them as zero via ``diff_deltas``;
    - non-user counters (``inbound>>>…``/``outbound>>>…``) are ignored.
    """
    try:
        data = json.loads(out) if out else {}
    except json.JSONDecodeError:
        return {}
    stats: dict[str, dict[str, int]] = {}
    for stat in data.get("stat", []):
        name = stat.get("name", "")
        parts = name.split(">>>")
        if len(parts) != 4 or parts[0] != "user" or parts[2] != "traffic":
            continue
        email, direction = parts[1], parts[3]
        if direction not in ("uplink", "downlink"):
            continue
        try:
            value = int(stat.get("value", 0))
        except (TypeError, ValueError):
            continue
        entry = stats.setdefault(email, {"up": 0, "down": 0})
        entry["up" if direction == "uplink" else "down"] = value
    return stats


def _flow_for_transport(transport_type: str) -> str:
    """Vision only for tcp+Reality; empty for xhttp/grpc/ws (vision is tcp-only)."""
    return REALITY_TCP_FLOW if transport_type == "tcp" else ""


# ---------------------------------------------------------------------------
# SSH + grpcurl client
# ---------------------------------------------------------------------------


class XrayApiClient:
    """Manages users on one bare-xray inbound via xray's gRPC API over SSH + grpcurl.

    One instance per (server, inbound/transport). The provider caches instances and
    closes them on shutdown. SSH is lazy + reconnects once on failure.
    """

    def __init__(
        self,
        config: XuiServerConfig,
        *,
        ssh_user: str,
        ssh_key: str,
        api_port: int = DEFAULT_API_PORT,
    ) -> None:
        self._config = config
        self._ssh_user = ssh_user
        self._ssh_key = ssh_key
        self._api_port = api_port
        self._ssh: object | None = None  # paramiko.SSHClient

    @property
    def server_id(self) -> int:
        return self._config.server_id

    @property
    def server_config(self) -> XuiServerConfig:
        return self._config

    @property
    def api_port(self) -> int:
        return self._api_port

    def _connect(self) -> object:
        import paramiko  # lazy — keeps module import light and test-isolated

        if self._ssh is not None:
            return self._ssh
        client = paramiko.SSHClient()
        # Cloud.ru is a single, fixed, operator-managed host; first-connect trust is
        # acceptable (equivalent to the operator ssh_run pattern). noqa picks that intent.
        client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
        client.connect(
            hostname=self._config.server_host,
            username=self._ssh_user,
            key_filename=self._ssh_key,
            look_for_keys=False,
            allow_agent=False,
            timeout=20,
        )
        self._ssh = client
        return client

    def _run_grpcurl(self, method: str, payload_json: str, *, service: str = _HANDLER_SERVICE) -> tuple[bool, str]:
        """Run grpcurl on the bare-xray host. Returns (ok, stdout). Reconnects once."""
        cmd = f"grpcurl -plaintext -d {shlex.quote(payload_json)} localhost:{self._api_port} {service}/{method}"
        for attempt in (1, 2):
            try:
                client = self._connect()
                _, stdout, stderr = client.exec_command(cmd, timeout=_GRPCURL_TIMEOUT)
                rc = stdout.channel.recv_exit_status()
                out = stdout.read().decode(errors="replace").strip()
                if rc == 0:
                    return True, out
                err = stderr.read().decode(errors="replace").strip()
                # Stale SSH channel can give odd rc — retry once on a fresh connection.
                if attempt == 1:
                    self._reset_ssh()
                    continue
                _LOGGER.warning(
                    "xray_api grpcurl %s server=%s tag=%s rc=%s out=%s err=%s",
                    method,
                    self._config.server_id,
                    self._config.xray_inbound_tag,
                    rc,
                    out,
                    err,
                )
                return False, out
            except Exception as exc:
                if attempt == 1:
                    self._reset_ssh()
                    continue
                _LOGGER.warning(
                    "xray_api %s exception server=%s tag=%s: %r",
                    method,
                    self._config.server_id,
                    self._config.xray_inbound_tag,
                    exc,
                )
                return False, ""

    def _reset_ssh(self) -> None:
        if self._ssh is not None:
            with __import__("contextlib").suppress(Exception):
                self._ssh.close()
        self._ssh = None

    async def add_client(self, *, user_uuid: str, email: str, **_ignored: object) -> bool:
        """Add (or replace) a user on this inbound. flow follows the transport."""
        payload = alter_add_payload(
            tag=self._config.xray_inbound_tag,
            email=email,
            uuid=user_uuid,
            flow=_flow_for_transport(self._config.transport_type),
        )
        ok, _ = await asyncio.to_thread(self._run_grpcurl, "AlterInbound", payload)
        return ok

    async def remove_client(self, *, email: str, **_ignored: object) -> bool:
        """Remove a user by email. Idempotent (missing user still returns ok)."""
        payload = alter_remove_payload(tag=self._config.xray_inbound_tag, email=email)
        ok, _ = await asyncio.to_thread(self._run_grpcurl, "AlterInbound", payload)
        return ok

    async def list_user_emails(self) -> list[str]:
        """Current user emails on this inbound (for reconcile / orphan detection)."""
        payload = get_inbound_users_payload(tag=self._config.xray_inbound_tag)
        ok, out = await asyncio.to_thread(self._run_grpcurl, "GetInboundUsers", payload)
        if not ok:
            return []
        try:
            data = json.loads(out) if out else {}
        except json.JSONDecodeError:
            return []
        return [u["email"] for u in data.get("users", []) if u.get("email")]

    async def query_stats(self, *, pattern: str = "user>>>", reset: bool = False) -> dict[str, dict[str, int]] | None:
        """Per-user traffic counters from StatsService/QueryStats, ``{email: {"up","down"}}``.

        Returns ``None`` on transport failure (distinguishable from "no counters yet" —
        an empty dict). Counters are per-PROCESS: they reset on xray restart, so the
        caller must diff against stored state, never treat them as monotonic totals.
        """
        payload = query_stats_payload(pattern=pattern, reset=reset)
        ok, out = await asyncio.to_thread(self._run_grpcurl, "QueryStats", payload, service=_STATS_SERVICE)
        if not ok:
            return None
        return parse_query_stats_response(out)

    async def aclose(self) -> None:
        await asyncio.to_thread(self._reset_ssh)
