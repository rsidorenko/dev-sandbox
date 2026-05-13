"""Tests for runtime polling policy boundary module."""

from __future__ import annotations

import importlib
import os
import sys


def test_noop_timeout_policy_is_request_kind_aware_and_behavior_free() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")

    noop = pp.NoopTimeoutPolicy()
    assert isinstance(noop, pp.PollingTimeoutPolicy)

    r_long = noop.timeout_for_request(pp.LONG_POLL_FETCH_REQUEST)
    r_ord = noop.timeout_for_request(pp.ORDINARY_OUTBOUND_REQUEST)
    assert isinstance(r_long, pp.PollingTimeoutDecision)
    assert isinstance(r_ord, pp.PollingTimeoutDecision)
    assert r_long.mode == pp.INHERIT_CLIENT_TIMEOUT_MODE and r_ord.mode == pp.INHERIT_CLIENT_TIMEOUT_MODE
    assert r_long.request_kind == pp.LONG_POLL_FETCH_REQUEST
    assert r_ord.request_kind == pp.ORDINARY_OUTBOUND_REQUEST
    assert r_long != r_ord


def test_polling_timeout_decision_construction() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    d = pp.PollingTimeoutDecision(request_kind=pp.ORDINARY_OUTBOUND_REQUEST)
    assert d.request_kind == pp.ORDINARY_OUTBOUND_REQUEST
    assert d.mode == pp.INHERIT_CLIENT_TIMEOUT_MODE
    assert d.httpx_timeout is None


def test_override_httpx_timeout_mode_decision_stores_payload() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    sentinel = object()
    d = pp.PollingTimeoutDecision(
        request_kind=pp.LONG_POLL_FETCH_REQUEST,
        mode=pp.OVERRIDE_HTTPX_TIMEOUT_MODE,
        httpx_timeout=sentinel,
    )
    assert d.mode == pp.OVERRIDE_HTTPX_TIMEOUT_MODE
    assert d.httpx_timeout is sentinel


def test_polling_timeout_decision_runtime_reexport_identity() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    rt = importlib.import_module("app.runtime")
    assert pp.PollingTimeoutDecision is rt.PollingTimeoutDecision


def test_noop_backoff_policy_is_request_kind_aware_and_returns_explicit_decision() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")

    noop = pp.NoopBackoffPolicy()
    assert isinstance(noop, pp.PollingBackoffPolicy)

    r_long = noop.backoff_for_request(pp.LONG_POLL_FETCH_REQUEST)
    r_ord = noop.backoff_for_request(pp.ORDINARY_OUTBOUND_REQUEST)
    assert isinstance(r_long, pp.PollingBackoffDecision)
    assert isinstance(r_ord, pp.PollingBackoffDecision)
    assert r_long.mode == "noop" and r_ord.mode == "noop"
    assert r_long.request_kind == pp.LONG_POLL_FETCH_REQUEST
    assert r_ord.request_kind == pp.ORDINARY_OUTBOUND_REQUEST
    assert r_long != r_ord


def test_polling_backoff_decision_construction() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    d = pp.PollingBackoffDecision(request_kind=pp.ORDINARY_OUTBOUND_REQUEST)
    assert d.request_kind == pp.ORDINARY_OUTBOUND_REQUEST
    assert d.mode == "noop"


def test_polling_backoff_decision_runtime_reexport_identity() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    rt = importlib.import_module("app.runtime")
    assert pp.PollingBackoffDecision is rt.PollingBackoffDecision


def test_noop_retry_policy_is_request_kind_aware_and_returns_explicit_decision() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")

    noop = pp.NoopRetryPolicy()
    assert isinstance(noop, pp.PollingRetryPolicy)

    r_long = noop.retry_for_request(pp.LONG_POLL_FETCH_REQUEST)
    r_ord = noop.retry_for_request(pp.ORDINARY_OUTBOUND_REQUEST)
    assert isinstance(r_long, pp.PollingRetryDecision)
    assert isinstance(r_ord, pp.PollingRetryDecision)
    assert r_long.mode == "noop" and r_ord.mode == "noop"
    assert r_long.request_kind == pp.LONG_POLL_FETCH_REQUEST
    assert r_ord.request_kind == pp.ORDINARY_OUTBOUND_REQUEST
    assert r_long != r_ord


def test_polling_retry_decision_construction() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    d = pp.PollingRetryDecision(request_kind=pp.ORDINARY_OUTBOUND_REQUEST)
    assert d.request_kind == pp.ORDINARY_OUTBOUND_REQUEST
    assert d.mode == "noop"


def test_polling_retry_decision_runtime_reexport_identity() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    rt = importlib.import_module("app.runtime")
    assert pp.PollingRetryDecision is rt.PollingRetryDecision


def test_polling_policy_import_and_default_construction_smoke() -> None:
    module = importlib.import_module("app.runtime.polling_policy")

    default_policy = module.create_default_polling_policy()
    assert isinstance(default_policy, module.PollingPolicy)
    assert isinstance(default_policy.timeout, module.PollingTimeoutPolicy)
    assert isinstance(default_policy.backoff, module.PollingBackoffPolicy)
    assert isinstance(default_policy.retry, module.PollingRetryPolicy)
    assert default_policy == module.DEFAULT_POLLING_POLICY


