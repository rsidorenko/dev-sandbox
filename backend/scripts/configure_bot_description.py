"""Operator tooling: set/verify the bot's start-page description (setMyDescription).

The description is the text Telegram shows in the chat with the bot when the chat
is empty — i.e. the "welcome card" users see when they open the bot before sending
anything. It is plain text (no HTML/Markdown), 0–512 characters; emoji and newlines
are supported. This text is intentionally versioned here so the card is reproducible
and lives in the repo (it is otherwise invisible — setMyDescription is a runtime API
call, not code in the request path).

Usage:
    python scripts/configure_bot_description.py            # set + verify (loads .env)
    python scripts/configure_bot_description.py --dry-run  # print planned text only
    python scripts/configure_bot_description.py --verify   # only read current value

The bot token is read from BOT_TOKEN (auto-loaded from backend/.env via a safe
parser; or from the environment). It is never printed.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any

import httpx

_ENV_BOT_TOKEN = "BOT_TOKEN"
# Best-effort .env location: this script lives in backend/scripts/, so .env is one
# level up. Loaded with a safe parser (no shell execution) to avoid leaking secrets.
_DOTENV_PATH = Path(__file__).resolve().parent.parent / ".env"

# The welcome card shown on the bot's empty/start chat screen. Plain text only.
WELCOME_DESCRIPTION = (
    "🛡️ Bravada VPN — быстрый и безопасный интернет без границ\n"
    "\n"
    "⚡ Высокая скорость на всех серверах\n"
    "🌍 Нидерланды · Германия · Финляндия · США · Япония\n"
    "🔒 Reality-протокол — не блокируется и не палится\n"
    "📱 Windows · Android · iOS · macOS · TV\n"
    "🎁 3 дня бесплатно новым пользователям\n"
    "\n"
    "Жми «Начать» ↓"
)


def _telegram_post_json(
    *,
    bot_token: str,
    method: str,
    payload: dict[str, Any] | None = None,
    timeout_seconds: float = 15.0,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout_seconds) as client:
        response = client.post(
            f"https://api.telegram.org/bot{bot_token}/{method}",
            json=dict(payload or {}),
        )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict):
        raise RuntimeError("issue_code=telegram_api_invalid_response_shape")
    if not data.get("ok"):
        raise RuntimeError(f"issue_code=telegram_api_error description={data.get('description')}")
    return data


def _load_dotenv() -> None:
    """Populate os.environ from .env using a safe KEY=VALUE parser.

    Never executes file lines (unlike ``source .env``) so secret values with
    spaces/special chars cannot leak as shell errors. Existing env vars win.
    """
    path = _DOTENV_PATH
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = val


def _require_bot_token() -> str:
    _load_dotenv()
    raw = os.environ.get(_ENV_BOT_TOKEN)
    if not raw or not raw.strip():
        raise RuntimeError("issue_code=telegram_bot_token_missing (set BOT_TOKEN in .env or env)")
    return raw.strip()


def _print_planned() -> None:
    print(f"description_length={len(WELCOME_DESCRIPTION)}")
    print("description_planned:")
    print(WELCOME_DESCRIPTION)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Set/verify the bot start-page description.")
    parser.add_argument("--dry-run", action="store_true", help="print planned text, no API call")
    parser.add_argument("--verify", action="store_true", help="only read and print current value")
    parser.add_argument(
        "--language-code",
        default=None,
        help="optional ISO-639-1 language code for a localized description",
    )
    args = parser.parse_args(argv)

    if args.dry_run:
        _print_planned()
        return 0

    bot_token = _require_bot_token()

    if args.verify:
        data = _telegram_post_json(
            bot_token=bot_token,
            method="getMyDescription",
            payload={"language_code": args.language_code} if args.language_code else {},
        )
        print("action=getMyDescription")
        print(f"description_current={data.get('result', {}).get('description', '')!r}")
        return 0

    payload: dict[str, Any] = {"description": WELCOME_DESCRIPTION}
    if args.language_code:
        payload["language_code"] = args.language_code
    _telegram_post_json(bot_token=bot_token, method="setMyDescription", payload=payload)
    print("action=setMyDescription status=ok")
    _print_planned()

    # Verify immediately so the operator sees what Telegram actually stored.
    data = _telegram_post_json(
        bot_token=bot_token,
        method="getMyDescription",
        payload={"language_code": args.language_code} if args.language_code else {},
    )
    print(f"description_verified={data.get('result', {}).get('description', '')!r}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"configure_bot_description: failed ({exc})", file=sys.stderr)
        raise SystemExit(1) from None
