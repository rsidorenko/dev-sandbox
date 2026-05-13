"""Runtime orchestration entrypoints (slice 1)."""

from __future__ import annotations

from app.runtime.binding import BoundRuntimeBatchResult, process_raw_updates_with_bridge
from app.runtime.bridge import (
    BridgeRuntimeBatchResult,
    RuntimeUpdateBridge,
    bridge_runtime_updates,
)
from app.runtime.default_bridge import accept_mapping_runtime_update
from app.runtime.live_loop import (
    LoopControl,
    Slice1LivePollingLoop,
    Slice1LiveRawPollingLoop,
    run_live_polling_until_stopped,
    run_live_raw_polling_until_stopped,
)
from app.runtime.live_startup import (
    Slice1InMemoryLiveRawRuntimeBundle,
    build_slice1_in_memory_live_raw_runtime_bundle,
    build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge,
)
from app.runtime.offsets import advance_polling_offset, extract_next_offset_from_raw_updates
from app.runtime.polling import (
    PollingBatchResult,
    PollingRuntimeConfig,
    Slice1PollingRuntime,
    TelegramPollingClient,
)
from app.runtime.polling_policy import (
    DEFAULT_POLLING_POLICY,
    LONG_POLL_FETCH_REQUEST,
    ORDINARY_OUTBOUND_REQUEST,
    OVERRIDE_HTTPX_TIMEOUT_MODE,
    NoopBackoffPolicy,
    NoopRetryPolicy,
    NoopTimeoutPolicy,
    PollingBackoffDecision,
    PollingBackoffPolicy,
    PollingPolicy,
    PollingRetryDecision,
    PollingRetryPolicy,
    PollingTimeoutDecision,
    PollingTimeoutPolicy,
    RequestKind,
    create_default_polling_policy,
)
from app.runtime.raw_polling import RawPollingBatchResult, Slice1RawPollingRuntime, TelegramRawPollingClient
from app.runtime.raw_runner import Slice1RawPollingRunner, run_raw_polling_iterations
from app.runtime.raw_startup import (
    Slice1InMemoryRawRuntimeBundle,
    build_slice1_in_memory_raw_runtime_bundle,
    build_slice1_in_memory_raw_runtime_bundle_with_default_bridge,
)
from app.runtime.runner import (
    PollingRunSummary,
    Slice1PollingRunner,
    run_polling_iterations,
)
from app.runtime.startup import Slice1InMemoryRuntimeBundle, build_slice1_in_memory_runtime_bundle
from app.runtime.telegram_httpx_live_app import (
    Slice1HttpxLiveRuntimeApp,
    build_slice1_httpx_live_runtime_app,
)
from app.runtime.telegram_httpx_live_configured import (
    build_slice1_httpx_live_runtime_app_from_config,
    build_slice1_httpx_live_runtime_app_from_config_async,
)
from app.runtime.telegram_httpx_live_env import (
    build_slice1_httpx_live_runtime_app_from_env,
)
from app.runtime.telegram_httpx_live_env_loop import (
    run_slice1_httpx_live_until_stopped_from_env,
)
from app.runtime.telegram_httpx_live_env_runner import (
    run_slice1_httpx_live_iterations_from_env,
)
from app.runtime.telegram_httpx_live_loop import run_slice1_httpx_live_until_stopped
from app.runtime.telegram_httpx_live_main import run_slice1_httpx_live_from_env
from app.runtime.telegram_httpx_live_process import (
    Slice1HttpxLiveProcess,
    build_slice1_httpx_live_process_from_config_async,
    build_slice1_httpx_live_process_from_env,
    build_slice1_httpx_live_process_from_env_async,
)
from app.runtime.telegram_httpx_live_runner import run_slice1_httpx_live_iterations
from app.runtime.telegram_httpx_live_startup import (
    Slice1HttpxLiveRuntimeBundle,
    build_slice1_httpx_live_runtime_bundle,
)
from app.runtime.telegram_httpx_raw_app import (
    Slice1HttpxRawRuntimeApp,
    build_slice1_httpx_raw_runtime_app,
)
from app.runtime.telegram_httpx_raw_configured import (
    build_slice1_httpx_raw_runtime_app_from_config,
)
from app.runtime.telegram_httpx_raw_env import (
    build_slice1_httpx_raw_runtime_app_from_env,
)
from app.runtime.telegram_httpx_raw_env_runner import (
    run_slice1_httpx_raw_iterations_from_env,
)
from app.runtime.telegram_httpx_raw_process import (
    Slice1HttpxRawProcess,
    build_slice1_httpx_raw_process_from_env,
)
from app.runtime.telegram_httpx_raw_runner import run_slice1_httpx_raw_iterations
from app.runtime.telegram_httpx_raw_startup import (
    Slice1HttpxRawRuntimeBundle,
    build_slice1_httpx_raw_runtime_bundle,
)

