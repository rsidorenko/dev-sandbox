"""Insert a vpn_servers row for an LTE-fleet entry (lla/lff/lhh) OR repoint id=10.

Each LTE entry is a whitelisted RU IP mobile users connect to directly; foreign
traffic relays to an existing foreign server (Frankfurt/Helsinki/LA). The row holds
the ENTRY's own Reality public key (what clients connect to) — NOT the relay target's.

Takes the values printed by setup_lte.py as env vars:
  LTE_ROW_ID         explicit vpn_servers id (12=ЛА LTE, 13=Хельсинки LTE, 14=Франкфурт 2 LTE)
  LTE_LABEL          bot label, e.g. "Лос-Анджелес LTE"
  LTE_COUNTRY_CODE   US / FI / DE
  LTE_COUNTRY_FLAG   🇺🇸 / 🇫🇮 / 🇩🇪
  LTE_SERVER_HOST    this entry's public IP (e.g. 194.67.120.48)
  LTE_PANEL_URL      panel URL incl. webBasePath
  LTE_PANEL_USER     panel username (bravada / romtan)
  LTE_PANEL_PASS     panel password (-> encrypted_password via FIELD_ENCRYPTION_KEY)
  LTE_INBOUND_ID     the :443 inbound id created by setup_lte
  LTE_REALITY_PBK    this entry's Reality PUBLIC key (REALITY_PUBKEY from setup)
  LTE_REALITY_SID    Reality shortId (default a1b2c3d4e5f6)
  LTE_REALITY_SNI    this entry's camo SNI (lla/lff/lhh.bravada-connect.online)

Runs in the production container with DATABASE_URL + FIELD_ENCRYPTION_KEY.

Safety: refuses to clobber an existing id that belongs to a different server_host
(guard against a wrong LTE_ROW_ID). Once inserted with is_active=TRUE, the bot picks
it up (_load_server_configs) and reconcile_all_users provisions every non-deleted
user's UUID on the new inbound at next restart. flow=xtls-rprx-vision is emitted by
flow_for_transport() because the id is in _VISION_SERVERS; sync_clients_table.py
mirrors flow -> client_inbounds.flow_override on the panel.
"""

import asyncio
import os
import sys


async def run():
    if not os.environ.get("DATABASE_URL"):
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    # Optional: LTE_ROW_JSON carries all LTE_* fields as one blob (used by the
    # relay-setup workflow). Populates env (without overriding explicit env), then
    # the per-field reads below work unchanged.
    _raw_json = os.environ.get("LTE_ROW_JSON", "").strip()
    if _raw_json:
        import json
        for _k, _v in json.loads(_raw_json).items():
            os.environ.setdefault(str(_k).upper(), "" if _v is None else str(_v))

    row_id = os.environ.get("LTE_ROW_ID", "").strip()
    label = os.environ.get("LTE_LABEL", "").strip()
    country_code = os.environ.get("LTE_COUNTRY_CODE", "").strip()
    country_flag = os.environ.get("LTE_COUNTRY_FLAG", "").strip()
    server_host = os.environ.get("LTE_SERVER_HOST", "").strip()
    panel_url = os.environ.get("LTE_PANEL_URL", "").strip()
    panel_user = os.environ.get("LTE_PANEL_USER", "bravada").strip()
    panel_pass = os.environ.get("LTE_PANEL_PASS", "").strip()
    inbound_id = os.environ.get("LTE_INBOUND_ID", "").strip()
    reality_pbk = os.environ.get("LTE_REALITY_PBK", "").strip()
    reality_sid = os.environ.get("LTE_REALITY_SID", "a1b2c3d4e5f6").strip()
    reality_sni = os.environ.get("LTE_REALITY_SNI", "").strip()

    missing = [k for k, v in [
        ("LTE_ROW_ID", row_id), ("LTE_LABEL", label), ("LTE_COUNTRY_CODE", country_code),
        ("LTE_COUNTRY_FLAG", country_flag), ("LTE_SERVER_HOST", server_host),
        ("LTE_PANEL_URL", panel_url), ("LTE_PANEL_PASS", panel_pass),
        ("LTE_INBOUND_ID", inbound_id), ("LTE_REALITY_PBK", reality_pbk),
        ("LTE_REALITY_SNI", reality_sni),
    ] if not v]
    if missing:
        print(f"ERROR: missing env: {missing}", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    from app.security.field_encryption import encrypt_field

    encrypted = encrypt_field(panel_pass)
    pool = await asyncpg.create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=3)
    try:
        # Guard: refuse to clobber an id that belongs to a DIFFERENT server. Same
        # label = same server being rebuilt/repointed (e.g. id=10 Yandex IP change),
        # which is allowed (host may change). Different label = wrong LTE_ROW_ID.
        existing = await pool.fetchrow(
            "SELECT id, label, server_host FROM vpn_servers WHERE id = $1", int(row_id))
        if existing and existing["label"] != label:
            print(f"ERROR: id={row_id} already used by {existing['label']!r} "
                  f"({existing['server_host']}). Pick a different LTE_ROW_ID.", file=sys.stderr)
            sys.exit(1)

        await pool.execute("DELETE FROM vpn_servers WHERE id = $1", int(row_id))
        await pool.execute(
            """
            INSERT INTO vpn_servers (
                id, label, country_code, country_flag, server_host, server_port,
                ws_path, tls_sni, panel_url, panel_username, panel_password,
                encrypted_password, inbound_id, reality_pbk, reality_sid,
                reality_sni, reality_fp, transport_type, is_active
            ) VALUES (
                $1, $2, $3, $4, $5, 443,
                '/ws', NULL, $6, $7, '',
                $8, $9, $10, $11,
                $12, 'chrome', 'tcp', TRUE
            )
            """,
            int(row_id), label, country_code, country_flag, server_host,
            panel_url, panel_user, encrypted, int(inbound_id),
            reality_pbk, reality_sid, reality_sni,
        )
        print(f"Inserted vpn_servers row id={row_id} ({server_host}) label={label!r}")

        row = await pool.fetchrow(
            "SELECT id, label, server_host, server_port, inbound_id, reality_pbk, "
            "       reality_sid, reality_sni, transport_type, is_active "
            "FROM vpn_servers WHERE id = $1", int(row_id))
        print(f"Verified: {dict(row)}")

        count = await pool.fetchval("SELECT count(*) FROM vpn_servers WHERE is_active = TRUE")
        print(f"Total active servers: {count}")
        print("\nThe bot provisions all active users on this inbound at next restart "
              "(reconcile_all_users). Vision flow is emitted (id in _VISION_SERVERS); "
              "sync_clients_table.py mirrors flow -> flow_override on the panel.")
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(run())
