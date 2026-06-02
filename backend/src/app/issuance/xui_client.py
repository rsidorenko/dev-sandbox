"""HTTP client for 3x-ui panel API: user CRUD operations on VLESS inbound."""

from __future__ import annotations

import json
import logging
import os
from asyncio import sleep as asyncio_sleep
from dataclasses import dataclass
from enum import StrEnum

import httpx

_LOGGER = logging.getLogger(__name__)

_DEFAULT_TIMEOUT = 5.0
_MAX_RETRIES = 1
_RETRY_DELAY_SECONDS = 0.5
_ENV_VERIFY_SSL = "XUI_VERIFY_SSL"
_LOGIN_SESSION_TTL_SECONDS = 300  # re-login at most every 5 minutes


def _should_verify_ssl() -> bool:
    raw = os.environ.get(_ENV_VERIFY_SSL, "1").strip().lower()
    return raw not in ("0", "false", "no")


class XuiOutcome(StrEnum):
    SUCCESS = "success"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    UNAVAILABLE = "unavailable"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class XuiClientResult:
    outcome: XuiOutcome
    client_id: str | None = None
    user_uuid: str | None = None
    panel_uuid: str | None = None


@dataclass(frozen=True, slots=True)
class XuiServerConfig:
    """Connection details for a single 3x-ui panel."""

    server_id: int
    label: str
    country_code: str
    country_flag: str
    server_host: str
    server_port: int
    ws_path: str
    tls_sni: str | None
    panel_url: str
    panel_username: str
    panel_password: str
    inbound_id: int
    reality_pbk: str = ""
    reality_sid: str = ""
    reality_sni: str = ""
    transport_type: str = "tcp"
    api_token: str = ""


