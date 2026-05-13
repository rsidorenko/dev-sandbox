"""Customer-facing launch readiness preflight (safe diagnostics, no side effects)."""

from __future__ import annotations

import argparse
import os
from collections.abc import Mapping

from app.application.telegram_access_resend import TELEGRAM_ACCESS_RESEND_ENABLE
from app.bot_transport.storefront_config import (
    load_storefront_public_config,
)
from app.runtime.payment_fulfillment_ingress import (
    _STRICT_CHECKOUT_REFERENCE_MAX_AGE_MAX_SECONDS,
    _STRICT_CHECKOUT_REFERENCE_MAX_AGE_MIN_SECONDS,
    ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE,
    ENV_PAYMENT_FULFILLMENT_SECRET,
    ENV_SUBSCRIPTION_DEFAULT_PERIOD_DAYS,
    ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
    ENV_TELEGRAM_CHECKOUT_REFERENCE_SECRET,
)
from app.runtime.telegram_webhook_ingress import (
    ENV_TELEGRAM_WEBHOOK_HTTP_ENABLE,
    ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN,
)
from app.security.public_url_policy import validate_public_https_operator_url
from app.security.safe_diagnostics import (
    has_suspicious_query_pattern,
    redact_dsn_for_diagnostics,
    redact_url_for_diagnostics,
)
from app.security.telegram_webhook_policy import (
    parse_webhook_allowed_updates,
    validate_allowed_updates_for_command_bot,
)

_ENV_DATABASE_URL = "DATABASE_URL"
_ENV_TELEGRAM_WEBHOOK_ALLOWED_UPDATES = "TELEGRAM_WEBHOOK_ALLOWED_UPDATES"
_ENV_BOT_TOKEN = "BOT_TOKEN"
_ENV_STRICT = "LAUNCH_PREFLIGHT_STRICT"
_ENV_PLAN_FALLBACK_ACK = "TELEGRAM_STOREFRONT_ALLOW_PLAN_FALLBACK"
_ENV_SUPPORT_FALLBACK_ACK = "TELEGRAM_STOREFRONT_ALLOW_SUPPORT_FALLBACK"
_ENV_CHECKOUT_REFERENCE_DEFAULT_TTL_ACCEPTED = "TELEGRAM_CHECKOUT_REFERENCE_DEFAULT_TTL_ACCEPTED"
_ENV_ACCESS_RECONCILE_SCHEDULE_ACK = "ACCESS_RECONCILE_SCHEDULE_ACK"
_ENV_ACCESS_RECONCILE_MAX_INTERVAL_SECONDS = "ACCESS_RECONCILE_MAX_INTERVAL_SECONDS"
_ENV_TELEGRAM_WEBHOOK_PUBLIC_URL = "TELEGRAM_WEBHOOK_PUBLIC_URL"
_TRUTHY = {"1", "true", "yes"}
_DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_SUBSCRIPTION_PERIOD_MIN_DAYS = 1
_SUBSCRIPTION_PERIOD_MAX_DAYS = 3660
_ACCESS_RECONCILE_INTERVAL_MIN_SECONDS = 300
_ACCESS_RECONCILE_INTERVAL_MAX_SECONDS = 86400