def test_noop_timeout_decision_is_inherit_client_semantics() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")

    assert pp.INHERIT_CLIENT_TIMEOUT_MODE == "inherit_client"
    assert pp.OVERRIDE_HTTPX_TIMEOUT_MODE == "override_httpx_timeout"
    d = pp.NoopTimeoutPolicy().timeout_for_request(pp.LONG_POLL_FETCH_REQUEST)
    assert d.mode == "inherit_client"
    assert d.request_kind == pp.LONG_POLL_FETCH_REQUEST
    assert d.httpx_timeout is None


def test_polling_policy_module_public_names_are_stable() -> None:
    module = importlib.import_module("app.runtime.polling_policy")

    assert set(module.__all__) == {
        "PollingPolicy",
        "PollingTimeoutPolicy",
        "PollingTimeoutDecision",
        "TimeoutDecisionMode",
        "INHERIT_CLIENT_TIMEOUT_MODE",
        "OVERRIDE_HTTPX_TIMEOUT_MODE",
        "PollingBackoffDecision",
        "PollingBackoffPolicy",
        "PollingRetryDecision",
        "PollingRetryPolicy",
        "NoopTimeoutPolicy",
        "NoopBackoffPolicy",
        "NoopRetryPolicy",
        "RequestKind",
        "LONG_POLL_FETCH_REQUEST",
        "ORDINARY_OUTBOUND_REQUEST",
        "create_default_polling_policy",
        "DEFAULT_POLLING_POLICY",
    }


def test_polling_policy_default_surface_has_no_env_or_httpx_dependency() -> None:
    module_name = "app.runtime.polling_policy"
    existing_module = sys.modules.pop(module_name, None)
    before_env = dict(os.environ)
    before_httpx = {name for name in sys.modules if name == "httpx" or name.startswith("httpx.")}
    try:
        module = importlib.import_module(module_name)
        policy = module.create_default_polling_policy()
    finally:
        if existing_module is not None:
            sys.modules[module_name] = existing_module
        else:
            sys.modules.pop(module_name, None)

    after_env = dict(os.environ)
    after_httpx = {name for name in sys.modules if name == "httpx" or name.startswith("httpx.")}
    assert before_env == after_env
    assert after_httpx == before_httpx
    assert isinstance(policy, module.PollingPolicy)


_POLLING_POLICY_BOUNDARY_NAMES = (
    "PollingPolicy",
    "PollingTimeoutPolicy",
    "PollingTimeoutDecision",
    "PollingBackoffDecision",
    "PollingBackoffPolicy",
    "PollingRetryDecision",
    "PollingRetryPolicy",
    "NoopTimeoutPolicy",
    "NoopBackoffPolicy",
    "NoopRetryPolicy",
    "RequestKind",
    "LONG_POLL_FETCH_REQUEST",
    "ORDINARY_OUTBOUND_REQUEST",
    "OVERRIDE_HTTPX_TIMEOUT_MODE",
    "create_default_polling_policy",
    "DEFAULT_POLLING_POLICY",
)


def test_request_kind_vocabulary_imports_and_runtime_identity() -> None:
    pp = importlib.import_module("app.runtime.polling_policy")
    rt = importlib.import_module("app.runtime")

    assert pp.RequestKind is rt.RequestKind
    assert pp.LONG_POLL_FETCH_REQUEST is rt.LONG_POLL_FETCH_REQUEST
    assert pp.ORDINARY_OUTBOUND_REQUEST is rt.ORDINARY_OUTBOUND_REQUEST

    assert pp.LONG_POLL_FETCH_REQUEST == "long_poll_fetch"
    assert pp.ORDINARY_OUTBOUND_REQUEST == "ordinary_outbound"
    assert pp.LONG_POLL_FETCH_REQUEST != pp.ORDINARY_OUTBOUND_REQUEST

    assert "RequestKind" in pp.__all__
    assert "LONG_POLL_FETCH_REQUEST" in pp.__all__
    assert "ORDINARY_OUTBOUND_REQUEST" in pp.__all__
    assert "PollingTimeoutDecision" in pp.__all__
    assert "PollingBackoffDecision" in pp.__all__
    assert "PollingRetryDecision" in pp.__all__
    assert "RequestKind" in rt.__all__
    assert "LONG_POLL_FETCH_REQUEST" in rt.__all__
    assert "ORDINARY_OUTBOUND_REQUEST" in rt.__all__
    assert "PollingTimeoutDecision" in rt.__all__
    assert "PollingBackoffDecision" in rt.__all__
    assert "PollingRetryDecision" in rt.__all__


def test_runtime_reexports_polling_policy_boundary_by_identity() -> None:
    rt = importlib.import_module("app.runtime")
    pp = importlib.import_module("app.runtime.polling_policy")
    for name in _POLLING_POLICY_BOUNDARY_NAMES:
        assert getattr(rt, name) is getattr(pp, name)


def test_runtime_all_includes_polling_policy_boundary_names() -> None:
    rt = importlib.import_module("app.runtime")
    for name in _POLLING_POLICY_BOUNDARY_NAMES:
        assert name in rt.__all__
