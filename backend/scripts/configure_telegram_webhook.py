"""Operator tooling for Telegram webhook configure/verify actions."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from app.security.public_url_policy import (
    classify_public_https_url_host,
    validate_public_https_operator_url,
)
from app.security.safe_diagnostics import redact_url_for_diagnostics
from app.security.telegram_webhook_policy import (
    normalize_webhook_url_for_compare,
    parse_webhook_allowed_updates,
    validate_allowed_updates_for_command_bot,
)

_ENV_BOT_TOKEN = "BOT_TOKEN"
_ENV_WEBHOOK_PUBLIC_URL = "TELEGRAM_WEBHOOK_PUBLIC_URL"
_ENV_WEBHOOK_SECRET_TOKEN = "TELEGRAM_WEBHOOK_SECRET_TOKEN"
_ENV_WEBHOOK_ALLOWED_UPDATES = "TELEGRAM_WEBHOOK_ALLOWED_UPDATES"


def _safe_get(env: Mapping[str, str], key: str) -> str | None:
    raw = env.get(key)
    if raw is None:
        return None
    value = raw.strip()
    return value or None


def _telegram_api_base_url(bot_token: str) -> str:
    return f"https://api.telegram.org/bot{bot_token}"


def _telegram_post_json(
    *,
    bot_token: str,
    method: str,
    payload: Mapping[str, Any],
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(f"{_telegram_api_base_url(bot_token)}/{method}", json=dict(payload))
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("issue_code=telegram_api_invalid_response_shape")
    return data


def _print_common_markers(*, action: str, public_url: str | None, secret_token: str | None) -> None:
    print(f"action={action}")
    print(f"secret_token_configured={'yes' if bool(secret_token) else 'no'}")
    print(f"webhook_public_url_host_class={classify_public_https_url_host(public_url)}")
    print(f"webhook_public_url={redact_url_for_diagnostics(public_url)}")


def _ensure_set_requirements(
    *,
    env: Mapping[str, str],
    allow_test_host: bool,
) -> tuple[str, str, str, tuple[str, ...]]:
    bot_token = _safe_get(env, _ENV_BOT_TOKEN)
    if bot_token is None:
        raise RuntimeError("issue_code=telegram_bot_token_missing")
    secret_token = _safe_get(env, _ENV_WEBHOOK_SECRET_TOKEN)
    if secret_token is None:
        raise RuntimeError("issue_code=telegram_webhook_secret_missing")
    if len(secret_token) < 24:
        raise RuntimeError("issue_code=telegram_webhook_secret_too_weak")
    public_url_raw = _safe_get(env, _ENV_WEBHOOK_PUBLIC_URL)
    if public_url_raw is None:
        raise RuntimeError("issue_code=telegram_webhook_public_url_missing")
    public_url = validate_public_https_operator_url(
        raw_url=public_url_raw,
        allow_test_host=allow_test_host,
    )
    if public_url is None:
        raise RuntimeError("issue_code=telegram_webhook_public_url_invalid")
    try:
        allowed_updates = parse_webhook_allowed_updates(_safe_get(env, _ENV_WEBHOOK_ALLOWED_UPDATES))
    except ValueError:
        raise RuntimeError("issue_code=telegram_webhook_allowed_updates_invalid") from None
    au_issue = validate_allowed_updates_for_command_bot(allowed_updates)
    if au_issue:
        raise RuntimeError(f"issue_code={au_issue}")
    return bot_token, public_url, secret_token, allowed_updates


def _summarize_last_error_fields(result: dict[str, Any]) -> tuple[str, str]:
    """Return safe markers only (no raw Telegram error text)."""
    has_date = result.get("last_error_date") is not None
    msg = result.get("last_error_message")
    has_msg = isinstance(msg, str) and bool(msg.strip())
    if has_msg or has_date:
        return "yes", "yes"
    return "no", "no"


def _verify_webhook_semantics(
    *,
    result: dict[str, Any],
    expected_public_url: str,
    expected_allowed_updates: tuple[str, ...],
) -> None:
    configured_url = result.get("url")
    if not isinstance(configured_url, str) or not configured_url.strip():
        raise RuntimeError("reason=telegram_webhook_verify_url_missing")
    expected_norm = normalize_webhook_url_for_compare(expected_public_url)
    actual_norm = normalize_webhook_url_for_compare(configured_url)
    if actual_norm != expected_norm:
        raise RuntimeError("reason=telegram_webhook_verify_url_mismatch")
    print("url_match=yes")

    raw_updates = result.get("allowed_updates")
    if raw_updates is None:
        print("allowed_updates_match=unknown")
    elif isinstance(raw_updates, list) and all(isinstance(x, str) for x in raw_updates):
        actual_tuple = tuple(sorted({x.strip() for x in raw_updates if isinstance(x, str) and x.strip()}))
        expected_tuple = tuple(sorted(expected_allowed_updates))
        if actual_tuple != expected_tuple:
            raise RuntimeError("reason=telegram_webhook_verify_allowed_updates_mismatch")
        print("allowed_updates_match=yes")
    else:
        print("allowed_updates_match=unknown")

    pending = result.get("pending_update_count", 0)
    pending_n = int(pending) if isinstance(pending, int) else 0
    print(f"pending_update_count={pending_n}")

    last_active, _ = _summarize_last_error_fields(result)
    if last_active == "yes":
        raise RuntimeError("reason=telegram_webhook_verify_last_error_present")

    print("secret_token_status_match=unknown")
    print("secret_token_api_visibility=not_echoed_by_telegram")
    print("last_error_active=no")


def run_configure_telegram_webhook(
    *,
    action: str,
    env: Mapping[str, str],
    api_post=_telegram_post_json,
) -> int:
    allow_test_host = action in {"dry_run", "verify"}
    try:
        if action == "delete":
            bot_token = _safe_get(env, _ENV_BOT_TOKEN)
            if bot_token is None:
                raise RuntimeError("issue_code=telegram_bot_token_missing")
            _print_common_markers(
                action=action,
                public_url=_safe_get(env, _ENV_WEBHOOK_PUBLIC_URL),
                secret_token=_safe_get(env, _ENV_WEBHOOK_SECRET_TOKEN),
            )
            data = api_post(bot_token=bot_token, method="deleteWebhook", payload={"drop_pending_updates": False})
            if data.get("ok") is not True:
                raise RuntimeError("issue_code=telegram_webhook_delete_failed")
            print("telegram_webhook_configure: ok")
            return 0

        bot_token, public_url, secret_token, allowed_updates = _ensure_set_requirements(
            env=env,
            allow_test_host=allow_test_host,
        )
        _print_common_markers(
            action=("set" if action in {"dry_run", "apply"} else action),
            public_url=public_url,
            secret_token=secret_token,
        )
        print(f"allowed_updates_count={len(allowed_updates)}")
        print(f"expected_allowed_updates_items={','.join(allowed_updates)}")

        if action == "dry_run":
            print("telegram_webhook_configure: dry_run")
            return 0

        if action == "verify":
            data = api_post(bot_token=bot_token, method="getWebhookInfo", payload={})
            if data.get("ok") is not True:
                raise RuntimeError("reason=telegram_webhook_verify_api_not_ok")
            result = data.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("reason=telegram_webhook_verify_invalid_result")
            configured_url = result.get("url")
            has_url = bool(str(configured_url).strip()) if isinstance(configured_url, str) else False
            print(f"configured_webhook_present={'yes' if has_url else 'no'}")
            print(
                f"configured_webhook_url={redact_url_for_diagnostics(configured_url if isinstance(configured_url, str) else None)}"
            )
            print(
                f"configured_webhook_host_class={classify_public_https_url_host(configured_url if isinstance(configured_url, str) else None)}"
            )
            _verify_webhook_semantics(
                result=result,
                expected_public_url=public_url,
                expected_allowed_updates=allowed_updates,
            )
            print("telegram_webhook_configure: ok")
            return 0

        if action == "apply":
            data = api_post(
                bot_token=bot_token,
                method="setWebhook",
                payload={
                    "url": public_url,
                    "secret_token": secret_token,
                    "allowed_updates": list(allowed_updates),
                },
            )
            if data.get("ok") is not True:
                raise RuntimeError("issue_code=telegram_webhook_set_failed")
            print("telegram_webhook_configure: ok")
            return 0

        raise RuntimeError("issue_code=unsupported_action")
    except RuntimeError as exc:
        print("telegram_webhook_configure: failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("telegram_webhook_configure: failed", file=sys.stderr)
        print("reason=telegram_webhook_runtime_failure", file=sys.stderr)
        return 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--apply", action="store_true", help="Apply setWebhook via Telegram Bot API.")
    mode.add_argument("--verify", action="store_true", help="Read-only getWebhookInfo verification.")
    mode.add_argument("--delete", action="store_true", help="Explicitly delete webhook via Telegram Bot API.")
    mode.add_argument("--dry-run", action="store_true", help="Validate config only (default).")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    args = _parse_args(argv)
    effective_env: Mapping[str, str] = os.environ if env is None else env
    action = "dry_run"
    if args.apply:
        action = "apply"
    elif args.verify:
        action = "verify"
    elif args.delete:
        action = "delete"
    return run_configure_telegram_webhook(action=action, env=effective_env)


if __name__ == "__main__":
    raise SystemExit(main())
