"""Thin slice-1 raw wiring: :class:`HttpxTelegramRawPollingClient` + in-memory raw bundle."""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from app.application.bootstrap import Slice1Composition
from app.runtime.polling import PollingRuntimeConfig
from app.runtime.polling_policy import DEFAULT_POLLING_POLICY, PollingPolicy
from app.runtime.raw_startup import (
    Slice1InMemoryRawRuntimeBundle,
    build_slice1_in_memory_raw_runtime_bundle_with_default_bridge,
)
from app.runtime.telegram_httpx_raw_client import HttpxTelegramRawPollingClient


@dataclass(frozen=True, slots=True)
class Slice1HttpxRawRuntimeBundle:
    client: HttpxTelegramRawPollingClient
    bundle: Slice1InMemoryRawRuntimeBundle

    async def aclose(self) -> None:
        await self.client.aclose()


def build_slice1_httpx_raw_runtime_bundle(
    bot_token: str,
    *,
    composition: Slice1Composition | None = None,
    config: PollingRuntimeConfig | None = None,
    base_url: str | None = None,
    client: httpx.AsyncClient | None = None,
    polling_policy: PollingPolicy = DEFAULT_POLLING_POLICY,
) -> Slice1HttpxRawRuntimeBundle:
    httpx_telegram = HttpxTelegramRawPollingClient(
        bot_token,
        base_url=base_url,
        client=client,
        polling_policy=polling_policy,
    )
    inner = build_slice1_in_memory_raw_runtime_bundle_with_default_bridge(
        httpx_telegram,
        config=config,
        composition=composition,
    )
    return Slice1HttpxRawRuntimeBundle(client=httpx_telegram, bundle=inner)
