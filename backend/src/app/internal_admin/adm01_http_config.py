"""Optional ADM-01 internal HTTP listener config (env → typed settings; no socket, no ASGI server).

Aligned with :mod:`app.security` patterns: :exc:`ConfigurationError` messages name fields only.

Default port ``18081`` is not fixed in the ADR; it is a reserved, internal off-by-default
placeholder for a future local-only bind when a production listener is implemented in a
separate slice.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from app.security.config import ConfigurationError

# Intentional internal default; ADR 34 defers the concrete port to deployment.
_DEFAULT_BIND_PORT = 18081
_DEFAULT_BIND_HOST = "127.0.0.1"

_ENV_ENABLE = "ADM01_INTERNAL_HTTP_ENABLE"
_ENV_BIND_HOST = "ADM01_INTERNAL_HTTP_BIND_HOST"
_ENV_BIND_PORT = "ADM01_INTERNAL_HTTP_BIND_PORT"
_ENV_INSECURE_ALL = "ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES"
_ENV_TRUST_REVERSE_PROXY = "ADM01_INTERNAL_HTTP_TRUST_REVERSE_PROXY"
_ENV_REQUIRE_MTLS = "ADM01_INTERNAL_HTTP_REQUIRE_MTLS"

_TRUE = frozenset({"1", "true", "yes"})
_FALSE = frozenset(("", "0", "false", "no"))


def _lookup(env: Mapping[str, str], key: str) -> str | None:
    if key not in env:
        return None
    return env[key]


def _parse_bool(*, name: str, env: Mapping[str, str], key: str) -> bool:
    raw = _lookup(env, key)
    if raw is None:
        return False
    s = raw.strip().lower()
    if s in _TRUE:
        return True
    if s in _FALSE:
        return False
    msg = f"invalid boolean configuration: {name}"
    raise ConfigurationError(msg)


def _parse_port(*, name: str, raw: str) -> int:
    try:
        port = int(raw.strip(), 10)
    except ValueError:
        msg = f"invalid configuration: {name}"
        raise ConfigurationError(msg) from None
    if not 1 <= port <= 65535:
        msg = f"invalid configuration: {name}"
        raise ConfigurationError(msg)
    return port


def _is_loopback_host(host: str) -> bool:
    h = host.strip().lower()
    return h in {"127.0.0.1", "localhost", "::1"}


def _is_all_interfaces_host(host: str) -> bool:
    s = host.strip()
    if s == "0.0.0.0":  # noqa: S104 — intentional check for all-interfaces binding
        return True
    return s in {"::", "[::]"}


@dataclass(frozen=True, slots=True)
class Adm01InternalHttpConfig:
    """Settings for a future ADM-01 internal HTTP process (listener not implemented in this module)."""

    enabled: bool
    bind_host: str
    bind_port: int
    bind_insecure_all_interfaces: bool
    trust_reverse_proxy: bool
    require_mtls: bool


def validate_adm01_internal_http_config(config: Adm01InternalHttpConfig) -> Adm01InternalHttpConfig:
    """
    Enforce ADR-34-style bind and transport-trust policy when the listener is enabled.
    When disabled, host/trust all-interface rules are not applied (config is inert for bind).
    """
    if not 1 <= config.bind_port <= 65535:
        msg = f"invalid configuration: {_ENV_BIND_PORT}"
        raise ConfigurationError(msg)

    host = config.bind_host
    if not config.enabled:
        return config

    if not host.strip():
        msg = f"empty configuration: {_ENV_BIND_HOST}"
        raise ConfigurationError(msg)
    if _is_all_interfaces_host(host) and not config.bind_insecure_all_interfaces:
        msg = f"insecure bind blocked without opt-in: {_ENV_INSECURE_ALL}"
        raise ConfigurationError(msg)
    if not _is_loopback_host(host) and not (config.trust_reverse_proxy or config.require_mtls):
        msg = (
            "non-loopback internal HTTP bind requires transport trust: "
            f"{_ENV_TRUST_REVERSE_PROXY} and/or {_ENV_REQUIRE_MTLS}"
        )
        raise ConfigurationError(msg)
    return config


def load_adm01_internal_http_config_from_env(
    env: Mapping[str, str] | None = None,
) -> Adm01InternalHttpConfig:
    m: Mapping[str, str] = os.environ if env is None else env

    enabled = _parse_bool(name=_ENV_ENABLE, env=m, key=_ENV_ENABLE)
    bind_insecure = _parse_bool(name=_ENV_INSECURE_ALL, env=m, key=_ENV_INSECURE_ALL)
    trust_rp = _parse_bool(name=_ENV_TRUST_REVERSE_PROXY, env=m, key=_ENV_TRUST_REVERSE_PROXY)
    require_mtls = _parse_bool(name=_ENV_REQUIRE_MTLS, env=m, key=_ENV_REQUIRE_MTLS)

    host_raw = _lookup(m, _ENV_BIND_HOST)
    if host_raw is None:
        bind_host = _DEFAULT_BIND_HOST
    else:
        host_st = host_raw.strip()
        if not host_st:
            if enabled:
                msg = f"empty configuration: {_ENV_BIND_HOST}"
                raise ConfigurationError(msg)
            bind_host = _DEFAULT_BIND_HOST
        else:
            bind_host = host_st

    port_raw = _lookup(m, _ENV_BIND_PORT)
    bind_port = _DEFAULT_BIND_PORT if port_raw is None else _parse_port(name=_ENV_BIND_PORT, raw=port_raw)

    cfg = Adm01InternalHttpConfig(
        enabled=enabled,
        bind_host=bind_host,
        bind_port=bind_port,
        bind_insecure_all_interfaces=bind_insecure,
        trust_reverse_proxy=trust_rp,
        require_mtls=require_mtls,
    )
    return validate_adm01_internal_http_config(cfg)


__all__ = [
    "Adm01InternalHttpConfig",
    "load_adm01_internal_http_config_from_env",
    "validate_adm01_internal_http_config",
]
