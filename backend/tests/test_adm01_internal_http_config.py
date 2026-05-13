"""Tests for ADM-01 internal HTTP env config (no listener, no network)."""

from __future__ import annotations

import pytest

from app.internal_admin.adm01_http_config import (
    _DEFAULT_BIND_PORT,
    Adm01InternalHttpConfig,
    load_adm01_internal_http_config_from_env,
    validate_adm01_internal_http_config,
)
from app.security.config import ConfigurationError

_SECRET_LIKE = "XSECRET_TOKEN_VALUE_SHOULD_NEVER_APPEAR_IN_STRINGS_992"


def test_default_env_disabled_and_safe_defaults() -> None:
    cfg = load_adm01_internal_http_config_from_env({})
    assert cfg.enabled is False
    assert cfg.bind_host == "127.0.0.1"
    assert cfg.bind_port == _DEFAULT_BIND_PORT
    assert cfg.bind_insecure_all_interfaces is False
    assert cfg.trust_reverse_proxy is False
    assert cfg.require_mtls is False


def test_enabled_with_defaults_is_valid_loopback() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "1",
        },
    )
    assert cfg.enabled is True
    assert cfg.bind_host == "127.0.0.1"
    assert validate_adm01_internal_http_config(cfg) is cfg


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("yes", True),
        ("", False),
        ("0", False),
        ("false", False),
        ("no", False),
    ],
)
def test_boolean_parsing(
    value: str,
    expected: bool,
) -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": value,
        },
    )
    assert cfg.enabled is expected


def test_invalid_boolean_does_not_echo_value() -> None:
    e = {
        "ADM01_INTERNAL_HTTP_TRUST_REVERSE_PROXY": _SECRET_LIKE,
    }
    with pytest.raises(
        ConfigurationError,
        match=r"^invalid boolean configuration: ADM01_INTERNAL_HTTP_TRUST_REVERSE_PROXY$",
    ) as exc:
        load_adm01_internal_http_config_from_env(e)
    assert _SECRET_LIKE not in str(exc.value)


def test_invalid_port_rejected() -> None:
    with pytest.raises(ConfigurationError, match="invalid configuration: ADM01_INTERNAL_HTTP_BIND_PORT"):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_BIND_PORT": "not_a_port",
            },
        )


@pytest.mark.parametrize("bad", ("0", "65536", "-1"))
def test_port_out_of_range_rejected(bad: str) -> None:
    with pytest.raises(ConfigurationError, match="invalid configuration: ADM01_INTERNAL_HTTP_BIND_PORT"):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_BIND_PORT": bad,
            },
        )


def test_0_0_0_0_rejected_without_insecure_override() -> None:
    with pytest.raises(
        ConfigurationError,
        match="insecure bind blocked without opt-in: ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES",
    ):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_ENABLE": "1",
                "ADM01_INTERNAL_HTTP_BIND_HOST": "0.0.0.0",
            },
        )


def test_wildcard_ipv6_rejected_without_insecure_and_trust() -> None:
    with pytest.raises(
        ConfigurationError,
        match="insecure bind blocked without opt-in: ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES",
    ):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_ENABLE": "1",
                "ADM01_INTERNAL_HTTP_BIND_HOST": "::",
            },
        )


def test_bracket_wildcard_ipv6_same_rule() -> None:
    with pytest.raises(ConfigurationError) as exc:
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_ENABLE": "1",
                "ADM01_INTERNAL_HTTP_BIND_HOST": "[::]",
            },
        )
    assert "ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES" in str(exc.value)


def test_all_interface_with_insecure_still_needs_trust() -> None:
    with pytest.raises(
        ConfigurationError,
        match="non-loopback internal HTTP bind requires transport trust",
    ):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_ENABLE": "1",
                "ADM01_INTERNAL_HTTP_BIND_HOST": "0.0.0.0",
                "ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES": "1",
            },
        )


def test_0_0_0_0_insecure_plus_trust_reverse_proxy() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "1",
            "ADM01_INTERNAL_HTTP_BIND_HOST": "0.0.0.0",
            "ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES": "1",
            "ADM01_INTERNAL_HTTP_TRUST_REVERSE_PROXY": "1",
        },
    )
    assert cfg.trust_reverse_proxy is True
    assert validate_adm01_internal_http_config(cfg) is cfg


def test_0_0_0_0_insecure_plus_mtls() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "1",
            "ADM01_INTERNAL_HTTP_BIND_HOST": "0.0.0.0",
            "ADM01_INTERNAL_HTTP_BIND_INSECURE_ALL_INTERFACES": "1",
            "ADM01_INTERNAL_HTTP_REQUIRE_MTLS": "true",
        },
    )
    assert cfg.require_mtls is True


def test_non_loopback_rejected_without_trust() -> None:
    with pytest.raises(
        ConfigurationError,
        match="non-loopback internal HTTP bind requires transport trust",
    ):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_ENABLE": "1",
                "ADM01_INTERNAL_HTTP_BIND_HOST": "10.0.0.1",
            },
        )


def test_non_loopback_accepted_with_trust_reverse_proxy() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "1",
            "ADM01_INTERNAL_HTTP_BIND_HOST": "10.0.0.1",
            "ADM01_INTERNAL_HTTP_TRUST_REVERSE_PROXY": "yes",
        },
    )
    assert cfg.trust_reverse_proxy is True
    assert validate_adm01_internal_http_config(cfg) is cfg


def test_non_loopback_accepted_with_require_mtls() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "1",
            "ADM01_INTERNAL_HTTP_BIND_HOST": "10.0.0.1",
            "ADM01_INTERNAL_HTTP_REQUIRE_MTLS": "1",
        },
    )
    assert cfg.require_mtls is True


def test_localhost_is_loopback_no_trust_required() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "1",
            "ADM01_INTERNAL_HTTP_BIND_HOST": "localhost",
        },
    )
    assert cfg.trust_reverse_proxy is False
    assert cfg.require_mtls is False


def test_empty_bind_host_when_enabled_rejected() -> None:
    with pytest.raises(ConfigurationError, match="empty configuration: ADM01_INTERNAL_HTTP_BIND_HOST"):
        load_adm01_internal_http_config_from_env(
            {
                "ADM01_INTERNAL_HTTP_ENABLE": "1",
                "ADM01_INTERNAL_HTTP_BIND_HOST": "",
            },
        )


def test_disabled_does_not_apply_trust_to_non_loopback() -> None:
    cfg = load_adm01_internal_http_config_from_env(
        {
            "ADM01_INTERNAL_HTTP_ENABLE": "0",
            "ADM01_INTERNAL_HTTP_BIND_HOST": "0.0.0.0",
        },
    )
    assert cfg.enabled is False
    assert cfg.bind_host == "0.0.0.0"


def test_invalid_bool_error_no_secret_in_message() -> None:
    e = {"ADM01_INTERNAL_HTTP_ENABLE": _SECRET_LIKE}
    with pytest.raises(ConfigurationError) as exc:
        load_adm01_internal_http_config_from_env(e)
    assert _SECRET_LIKE not in str(exc.value)


def test_direct_validate_both_mtls_and_trust_ok_non_loopback() -> None:
    c = Adm01InternalHttpConfig(
        enabled=True,
        bind_host="192.0.2.1",
        bind_port=18081,
        bind_insecure_all_interfaces=False,
        trust_reverse_proxy=True,
        require_mtls=True,
    )
    assert validate_adm01_internal_http_config(c) is c