def _truthy(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY


def _min_secret_strength_ok(value: str) -> bool:
    # Lightweight entropy-ish gate: enough length + character diversity.
    if len(value) < 24:
        return False
    classes = 0
    if any(c.islower() for c in value):
        classes += 1
    if any(c.isupper() for c in value):
        classes += 1
    if any(c.isdigit() for c in value):
        classes += 1
    if any(not c.isalnum() for c in value):
        classes += 1
    return classes >= 2 and len(set(value)) >= 8


def _safe_get(env: Mapping[str, str], name: str) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _check_storefront(*, env: Mapping[str, str], strict: bool, errors: list[str], warnings: list[str]) -> None:
    config = load_storefront_public_config(getenv=env.get)
    raw_checkout = _safe_get(env, "TELEGRAM_STOREFRONT_CHECKOUT_URL")
    raw_renewal = _safe_get(env, "TELEGRAM_STOREFRONT_RENEWAL_URL")
    raw_support = _safe_get(env, "TELEGRAM_STOREFRONT_SUPPORT_URL")

    if strict and not config.plan_name:
        if not _truthy(_safe_get(env, _ENV_PLAN_FALLBACK_ACK)):
            errors.append("storefront_plan_name_missing_without_fallback_ack")
        else:
            warnings.append("storefront_plan_name_missing_fallback_acknowledged")
    elif not config.plan_name:
        warnings.append("storefront_plan_name_missing")

    if strict and not config.plan_price:
        if not _truthy(_safe_get(env, _ENV_PLAN_FALLBACK_ACK)):
            errors.append("storefront_plan_price_missing_without_fallback_ack")
        else:
            warnings.append("storefront_plan_price_missing_fallback_acknowledged")
    elif not config.plan_price:
        warnings.append("storefront_plan_price_missing")

    if not raw_checkout:
        errors.append("storefront_checkout_url_missing")
    elif config.checkout_url is None:
        errors.append("storefront_checkout_url_invalid")

    if raw_renewal and config.renewal_url is None:
        errors.append("storefront_renewal_url_invalid")
    elif not raw_renewal:
        if config.checkout_url is not None:
            warnings.append("storefront_renewal_url_missing_using_checkout_fallback")
        else:
            errors.append("storefront_renewal_url_fallback_unavailable")

    support_ready = config.support_url is not None or config.support_handle is not None
    if raw_support and config.support_url is None:
        errors.append("storefront_support_url_invalid")
    if strict and not support_ready:
        if not _truthy(_safe_get(env, _ENV_SUPPORT_FALLBACK_ACK)):
            errors.append("storefront_support_contact_missing_without_fallback_ack")
        else:
            warnings.append("storefront_support_contact_missing_fallback_acknowledged")
    elif not support_ready:
        warnings.append("storefront_support_contact_missing")

    if has_suspicious_query_pattern(raw_checkout) or has_suspicious_query_pattern(raw_support):
        errors.append("storefront_url_contains_suspicious_query_pattern")


def _check_fulfillment(*, env: Mapping[str, str], strict: bool, errors: list[str], warnings: list[str]) -> None:
    http_enabled = _truthy(_safe_get(env, ENV_PAYMENT_FULFILLMENT_HTTP_ENABLE))
    secret = _safe_get(env, ENV_PAYMENT_FULFILLMENT_SECRET)
    checkout_reference_secret = _safe_get(env, ENV_TELEGRAM_CHECKOUT_REFERENCE_SECRET)
    if strict and not http_enabled:
        errors.append("payment_fulfillment_http_not_enabled")
    elif not http_enabled:
        warnings.append("payment_fulfillment_http_not_enabled")
        return
    if not secret:
        errors.append("payment_fulfillment_secret_missing")
        return
    if not _min_secret_strength_ok(secret):
        errors.append("payment_fulfillment_secret_too_weak")
    if strict and not checkout_reference_secret:
        errors.append("checkout_reference_secret_missing")
        return
    if checkout_reference_secret and not _min_secret_strength_ok(checkout_reference_secret):
        errors.append("checkout_reference_secret_too_weak")
    default_period_raw = _safe_get(env, ENV_SUBSCRIPTION_DEFAULT_PERIOD_DAYS)
    if default_period_raw is None:
        if strict:
            warnings.append("subscription_default_period_days_not_set")
        return
    try:
        default_period_days = int(default_period_raw)
    except ValueError:
        errors.append("subscription_default_period_days_invalid")
        return
    if default_period_days < _SUBSCRIPTION_PERIOD_MIN_DAYS:
        errors.append("subscription_default_period_days_too_small")
    if default_period_days > _SUBSCRIPTION_PERIOD_MAX_DAYS:
        errors.append("subscription_default_period_days_too_large")


def _classify_subscription_period(days: int | None) -> str:
    if days is None:
        return "not_set"
    if days < _SUBSCRIPTION_PERIOD_MIN_DAYS:
        return "too_small"
    if days > _SUBSCRIPTION_PERIOD_MAX_DAYS:
        return "too_large"
    return "recommended"


def _classify_checkout_reference_ttl(seconds: int) -> str:
    if seconds < _STRICT_CHECKOUT_REFERENCE_MAX_AGE_MIN_SECONDS:
        return "too_small"
    if seconds > _STRICT_CHECKOUT_REFERENCE_MAX_AGE_MAX_SECONDS:
        return "too_large"
    return "recommended"


def _check_checkout_reference_ttl(
    *,
    env: Mapping[str, str],
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> tuple[int, str]:
    ttl_raw = _safe_get(env, ENV_TELEGRAM_CHECKOUT_REFERENCE_MAX_AGE_SECONDS)
    using_default = ttl_raw is None
    ttl_value = _DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS if using_default else -1
    if not using_default:
        try:
            ttl_value = int(ttl_raw)
        except (TypeError, ValueError):
            errors.append("checkout_reference_ttl_invalid")
            return _DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS, "invalid"
    if ttl_value <= 0:
        errors.append("checkout_reference_ttl_invalid")
        return _DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS, "invalid"

    classification = _classify_checkout_reference_ttl(ttl_value)
    if strict and using_default and not _truthy(_safe_get(env, _ENV_CHECKOUT_REFERENCE_DEFAULT_TTL_ACCEPTED)):
        errors.append("checkout_reference_ttl_default_not_explicitly_accepted")
    elif using_default and not strict:
        warnings.append("checkout_reference_ttl_default_in_use")

    if strict and classification == "too_small":
        errors.append("checkout_reference_ttl_too_small_for_checkout_flow")
    elif strict and classification == "too_large":
        errors.append("checkout_reference_ttl_too_large_for_replay_safety")
    return ttl_value, classification


def _check_telegram_runtime(
    *,
    env: Mapping[str, str],
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> None:
    bot_token = _safe_get(env, _ENV_BOT_TOKEN)
    if not bot_token:
        errors.append("telegram_bot_token_missing")
    elif len(bot_token) < 10:
        errors.append("telegram_bot_token_invalid_shape")

    webhook_enabled = _truthy(_safe_get(env, ENV_TELEGRAM_WEBHOOK_HTTP_ENABLE))
    webhook_secret = _safe_get(env, ENV_TELEGRAM_WEBHOOK_SECRET_TOKEN)
    webhook_public_url_raw = _safe_get(env, _ENV_TELEGRAM_WEBHOOK_PUBLIC_URL)
    if webhook_enabled:
        if not webhook_secret:
            errors.append("telegram_webhook_secret_missing")
        elif not _min_secret_strength_ok(webhook_secret):
            errors.append("telegram_webhook_secret_too_weak")
        if not webhook_public_url_raw:
            errors.append("telegram_webhook_public_url_missing")
        elif (
            validate_public_https_operator_url(
                raw_url=webhook_public_url_raw,
                allow_test_host=False,
            )
            is None
        ):
            errors.append("telegram_webhook_public_url_invalid")
        raw_au = _safe_get(env, _ENV_TELEGRAM_WEBHOOK_ALLOWED_UPDATES)
        try:
            au_items = parse_webhook_allowed_updates(raw_au)
        except ValueError:
            errors.append("telegram_webhook_allowed_updates_invalid")
        else:
            au_issue = validate_allowed_updates_for_command_bot(au_items)
            if au_issue:
                errors.append(au_issue)
    elif strict:
        warnings.append("telegram_webhook_http_disabled_using_polling_path")


def _check_access_delivery(*, env: Mapping[str, str], strict: bool, errors: list[str], warnings: list[str]) -> None:
    resend_enabled = _truthy(_safe_get(env, TELEGRAM_ACCESS_RESEND_ENABLE))
    if strict and not resend_enabled:
        errors.append("telegram_access_resend_not_enabled")
        return
    if not resend_enabled:
        warnings.append("telegram_access_resend_not_enabled")


def _classify_access_reconcile_interval(seconds: int | None) -> str:
    if seconds is None:
        return "not_set"
    if seconds < _ACCESS_RECONCILE_INTERVAL_MIN_SECONDS:
        return "too_small"
    if seconds > _ACCESS_RECONCILE_INTERVAL_MAX_SECONDS:
        return "too_large"
    return "recommended"


def _check_access_reconcile_schedule(
    *,
    env: Mapping[str, str],
    strict: bool,
    errors: list[str],
    warnings: list[str],
) -> tuple[str, int | None, str]:
    schedule_ack = _safe_get(env, _ENV_ACCESS_RECONCILE_SCHEDULE_ACK)
    interval_raw = _safe_get(env, _ENV_ACCESS_RECONCILE_MAX_INTERVAL_SECONDS)
    schedule_ack_ok = _truthy(schedule_ack)
    interval_seconds: int | None = None
    interval_classification = "not_set"

    if strict and not schedule_ack_ok:
        errors.append("access_reconcile_schedule_ack_missing")
    elif not schedule_ack_ok:
        warnings.append("access_reconcile_schedule_ack_missing")

    if interval_raw is None:
        if strict:
            errors.append("access_reconcile_max_interval_seconds_missing")
        else:
            warnings.append("access_reconcile_max_interval_seconds_missing")
        return ("acknowledged" if schedule_ack_ok else "missing", None, "not_set")

    try:
        interval_seconds = int(interval_raw)
    except ValueError:
        errors.append("access_reconcile_max_interval_seconds_invalid")
        return ("acknowledged" if schedule_ack_ok else "missing", None, "invalid")

    interval_classification = _classify_access_reconcile_interval(interval_seconds)
    if interval_classification == "too_small":
        errors.append("access_reconcile_max_interval_seconds_too_small")
    elif interval_classification == "too_large":
        errors.append("access_reconcile_max_interval_seconds_too_large")
    return (
        "acknowledged" if schedule_ack_ok else "missing",
        interval_seconds,
        interval_classification,
    )


def _check_database(*, env: Mapping[str, str], strict: bool, errors: list[str], warnings: list[str]) -> str:
    dsn = _safe_get(env, _ENV_DATABASE_URL)
    if not dsn:
        if strict:
            errors.append("database_url_missing")
        else:
            warnings.append("database_url_missing")
        return "<missing>"
    if has_suspicious_query_pattern(dsn):
        errors.append("database_url_contains_suspicious_query_pattern")
    if not dsn.startswith(("postgresql://", "postgres://")):
        errors.append("database_url_invalid_scheme")
    return redact_dsn_for_diagnostics(dsn)


def run_launch_preflight(
    *,
    strict: bool,
    env: Mapping[str, str] | None = None,
) -> tuple[int, tuple[str, ...]]:
    effective_env: Mapping[str, str] = os.environ if env is None else env
    errors: list[str] = []
    warnings: list[str] = []

    _check_storefront(env=effective_env, strict=strict, errors=errors, warnings=warnings)
    _check_fulfillment(env=effective_env, strict=strict, errors=errors, warnings=warnings)
    checkout_ttl_value, checkout_ttl_classification = _check_checkout_reference_ttl(
        env=effective_env,
        strict=strict,
        errors=errors,
        warnings=warnings,
    )
    _check_telegram_runtime(env=effective_env, strict=strict, errors=errors, warnings=warnings)
    _check_access_delivery(env=effective_env, strict=strict, errors=errors, warnings=warnings)
    reconcile_ack_marker, reconcile_interval_seconds, reconcile_interval_classification = (
        _check_access_reconcile_schedule(
            env=effective_env,
            strict=strict,
            errors=errors,
            warnings=warnings,
        )
    )
    database_marker = _check_database(env=effective_env, strict=strict, errors=errors, warnings=warnings)
    subscription_period_days_raw = _safe_get(effective_env, ENV_SUBSCRIPTION_DEFAULT_PERIOD_DAYS)
    subscription_period_days: int | None = None
    if subscription_period_days_raw is not None:
        try:
            subscription_period_days = int(subscription_period_days_raw)
        except ValueError:
            subscription_period_days = None

    mode_marker = "strict" if strict else "default"
    status = "ok" if not errors else "fail"
    print(f"launch_readiness_preflight: {status}")
    print(f"mode={mode_marker}")
    print(f"database={database_marker}")
    checkout_marker = redact_url_for_diagnostics(_safe_get(effective_env, "TELEGRAM_STOREFRONT_CHECKOUT_URL"))
    support_marker = redact_url_for_diagnostics(_safe_get(effective_env, "TELEGRAM_STOREFRONT_SUPPORT_URL"))
    print(f"checkout={checkout_marker}")
    print(f"support={support_marker}")
    webhook_public_marker = redact_url_for_diagnostics(_safe_get(effective_env, _ENV_TELEGRAM_WEBHOOK_PUBLIC_URL))
    print(f"webhook_public_url={webhook_public_marker}")
    if _truthy(_safe_get(effective_env, ENV_TELEGRAM_WEBHOOK_HTTP_ENABLE)):
        try:
            au_line = parse_webhook_allowed_updates(_safe_get(effective_env, _ENV_TELEGRAM_WEBHOOK_ALLOWED_UPDATES))
            print(f"telegram_webhook_allowed_updates_items={','.join(au_line)}")
        except ValueError:
            print("telegram_webhook_allowed_updates_items=<invalid>")
    print(f"checkout_reference_ttl_seconds={checkout_ttl_value}")
    print(f"checkout_reference_ttl_classification={checkout_ttl_classification}")
    print(f"access_reconcile_schedule_ack={reconcile_ack_marker}")
    print(
        "access_reconcile_max_interval_seconds="
        f"{reconcile_interval_seconds if reconcile_interval_seconds is not None else '<missing>'}"
    )
    print(f"access_reconcile_interval_classification={reconcile_interval_classification}")
    print("access_reconcile_operator_command=python scripts/reconcile_expired_access.py")
    print(
        f"subscription_default_period_days={subscription_period_days if subscription_period_days is not None else '<missing>'}"
    )
    print(f"subscription_default_period_classification={_classify_subscription_period(subscription_period_days)}")

    for code in warnings:
        print(f"warn_code={code}")
    for code in errors:
        print(f"issue_code={code}")
    return (0 if not errors else 1), tuple(warnings + errors)


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict", action="store_true", help="Enable launch-required strict checks.")
    args = parser.parse_args(argv)
    strict = bool(args.strict)
    if not strict:
        strict = _truthy(_safe_get(os.environ if env is None else env, _ENV_STRICT))
    exit_code, _ = run_launch_preflight(strict=strict, env=env)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
