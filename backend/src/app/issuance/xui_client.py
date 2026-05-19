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

_DEFAULT_TIMEOUT = 15.0
_MAX_RETRIES = 2
_RETRY_DELAY_SECONDS = 1.0
_ENV_VERIFY_SSL = "XUI_VERIFY_SSL"


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


class XuiApiClient:
    """HTTP client for a single 3x-ui panel.

    Uses a lazily-created httpx.AsyncClient for connection reuse.
    Each operation authenticates fresh to avoid stale-session issues.
    """

    def __init__(self, config: XuiServerConfig) -> None:
        self._config = config
        self._base = config.panel_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(verify=_should_verify_ssl())
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    @property
    def server_id(self) -> int:
        return self._config.server_id

    @property
    def server_config(self) -> XuiServerConfig:
        return self._config

    async def _login(self) -> bool:
        client = await self._get_client()
        try:
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
                return body.get("success", False)
            return False
        except Exception:
            _LOGGER.debug("xui login failed for server %s", self._config.server_id, exc_info=True)
            return False

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
            "flow": "xtls-rprx-vision",
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
            f"{self._base}/panel/api/inbounds/addClient",
            payload,
            user_uuid=user_uuid,
        )

    async def get_client(self, *, email: str) -> XuiClientResult:
        return await self._do_client_op(
            "GET",
            f"{self._base}/panel/api/inbounds/list",
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
            "flow": "xtls-rprx-vision",
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
            f"{self._base}/panel/api/inbounds/updateClient/{self._config.inbound_id}",
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

    async def _do_client_op(
        self,
        method: str,
        url: str,
        payload: dict | None,
        *,
        user_uuid: str | None = None,
    ) -> XuiClientResult:
        last_result = XuiClientResult(outcome=XuiOutcome.ERROR)
        for attempt in range(_MAX_RETRIES + 1):
            try:
                client = await self._get_client()
                logged_in = await self._login()
                if not logged_in:
                    return XuiClientResult(outcome=XuiOutcome.UNAUTHORIZED)
                if method == "GET":
                    resp = await client.get(url, timeout=_DEFAULT_TIMEOUT)
                else:
                    resp = await client.post(url, json=payload, timeout=_DEFAULT_TIMEOUT)
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
    if not success and resp.status_code >= 400:
        return XuiClientResult(outcome=XuiOutcome.ERROR)
    return XuiClientResult(outcome=XuiOutcome.SUCCESS, client_id=user_uuid, user_uuid=user_uuid)
