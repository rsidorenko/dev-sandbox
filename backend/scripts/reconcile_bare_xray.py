#!/usr/bin/env python3
"""Operator: desired-state reconcile for bare_xray inbounds (live xray gRPC).

Ensures every active + expired-in-grace user is present on every bare_xray inbound
(raw xray boxes: Yandex / cloud.ru / Helsinki) and removes orphans — via xray's gRPC
``HandlerService/AlterInbound``. This is **non-disruptive**: it never restarts xray
and never drops existing connections, so it is safe to run on a fast timer (~3 min,
``reconcile-bare-xray.timer``) and on demand.

It backfills users never added by the best-effort live path (e.g. a host where
``grpcurl`` was missing) and self-heals drift (a wiped inbound such as Helsinki
``clr-tcp``, or clients lost to an xray restart). Hosts come from ``vpn_servers``
(``provider_kind='bare_xray'``), never hardcoded.

Runs in the production container with ``DATABASE_URL`` + ``CLOUDRU_SSH_*`` + the
mounted key (``/opt/bravada/cloudru_key.pem``). It is shipped to the container at run
time via ``docker compose cp`` (scripts/ is not baked into the image), exactly like
``reprovision_active.py``.
"""
from __future__ import annotations

import asyncio
import os
import sys

import asyncpg

# In the container PYTHONPATH=/app/src is already set; be defensive for local runs.
_SRC = "/app/src"
if os.path.isdir(_SRC):
    sys.path.insert(0, _SRC)

from app.issuance.xui_vless_provider import XuiVlessProvider  # noqa: E402


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        provider = XuiVlessProvider(pool)
        result = await provider.reconcile_bare_xray()
        print("=== reconcile_bare_xray ===")
        print(
            f"users={result['users']}  added={result['added']}  removed={result['removed']}  "
            f"failed_inbounds={result['failed_inbounds']}"
        )
        for key, info in result["inbounds"].items():
            present = info.get("present")
            print(
                f"  {key}: present={present} desired={info['desired']} "
                f"+{info['added']} -{info['removed']} [{info.get('status', '?')}]"
            )
        if result["failed_inbounds"]:
            print(f"\n!! {result['failed_inbounds']} inbound(s) failed to list — see app logs")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
