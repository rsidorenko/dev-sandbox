"""Operator tooling for the CryptoBot webhook — verify the API token and manage the webhook.

Unlike the Telegram Bot API, Crypto Pay does NOT expose a ``createWebhook`` endpoint:
the webhook URL is configured in the @CryptoBot app settings UI (Crypto Pay → your app →
App Info → Webhook). This script therefore:

  * ``--dry-run``  — validate that ``CRYPTOBOT_API_TOKEN`` is set and print the exact
                      webhook URL to paste into the @CryptoBot UI.
  * ``--verify``   — call ``getMe`` to confirm the token works and print the app id/name.
  * ``--delete``   — call ``deleteWebhook`` (switches the app to long-polling mode).

Run from the ``backend/`` directory. The token may also be supplied via ``--token``.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import httpx

_ENV_API_TOKEN = "CRYPTOBOT_API_TOKEN"
_ENV_TESTNET = "CRYPTOBOT_TESTNET"
_ENV_SITE_URL = "NEXT_PUBLIC_SITE_URL"
_DEFAULT_SITE_URL = "https://bravada-connect.ru"
_WEBHOOK_PATH = "/api/v1/payment/cryptobot/webhook"

_PROD_API_BASE = "https://pay.crypt.bot/api"
_TESTNET_API_BASE = "https://testnet-pay.crypt.bot/api"


def _api_base(env: Mapping[str, str]) -> str:
    testnet = env.get(_ENV_TESTNET, "").strip().lower() in ("1", "true", "yes")
    return _TESTNET_API_BASE if testnet else _PROD_API_BASE


def _cryptobot_post_json(
    *,
    api_base: str,
    api_token: str,
    method: str,
    payload: Mapping[str, object] | None = None,
    timeout_seconds: float = 15.0,
) -> dict:
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"{api_base}/{method}",
            json=dict(payload or {}),
            headers={"Crypto-Pay-API-Token": api_token, "Content-Type": "application/json"},
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("issue_code=cryptobot_api_invalid_response_shape")
    return data


def _webhook_url(env: Mapping[str, str]) -> str:
    base = (env.get(_ENV_SITE_URL) or _DEFAULT_SITE_URL).strip().rstrip("/")
    return f"{base}{_WEBHOOK_PATH}"


def run_configure_cryptobot_webhook(
    *,
    action: str,
    env: Mapping[str, str],
    api_token_override: str | None,
    api_post=_cryptobot_post_json,
) -> int:
    api_token = (api_token_override or env.get(_ENV_API_TOKEN, "")).strip()
    if not api_token:
        print("cryptobot_webhook_configure: failed", file=sys.stderr)
        print("issue_code=cryptobot_api_token_missing", file=sys.stderr)
        return 1

    api_base = _api_base(env)
    webhook_url = _webhook_url(env)
    print(f"action={action}")
    print(f"api_base={api_base}")
    print(f"webhook_url_to_set_in_cryptobot_ui={webhook_url}")

    try:
        if action == "dry_run":
            print("cryptobot_webhook_configure: dry_run")
            return 0

        if action == "verify":
            data = api_post(api_base=api_base, api_token=api_token, method="getMe")
            if data.get("ok") is not True:
                raise RuntimeError("reason=cryptobot_getme_not_ok")
            result = data.get("result") or {}
            print(f"app_id={result.get('app_id')}")
            print(f"app_name={result.get('name')}")
            print("cryptobot_webhook_configure: ok")
            return 0

        if action == "delete":
            data = api_post(api_base=api_base, api_token=api_token, method="deleteWebhook")
            if data.get("ok") is not True:
                raise RuntimeError("issue_code=cryptobot_delete_webhook_failed")
            print("cryptobot_webhook_configure: ok")
            return 0

        raise RuntimeError("issue_code=unsupported_action")
    except RuntimeError as exc:
        print("cryptobot_webhook_configure: failed", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 1
    except Exception:
        print("cryptobot_webhook_configure: failed", file=sys.stderr)
        print("reason=cryptobot_runtime_failure", file=sys.stderr)
        return 1


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--verify", action="store_true", help="Call getMe to confirm the token works.")
    mode.add_argument("--delete", action="store_true", help="Call deleteWebhook (switch to polling).")
    mode.add_argument("--dry-run", action="store_true", help="Validate config only (default).")
    parser.add_argument("--token", default=None, help="Crypto Pay API token (overrides CRYPTOBOT_API_TOKEN).")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None, *, env: Mapping[str, str] | None = None) -> int:
    args = _parse_args(argv)
    effective_env: Mapping[str, str] = os.environ if env is None else env
    action = "delete" if args.delete else ("verify" if args.verify else "dry_run")
    return run_configure_cryptobot_webhook(action=action, env=effective_env, api_token_override=args.token)


if __name__ == "__main__":
    raise SystemExit(main())
