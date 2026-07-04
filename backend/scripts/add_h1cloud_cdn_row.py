"""Insert the h1cloud «Обход белых списков» CDN-bypass row (id=22) — the 3rd Happ key.

h1cloud (193.233.126.96, NL, Pterodactyl 3x-ui) is the whitelist-bypass server. Direct
VLESS+Reality to the NL IP gets frozen by ТСПУ after ~15-20KB; the WORKING bypass is the
CDN-fronted xhttp: client → node67.safetunn.shop:443 (TLS) → h1cloud inbound 1 (xhttp,
sec=none), with the `extra` packet-up/xmux/xPadding obfuscation. That `extra` is
Xray-core/Happ-only (Karing/mihomo can't parse it) → this key belongs in the Happ surface.

This row is provider_kind='xui' (a real 3x-ui panel — the bot provisions `x-user-<id>`
clients on inbound 1 via the panel API, attaching them to that inbound) but
client_surface='happ', so /sub/ serves its link only to Happ imports. `link_template`
(the PR #397 mechanism) makes _build_vless_link emit the full CDN vless:// verbatim with
{UUID} → per-user VLESS UUID; the CDN host + `extra` obfuscation are fixed.

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY. Idempotent.
Replaces the prior wl_set_cdn_row.py one-shot (rows 22-25 were deleted; this re-adds 22).
"""

import asyncio
import os
import sys

CLOUDRU_HOST = ""  # unused; kept for structural parity
ROW_ID = 22

PANEL_URL = "http://193.233.126.96:25100/3cODpzPfiFXNpbwx"
PANEL_USER = "P7nPaR0QG19Q"
PANEL_PASS = "V65gDiLpvNq4FrJbXb3a"  # written to encrypted_password; not stored in plaintext

# Verified-working CDN bypass link (h1cloud). {UUID} placeholder; only the uuid differs per user.
_USER_LINK = (
    "vless://683c8db2-b108-47dd-a0ba-fb981f4c24d6@node67.safetunn.shop:443"
    "?alpn=h2%2Chttp%2F1.1"
    "&extra=%7B%22mode%22%3A%22packet-up%22%2C%22scMaxEachPostBytes%22%3A976846"
    "%2C%22scMinPostsIntervalMs%22%3A70%2C%22seqKey%22%3A%22part_index%22%2C%22seqPlacement%22%3A%22cookie%22"
    "%2C%22sessionIDKey%22%3A%22viewer_session%22%2C%22sessionIDPlacement%22%3A%22cookie%22"
    "%2C%22sessionKey%22%3A%22stream_auth%22%2C%22sessionPlacement%22%3A%22cookie%22"
    "%2C%22uplinkDataPlacement%22%3A%22body%22%2C%22uplinkHTTPMethod%22%3A%22GET%22"
    "%2C%22xPaddingBytes%22%3A%2296-1040%22%2C%22xPaddingHeader%22%3A%22X-Media-Token%22"
    "%2C%22xPaddingKey%22%3A%22_t%22%2C%22xPaddingMethod%22%3A%22tokenish%22"
    "%2C%22xPaddingObfsMode%22%3Atrue%2C%22xPaddingPlacement%22%3A%22queryInHeader%22"
    "%2C%22xmux%22%3A%7B%22cMaxLifetimeMs%22%3A0%2C%22cMaxReuseTimes%22%3A%2236-96%22"
    "%2C%22hMaxRequestTimes%22%3A%22320-640%22%2C%22hMaxReusableSecs%22%3A%22720-1800%22"
    "%2C%22maxConcurrency%22%3A%228-32%22%2C%22maxConnections%22%3A0%7D%7D"
    "&fp=chrome&host=&mode=packet-up&path=%2Fapi%2Fv1%2Fbs1961%2Fsync%2F"
    "&security=tls&sni=node67.safetunn.shop&type=xhttp#h1cloud-CDN"
)
LINK_TEMPLATE = _USER_LINK.replace("683c8db2-b108-47dd-a0ba-fb981f4c24d6", "{UUID}").replace(
    "#h1cloud-CDN", "#🇳🇱 Обход белых списков"
)


async def run() -> None:
    if not os.environ.get("DATABASE_URL") or not os.environ.get("FIELD_ENCRYPTION_KEY"):
        print("ERROR: DATABASE_URL and FIELD_ENCRYPTION_KEY must be set", file=sys.stderr)
        sys.exit(1)

    import asyncpg

    from app.security.field_encryption import encrypt_field

    encrypted = encrypt_field(PANEL_PASS)
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        # Don't clobber an unrelated row that took id=22.
        existing = await pool.fetchrow("SELECT id, label, server_host FROM vpn_servers WHERE id = $1", ROW_ID)
        if existing and "safetunn" not in (existing["server_host"] or ""):
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
                provider_kind, client_surface, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, 443,
                $6, $5, $7, $8, '',
                $9, 1, '', '',
                '', 'chrome', 'xhttp', '', $10,
                'xui', 'happ', TRUE
            )
            """,
            ROW_ID,
            "Обход белых списков",  # label
            "NL",  # country_code
            "🇳🇱",  # country_flag
            "node67.safetunn.shop",  # server_host (+ tls_sni)
            "api/v1/bs1961/sync",  # ws_path (xhttp path)
            PANEL_URL,
            PANEL_USER,
            encrypted,
            LINK_TEMPLATE,
        )
        print(f"Inserted vpn_servers row id={ROW_ID} (h1cloud CDN bypass, client_surface=happ)")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, server_port, transport_type, provider_kind, "
            "       client_surface, inbound_id, is_active "
            "FROM vpn_servers WHERE id = $1",
            ROW_ID,
        )
        print(f"Verified: {dict(row)}")
        print(
            "\nBot provisions x-user-<id> clients on h1cloud inbound 1 (panel API). "
            "Run reconcile_all_users (or reprovision) to seed all active users."
        )
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