__all__ = [
    "DEFAULT_POLLING_POLICY",
    "LONG_POLL_FETCH_REQUEST",
    "ORDINARY_OUTBOUND_REQUEST",
    "OVERRIDE_HTTPX_TIMEOUT_MODE",
    "BoundRuntimeBatchResult",
    "BridgeRuntimeBatchResult",
    "LoopControl",
    "NoopBackoffPolicy",
    "NoopRetryPolicy",
    "NoopTimeoutPolicy",
    "PollingBackoffDecision",
    "PollingBackoffPolicy",
    "PollingBatchResult",
    "PollingPolicy",
    "PollingRetryDecision",
    "PollingRetryPolicy",
    "PollingRunSummary",
    "PollingRuntimeConfig",
    "PollingTimeoutDecision",
    "PollingTimeoutPolicy",
    "RawPollingBatchResult",
    "RequestKind",
    "RuntimeUpdateBridge",
    "Slice1HttpxLiveProcess",
    "Slice1HttpxLiveRuntimeApp",
    "Slice1HttpxLiveRuntimeBundle",
    "Slice1HttpxRawProcess",
    "Slice1HttpxRawRuntimeApp",
    "Slice1HttpxRawRuntimeBundle",
    "Slice1InMemoryLiveRawRuntimeBundle",
    "Slice1InMemoryRawRuntimeBundle",
    "Slice1InMemoryRuntimeBundle",
    "Slice1LivePollingLoop",
    "Slice1LiveRawPollingLoop",
    "Slice1PollingRunner",
    "Slice1PollingRuntime",
    "Slice1RawPollingRunner",
    "Slice1RawPollingRuntime",
    "TelegramPollingClient",
    "TelegramRawPollingClient",
    "accept_mapping_runtime_update",
    "advance_polling_offset",
    "bridge_runtime_updates",
    "build_slice1_httpx_live_process_from_config_async",
    "build_slice1_httpx_live_process_from_env",
    "build_slice1_httpx_live_process_from_env_async",
    "build_slice1_httpx_live_runtime_app",
    "build_slice1_httpx_live_runtime_app_from_config",
    "build_slice1_httpx_live_runtime_app_from_config_async",
    "build_slice1_httpx_live_runtime_app_from_env",
    "build_slice1_httpx_live_runtime_bundle",
    "build_slice1_httpx_raw_process_from_env",
    "build_slice1_httpx_raw_runtime_app",
    "build_slice1_httpx_raw_runtime_app_from_config",
    "build_slice1_httpx_raw_runtime_app_from_env",
    "build_slice1_httpx_raw_runtime_bundle",
    "build_slice1_in_memory_live_raw_runtime_bundle",
    "build_slice1_in_memory_live_raw_runtime_bundle_with_default_bridge",
    "build_slice1_in_memory_raw_runtime_bundle",
    "build_slice1_in_memory_raw_runtime_bundle_with_default_bridge",
    "build_slice1_in_memory_runtime_bundle",
    "create_default_polling_policy",
    "extract_next_offset_from_raw_updates",
    "process_raw_updates_with_bridge",
    "run_live_polling_until_stopped",
    "run_live_raw_polling_until_stopped",
    "run_polling_iterations",
    "run_raw_polling_iterations",
    "run_slice1_httpx_live_from_env",
    "run_slice1_httpx_live_iterations",
    "run_slice1_httpx_live_iterations_from_env",
    "run_slice1_httpx_live_until_stopped",
    "run_slice1_httpx_live_until_stopped_from_env",
    "run_slice1_httpx_raw_iterations",
    "run_slice1_httpx_raw_iterations_from_env",
]