class XuiApiClient:
    """HTTP client for a single 3x-ui panel.

    Uses a lazily-created httpx.AsyncClient with connection pool limits.
    Supports both session login (username/password) and Bearer token auth.
    Session cookies are cached — login is skipped if a recent session exists.

    Handles both legacy API (addClient endpoint) and v3+ API (update-based
    read-modify-write) automatically.
    """

    def __init__(self, config: XuiServerConfig) -> None:
        self._config = config
        self._base = config.panel_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._last_login_ts: float = 0.0
        self._v3_mode: bool | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                verify=_should_verify_ssl(),
                limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        self._last_login_ts = 0.0

    @property
    def server_id(self) -> int:
        return self._config.server_id

    @property
    def server_config(self) -> XuiServerConfig:
        return self._config

    async def _login(self) -> bool:
        import time

        client = await self._get_client()
        try:
            if self._config.api_token:
                resp = await client.get(
                    f"{self._base}/panel/api/inbounds/list",
                    headers={"Authorization": f"Bearer {self._config.api_token}"},
                    timeout=_DEFAULT_TIMEOUT,
                )
                if resp.status_code == 200:
                    self._last_login_ts = time.monotonic()
                    return True
                return False
            await client.get(f"{self._base}/", timeout=_DEFAULT_TIMEOUT)
            resp = await client.post(
                f"{self._base}/login",
                data={
                    "username": self._config.panel_username,
                    "password": self._config.panel_password,
                },
                timeout=_DEFAULT_TIMEOUT,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success", False):
                    self._last_login_ts = time.monotonic()
                    return True
            return False
        except Exception:
            _LOGGER.debug("xui login failed for server %s", self._config.server_id, exc_info=True)
            return False

    async def _ensure_session(self) -> bool:
        """Login only if session is stale or missing. Returns True if session is valid."""
        import time

        if time.monotonic() - self._last_login_ts < _LOGIN_SESSION_TTL_SECONDS:
            return True
        return await self._login()

    async def add_client(
        self,
        *,
        user_uuid: str,
        email: str,
        expiry_ts: int,
        enable: bool = True,
        limit_ip: int = 0,
    ) -> XuiClientResult:
        settings = {
            "id": user_uuid,
            "email": email,
            "enable": enable,
            "expiryTime": expiry_ts,
            "flow": "xtls-rprx-vision" if self._config.transport_type == "tcp" else "",
            "limitIp": limit_ip,
            "totalGB": 0,
            "tgId": "",
            "subId": "",
        }
        result = await self._do_client_op(
            "POST",
            f"{self._base}/panel/api/inbounds/addClient",
            {"id": self._config.inbound_id, "settings": f'{{"clients": [{json.dumps(settings, separators=(",", ":"))}]}}'},
            user_uuid=user_uuid,
        )
        if result.outcome != XuiOutcome.NOT_FOUND:
            return result
        # Fallback: v3+ panels lack addClient — use read-modify-write via update
        self._v3_mode = True
        return await self._add_client_via_update(settings)

    async def get_client(self, *, email: str) -> XuiClientResult:
        return await self._do_client_op(
            "GET",
            f"{self._base}/panel/api/inbounds/list",
            None,
        )

    async def get_client_traffics(self, *, email: str) -> XuiClientResult:
        """Check if a specific client exists by email via 3x-ui traffics endpoint."""
        return await self._do_client_op(
            "GET",
            f"{self._base}/panel/api/inbounds/getClientTraffics/{email}",
            None,
        )

    async def update_client(
        self,
        *,
        user_uuid: str,
        email: str,
        enable: bool,
        expiry_ts: int,
        limit_ip: int = 0,
    ) -> XuiClientResult:
        settings = {
            "id": user_uuid,
            "email": email,
            "enable": enable,
            "expiryTime": expiry_ts,
            "flow": "xtls-rprx-vision" if self._config.transport_type == "tcp" else "",
            "limitIp": limit_ip,
            "totalGB": 0,
            "tgId": "",
            "subId": "",
        }
        payload = {
            "id": self._config.inbound_id,
            "settings": f'{{"clients": [{json.dumps(settings, separators=(",", ":"))}]}}',
        }
        return await self._do_client_op(
            "POST",
            f"{self._base}/panel/api/inbounds/updateClient/{user_uuid}",
            payload,
            user_uuid=user_uuid,
        )

    async def delete_client(self, *, user_uuid: str) -> XuiClientResult:
        return await self._do_client_op(
            "POST",
            f"{self._base}/panel/api/inbounds/{self._config.inbound_id}/delClient/{user_uuid}",
            None,
            user_uuid=user_uuid,
        )

    async def disable_client(self, *, user_uuid: str, email: str, expiry_ts: int, limit_ip: int = 0) -> XuiClientResult:
        return await self.update_client(
            user_uuid=user_uuid, email=email, enable=False, expiry_ts=expiry_ts, limit_ip=limit_ip
        )

    async def enable_client(self, *, user_uuid: str, email: str, expiry_ts: int, limit_ip: int = 0) -> XuiClientResult:
        return await self.update_client(
            user_uuid=user_uuid, email=email, enable=True, expiry_ts=expiry_ts, limit_ip=limit_ip
        )

    async def restart_xray(self) -> bool:
        """Force 3x-ui to regenerate xray config and restart xray-core."""
        if self._v3_mode:
            return True  # v3 panels may lack this endpoint; wrapper handles config
        headers = self._auth_headers()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                if not await self._ensure_session():
                    return False
                resp = await client.post(
                    f"{self._base}/panel/setting/restartXray",
                    headers=headers,
                    timeout=10.0,
                )
                if resp.status_code == 401 and attempt == 0:
                    self._last_login_ts = 0.0
                    continue
                if resp.status_code == 200:
                    body = resp.json()
                    return body.get("success", False)
                return False
            except Exception:
                _LOGGER.debug(
                    "xray restart failed server=%s attempt=%s",
                    self._config.server_id, attempt, exc_info=True,
                )
                if attempt < _MAX_RETRIES:
                    await asyncio_sleep(_RETRY_DELAY_SECONDS)
        return False

    async def resolve_client_uuid(self, *, email: str) -> str | None:
        """Resolve actual client UUID from panel by email. Returns None if not found."""
        if self._v3_mode:
            return await self._resolve_client_uuid_v3(email=email)
        headers = self._auth_headers()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                if not await self._ensure_session():
                    return None
                resp = await client.get(
                    f"{self._base}/panel/api/inbounds/getClientTraffics/{email}",
                    headers=headers,
                    timeout=_DEFAULT_TIMEOUT,
                )
                if resp.status_code == 401 and attempt == 0:
                    self._last_login_ts = 0.0
                    continue
                if resp.status_code == 404:
                    # Might be v3 panel — try fallback
                    self._v3_mode = True
                    return await self._resolve_client_uuid_v3(email=email)
                if resp.status_code >= 400:
                    return None
                body = resp.json()
                if body.get("success") and body.get("obj"):
                    return body["obj"].get("uuid")
                return None
            except Exception:
                if attempt < _MAX_RETRIES:
                    await asyncio_sleep(_RETRY_DELAY_SECONDS)
        return None

    async def _get_inbound(self) -> dict | None:
        """Fetch full inbound object from panel (v3 compatible)."""
        headers = self._auth_headers()
        try:
            client = await self._get_client()
            if not await self._ensure_session():
                return None
            resp = await client.get(
                f"{self._base}/panel/api/inbounds/get/{self._config.inbound_id}",
                headers=headers, timeout=_DEFAULT_TIMEOUT,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success"):
                    return body["obj"]
        except Exception:
            _LOGGER.debug("get_inbound failed server=%s", self._config.server_id, exc_info=True)
        return None

    async def _add_client_via_update(self, client_settings: dict) -> XuiClientResult:
        """Add client via inbound update (v3 panels without addClient endpoint)."""
        inbound = await self._get_inbound()
        if not inbound:
            return XuiClientResult(outcome=XuiOutcome.UNAVAILABLE)
        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)
        settings.setdefault("clients", []).append(client_settings)
        payload = {
            "id": inbound["id"],
            "settings": json.dumps(settings),
            "streamSettings": json.dumps(inbound["streamSettings"]) if isinstance(inbound.get("streamSettings"), dict) else inbound.get("streamSettings", ""),
            "sniffing": json.dumps(inbound["sniffing"]) if isinstance(inbound.get("sniffing"), dict) else inbound.get("sniffing", ""),
            "protocol": inbound["protocol"],
            "port": inbound["port"],
            "listen": inbound.get("listen", ""),
            "tag": inbound.get("tag", ""),
            "remark": inbound.get("remark", ""),
            "enable": inbound.get("enable", True),
            "expiryTime": inbound.get("expiryTime", 0),
            "total": inbound.get("total", 0),
            "up": inbound.get("up", 0),
            "down": inbound.get("down", 0),
        }
        return await self._do_client_op(
            "POST",
            f"{self._base}/panel/api/inbounds/update/{self._config.inbound_id}",
            payload,
            user_uuid=client_settings.get("id"),
        )

    async def _resolve_client_uuid_v3(self, *, email: str) -> str | None:
        """Resolve client UUID by fetching inbound and searching clients (v3 fallback)."""
        inbound = await self._get_inbound()
        if not inbound:
            return None
        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)
        for c in settings.get("clients", []):
            if c.get("email") == email:
                return c.get("id")
        return None

    def _auth_headers(self) -> dict[str, str]:
        if self._config.api_token:
            return {"Authorization": f"Bearer {self._config.api_token}"}
        return {}

    async def _do_client_op(
        self,
        method: str,
        url: str,
        payload: dict | None,
        *,
        user_uuid: str | None = None,
    ) -> XuiClientResult:
        last_result = XuiClientResult(outcome=XuiOutcome.ERROR)
        headers = self._auth_headers()
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                # Try with cached session first; re-login only on 401
                if not await self._ensure_session():
                    return XuiClientResult(outcome=XuiOutcome.UNAUTHORIZED)
                if method == "GET":
                    resp = await client.get(url, headers=headers, timeout=_DEFAULT_TIMEOUT)
                else:
                    resp = await client.post(url, json=payload, headers=headers, timeout=_DEFAULT_TIMEOUT)
                # If session expired mid-request, force re-login and retry once
                if resp.status_code == 401 and attempt == 0:
                    self._last_login_ts = 0.0
                    _LOGGER.debug("xui session expired, re-login server=%s", self._config.server_id)
                    continue
                result = _map_response(resp, user_uuid=user_uuid)
                if result.outcome != XuiOutcome.UNAVAILABLE or attempt == _MAX_RETRIES:
                    return result
                last_result = result
            except httpx.ConnectError:
                _LOGGER.debug("xui connect error server=%s attempt=%s", self._config.server_id, attempt, exc_info=True)
                last_result = XuiClientResult(outcome=XuiOutcome.UNAVAILABLE)
            except Exception:
                _LOGGER.debug("xui op error server=%s attempt=%s", self._config.server_id, attempt, exc_info=True)
                last_result = XuiClientResult(outcome=XuiOutcome.ERROR)
            if attempt < _MAX_RETRIES:
                await asyncio_sleep(_RETRY_DELAY_SECONDS)
        return last_result


def _map_response(resp: httpx.Response, *, user_uuid: str | None = None) -> XuiClientResult:
    if resp.status_code == 401:
        return XuiClientResult(outcome=XuiOutcome.UNAUTHORIZED)
    if resp.status_code == 404:
        return XuiClientResult(outcome=XuiOutcome.NOT_FOUND)
    if resp.status_code in (409, 422):
        return XuiClientResult(outcome=XuiOutcome.CONFLICT)
    if resp.status_code >= 500:
        return XuiClientResult(outcome=XuiOutcome.UNAVAILABLE)
    try:
        body = resp.json()
    except Exception:
        return XuiClientResult(outcome=XuiOutcome.ERROR)
    success = body.get("success", False)
    if not success:
        return XuiClientResult(outcome=XuiOutcome.ERROR)
    return XuiClientResult(outcome=XuiOutcome.SUCCESS, client_id=user_uuid, user_uuid=user_uuid)
