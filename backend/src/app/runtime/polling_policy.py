"""Declarative runtime boundary for polling policy ownership."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol, runtime_checkable

if TYPE_CHECKING:
    from httpx import Timeout

RequestKind = Literal["long_poll_fetch", "ordinary_outbound"]

LONG_POLL_FETCH_REQUEST: RequestKind = "long_poll_fetch"
ORDINARY_OUTBOUND_REQUEST: RequestKind = "ordinary_outbound"

TimeoutDecisionMode = Literal["inherit_client", "override_httpx_timeout"]
BackoffDecisionMode = Literal["noop"]
RetryDecisionMode = Literal["noop"]

INHERIT_CLIENT_TIMEOUT_MODE: TimeoutDecisionMode = "inherit_client"
OVERRIDE_HTTPX_TIMEOUT_MODE: TimeoutDecisionMode = "override_httpx_timeout"


@dataclass(frozen=True, slots=True)
class PollingTimeoutDecision:
    """Explicit timeout boundary result for httpx call sites.

    inherit_client: do not set a per-request timeout override; use the enclosing
    :class:`httpx.AsyncClient` configuration (default path until explicit rollout).

    override_httpx_timeout: per-request ``timeout`` for httpx; ``httpx_timeout`` must
    be set to an :class:`httpx.Timeout` instance at the binding layer.
    """

    request_kind: RequestKind
    mode: TimeoutDecisionMode = INHERIT_CLIENT_TIMEOUT_MODE
    httpx_timeout: Timeout | None = None


@dataclass(frozen=True, slots=True)
class PollingBackoffDecision:
    """Explicit backoff boundary result; noop carries no numeric backoff."""

    request_kind: RequestKind
    mode: BackoffDecisionMode = "noop"


@dataclass(frozen=True, slots=True)
class PollingRetryDecision:
    """Explicit retry boundary result; noop carries no retry counts or timing."""

    request_kind: RequestKind
    mode: RetryDecisionMode = "noop"


__all__ = (
    "DEFAULT_POLLING_POLICY",
    "INHERIT_CLIENT_TIMEOUT_MODE",
    "LONG_POLL_FETCH_REQUEST",
    "ORDINARY_OUTBOUND_REQUEST",
    "OVERRIDE_HTTPX_TIMEOUT_MODE",
    "NoopBackoffPolicy",
    "NoopRetryPolicy",
    "NoopTimeoutPolicy",
    "PollingBackoffDecision",
    "PollingBackoffPolicy",
    "PollingPolicy",
    "PollingRetryDecision",
    "PollingRetryPolicy",
    "PollingTimeoutDecision",
    "PollingTimeoutPolicy",
    "RequestKind",
    "TimeoutDecisionMode",
    "create_default_polling_policy",
)


@runtime_checkable
class PollingTimeoutPolicy(Protocol):
    """Type boundary for future timeout policy variants."""

    kind: str

    def timeout_for_request(self, request_kind: RequestKind) -> PollingTimeoutDecision:
        """Return a timeout decision for the outbound request kind."""

        ...


@runtime_checkable
class PollingBackoffPolicy(Protocol):
    """Type boundary for future backoff policy variants."""

    kind: str

    def backoff_for_request(self, request_kind: RequestKind) -> PollingBackoffDecision:
        """Return a backoff decision for the outbound request kind."""

        ...


@runtime_checkable
class PollingRetryPolicy(Protocol):
    """Type boundary for future retry policy variants."""

    kind: str

    def retry_for_request(self, request_kind: RequestKind) -> PollingRetryDecision:
        """Return a retry decision for the outbound request kind."""

        ...


@dataclass(frozen=True, slots=True)
class NoopTimeoutPolicy:
    """Behavior-free timeout placeholder owned by runtime boundary."""

    kind: Literal["noop"] = "noop"

    def timeout_for_request(self, request_kind: RequestKind) -> PollingTimeoutDecision:
        return PollingTimeoutDecision(request_kind=request_kind)


@dataclass(frozen=True, slots=True)
class NoopBackoffPolicy:
    """Behavior-free backoff placeholder owned by runtime boundary."""

    kind: Literal["noop"] = "noop"

    def backoff_for_request(self, request_kind: RequestKind) -> PollingBackoffDecision:
        return PollingBackoffDecision(request_kind=request_kind)


@dataclass(frozen=True, slots=True)
class NoopRetryPolicy:
    """Behavior-free retry placeholder owned by runtime boundary."""

    kind: Literal["noop"] = "noop"

    def retry_for_request(self, request_kind: RequestKind) -> PollingRetryDecision:
        return PollingRetryDecision(request_kind=request_kind)


@dataclass(frozen=True, slots=True)
class PollingPolicy:
    """Single owner boundary for polling policy surface."""

    timeout: PollingTimeoutPolicy
    backoff: PollingBackoffPolicy
    retry: PollingRetryPolicy


def create_default_polling_policy() -> PollingPolicy:
    """Build a behavior-free default surface for runtime callers."""
    return PollingPolicy(
        timeout=NoopTimeoutPolicy(),
        backoff=NoopBackoffPolicy(),
        retry=NoopRetryPolicy(),
    )


DEFAULT_POLLING_POLICY = create_default_polling_policy()
