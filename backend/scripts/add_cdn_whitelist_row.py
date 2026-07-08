"""Insert the Yandex-Cloud-CDN whitelist-bypass row (id=34) — the xhttp Happ key.

Architecture (see docs/memory `cdn-whitelist-strimingset`):
  client → Yandex Cloud CDN (cdn.strimingset.ru, cert bravada)
        → Origin 158.160.149.191 (nginx → xray xhttp :8003, inbound tag `from-yandex-cdn`)
        → Frankfurt Exit 77.110.100.210:10444 (xray tcp+tls, freedom)

The Origin **terminates the client vless**, so per-user UUIDs live on the Origin
inbound :8003 — this row is provider_kind='bare_xray' and the bot manages users there
via xray gRPC HandlerService over SSH+grpcurl (XrayApiClient, same path as Cloud.ru
id=26/27). The Origin→Exit leg uses a FIXED uuid (585e5cdc…, baked into the Origin's
outbound config) so the Exit stays static and is NOT touched by the bot.

`link_template` carries the full vless:// with a literal {UUID} placeholder + an `extra`
block (packet-up + xPadding obfuscation + uplinkHTTPMethod=OPTIONS) — the h1cloud-style
CDN link that Happ parses but Karing/mihomo do not. client_surface='happ' serves it only
to Happ imports. ssh_user/ssh_key_path are NULL → the global CLOUDRU_SSH_USER /
CLOUDRU_SSH_KEY env fallback applies (romtan + cloudru_key.pem, whose public half is
authorized on the Yandex host).

Idempotent. Run in the prod container with DATABASE_URL set.
"""

import asyncio
import os
import sys

ROW_ID = 34
ORIGIN_HOST = "158.160.149.191"  # SSH target — CLOUDRU_SSH_KEY (cloudru_key.pem), user romtan
XHTTP_PATH = "/api-test"
INBOUND_TAG = "from-yandex-cdn"  # literal `tag` of the :8003 inbound in Origin config.json

# Full vless:// with {UUID} placeholder (substituted per-user). `extra` is the URL-encoded
# xhttp padding JSON — must match the Origin inbound :8003 xhttpSettings 1:1
# (mode packet-up, xPadding key 'dc', header 'X-Cache', method tokenish, placement
# queryInHeader; uplinkHTTPMethod OPTIONS is client-side). Verified end-to-end.
_LINK_TEMPLATE = (
    "vless://{UUID}@cdn.strimingset.ru:443"
    "?type=xhttp&security=tls&sni=cdn.strimingset.ru&host=cdn.strimingset.ru"
    "&path=%2Fapi-test&fp=firefox&alpn=h2%2Chttp%2F1.1&mode=packet-up"
    "&extra=%7B%22mode%22%3A%22packet-up%22%2C%22scMaxEachPostBytes%22%3A1000000"
    "%2C%22scMinPostsIntervalMs%22%3A30%2C%22scMaxBufferedPosts%22%3A30"
    "%2C%22xPaddingObfsMode%22%3Atrue%2C%22xPaddingKey%22%3A%22dc%22"
    "%2C%22xPaddingHeader%22%3A%22X-Cache%22%2C%22xPaddingMethod%22%3A%22tokenish%22"
    "%2C%22xPaddingPlacement%22%3A%22queryInHeader%22%2C%22uplinkHTTPMethod%22%3A%22OPTIONS%22%7D"
    "#%F0%9F%9B%A1%EF%B8%8F%20%D0%9E%D0%B1%D1%85%D0%BE%D0%B4%20CDN"
)


async def run() -> None:
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL must be set", file=sys.stderr)
        sys.exit(1)

    import asyncpg

    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        # Don't clobber an unrelated row that took id=34.
        existing = await pool.fetchrow("SELECT id, label, server_host FROM vpn_servers WHERE id = $1", ROW_ID)
        if existing and existing["server_host"] != ORIGIN_HOST:
            print(
                f"ERROR: id={ROW_ID} already used by {existing['label']!r} ({existing['server_host']}).",
                file=sys.stderr,
            )
            sys.exit(1)

        await pool.execute("DELETE FROM vpn_servers WHERE id = $1", ROW_ID)
        await pool.execute(
            """
            INSERT INTO vpn_servers (
                id, label, country_code, country_flag, server_host, server_port,
                ws_path, tls_sni, panel_url, panel_username, panel_password,
                encrypted_password, inbound_id, reality_pbk, reality_sid,
                reality_sni, reality_fp, transport_type, api_token, link_template,
                provider_kind, xray_inbound_tag, client_surface, panel_flavor,
                ssh_user, ssh_key_path, is_active
            ) VALUES (
                $1, $2, '', '\U0001f310', $3, 443,
                $4, NULL, '', '', '',
                '', 0, '', '',
                '', 'firefox', 'xhttp', '', $5,
                'bare_xray', $6, 'happ', 'standard',
                NULL, NULL, TRUE
            )
            """,
            ROW_ID,
            "Обход CDN",  # label
            ORIGIN_HOST,
            XHTTP_PATH.strip("/"),  # ws_path = "api-test"
            _LINK_TEMPLATE,
            INBOUND_TAG,
        )
        print(f"Inserted vpn_servers row id={ROW_ID} (CDN whitelist bypass, client_surface=happ)")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, transport_type, provider_kind, "
            "       client_surface, xray_inbound_tag, is_active "
            "FROM vpn_servers WHERE id = $1",
            ROW_ID,
        )
        print(f"Verified: {dict(row)}")
        print(
            "\nBot manages per-user clients on Origin :8003 via gRPC (cloudru_key.pem/romtan). "
            "Run reconcile_all_users (or reprovision) to seed all active users."
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
