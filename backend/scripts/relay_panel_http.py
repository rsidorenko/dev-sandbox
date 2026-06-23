"""Switch the RU relay panel_url scheme to HTTP (or back to HTTPS via env).

Why HTTP: prod runs XUI_VERIFY_SSL=1 (the bot verifies panel TLS). The other
panels have CA-trusted certs; the freshly-installed relay panel is HTTP. A
self-signed cert won't pass verify, and a CA cert for the relay IP is a separate
effort. HTTP has no TLS verification, so it works immediately. The panel sits
behind ufw + the Yandex security group (limited source) + a random webBasePath +
a strong password.

When a trusted cert is provisioned for the relay, set RELAY_PANEL_SCHEME=https
and re-run to switch back.

Runs in the production container with DATABASE_URL.
"""

import asyncio
import os

RELAY_ID = 11
SCHEME = (os.environ.get("RELAY_PANEL_SCHEME") or "http").strip().lower()


async def run():
    if SCHEME not in ("http", "https"):
        raise SystemExit(f"bad RELAY_PANEL_SCHEME={SCHEME!r}")
    import asyncpg
    conn = await asyncpg.connect(os.environ["DATABASE_URL"])
    try:
        row = await conn.fetchrow("SELECT panel_url FROM vpn_servers WHERE id=$1", RELAY_ID)
        if not row:
            raise SystemExit(f"no vpn_servers row id={RELAY_ID}")
        url = row["panel_url"]
        new = url
        if SCHEME == "http" and url.startswith("https://"):
            new = "http://" + url[len("https://"):]
        elif SCHEME == "https" and url.startswith("http://"):
            new = "https://" + url[len("http://"):]
        if new != url:
            await conn.execute("UPDATE vpn_servers SET panel_url=$1 WHERE id=$2", new, RELAY_ID)
            print(f"panel_url id={RELAY_ID}: {url} -> {new}")
        else:
            print(f"panel_url id={RELAY_ID} already {SCHEME}: {url}")
    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(run())
