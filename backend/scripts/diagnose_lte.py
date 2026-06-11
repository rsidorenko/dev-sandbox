"""Diagnose LTE relay chain: LTE panel → Frankfurt panel via 3x-ui APIs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import sys

import httpx


async def _panel_login(client: httpx.AsyncClient, panel_url: str, username: str, password: str) -> dict:
    """Login to 3x-ui panel, return auth headers."""
    page = await client.get(f"{panel_url}/")
    csrf = None
    if page.status_code == 200:
        m = re.search(r'csrf-token" content="([^"]+)"', page.text)
        if m:
            csrf = m.group(1)
    headers = {"Content-Type": "application/json"}
    if csrf:
        headers["X-CSRF-Token"] = csrf

    login = await client.post(f"{panel_url}/login",
        json={"username": username, "password": password}, headers=headers)
    if login.status_code != 200 or not login.json().get("success"):
        raise RuntimeError(f"Login failed: {login.status_code}")
    return headers


async def _get_inbounds(client: httpx.AsyncClient, panel_url: str, headers: dict) -> list:
    """Get all inbounds from panel."""
    resp = await client.get(f"{panel_url}/panel/api/inbounds/list", headers=headers)
    if resp.status_code != 200:
        raise RuntimeError(f"List inbounds failed: {resp.status_code}")
    return resp.json().get("obj", [])


async def run() -> None:
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("ERROR: DATABASE_URL not set", file=sys.stderr)
        sys.exit(1)

    import asyncpg
    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        from app.security.field_encryption import decrypt_field

        # Get all server configs
        rows = await pool.fetch(
            "SELECT id, label, panel_url, panel_username, panel_password, "
            "COALESCE(encrypted_password, '') AS ep, "
            "server_host, server_port, transport_type, tls_sni, reality_sni, "
            "reality_pbk, reality_sid, inbound_id "
            "FROM vpn_servers ORDER BY id"
        )
    finally:
        await pool.close()

    servers = []
    for r in rows:
        pw = decrypt_field(r["ep"]) if r["ep"] else r["panel_password"]
        servers.append({
            "id": r["id"],
            "label": r["label"],
            "panel_url": r["panel_url"].rstrip("/"),
            "username": r["panel_username"],
            "password": pw,
            "host": r["server_host"],
            "port": r["server_port"],
            "transport": r["transport_type"],
            "inbound_id": r["inbound_id"],
        })

    # Find LTE and Frankfurt servers
    lte_servers = [s for s in servers if s["id"] in (10, 12)]
    frankfurt_servers = [s for s in servers if "77.110.100.210" in s["host"]]
    all_servers = lte_servers + frankfurt_servers

    print("=" * 60)
    print("LTE RELAY CHAIN DIAGNOSTIC")
    print("=" * 60)
    print(f"LTE servers: {[s['label'] for s in lte_servers]}")
    print(f"Frankfurt servers (relay target): {[s['label'] for s in frankfurt_servers]}")
    print(f"Frankfurt IP: 77.110.100.210")

    async with httpx.AsyncClient(verify=False, follow_redirects=True, timeout=15) as client:
        # ── Check each server panel ──
        for srv in all_servers:
            print(f"\n{'=' * 60}")
            print(f"SERVER {srv['id']}: {srv['label']} ({srv['host']})")
            print(f"Panel: {srv['panel_url']}")
            print(f"{'=' * 60}")

            try:
                headers = await _panel_login(client, srv["panel_url"], srv["username"], srv["password"])
                print("Login: OK")
            except Exception as e:
                print(f"Login: FAILED - {e}")
                continue

            try:
                inbounds = await _get_inbounds(client, srv["panel_url"], headers)
                print(f"Inbounds: {len(inbounds)}")
            except Exception as e:
                print(f"Get inbounds: FAILED - {e}")
                continue

            for ib in inbounds:
                settings = ib.get("settings", {})
                if isinstance(settings, str):
                    try:
                        settings = json.loads(settings)
                    except:
                        settings = {}
                clients = settings.get("clients", [])
                stream = ib.get("streamSettings", {})
                net = stream.get("network", "?")
                sec = stream.get("security", "?")

                print(f"\n  Inbound ID={ib['id']} port={ib['port']} proto={ib['protocol']} "
                      f"enable={ib.get('enable')} net={net} sec={sec}")

                # Show transport details
                if "realitySettings" in stream:
                    rs = stream["realitySettings"]
                    dest = rs.get("dest", "?")
                    sni = rs.get("serverNames", [])
                    short_ids = rs.get("shortIds", [])
                    print(f"    Reality: dest={dest} sni={sni} shortIds={short_ids}")
                if "wsSettings" in stream:
                    ws = stream["wsSettings"]
                    print(f"    WS: path={ws.get('path','?')} host={ws.get('headers',{}).get('Host','?')}")

                print(f"    Clients: {len(clients)}")
                for c in clients[:5]:
                    email = c.get("email", "?")
                    flow = c.get("flow", "")
                    uuid = c.get("id", "")
                    limit_ip = c.get("limitIp", 0)
                    enable = c.get("enable", True)
                    print(f"      {email}: uuid={uuid[:12]}... flow=\"{flow}\" "
                          f"limitIp={limit_ip} enable={enable}")
                if len(clients) > 5:
                    print(f"      ... and {len(clients) - 5} more")

            # Try to get xray config (outbounds) via API
            print(f"\n  --- Xray config via API ---")
            for endpoint in [
                "/panel/api/setting/all",
                "/panel/api/setting/config",
            ]:
                try:
                    resp = await client.get(f"{srv['panel_url']}{endpoint}", headers=headers)
                    if resp.status_code == 200:
                        data = resp.json()
                        obj = data.get("obj", data)
                        if isinstance(obj, dict):
                            for key in obj:
                                val = obj[key]
                                if isinstance(val, str) and len(val) > 200:
                                    try:
                                        parsed = json.loads(val)
                                        if isinstance(parsed, dict) and ("outbounds" in parsed or "routing" in parsed):
                                            print(f"    Found xray config in '{key}'!")
                                            for ob in parsed.get("outbounds", []):
                                                tag = ob.get("tag", "?")
                                                proto = ob.get("protocol", "?")
                                                addr = ""
                                                port = ""
                                                if proto == "vless":
                                                    vnext = ob.get("settings", {}).get("vnext", [])
                                                    if vnext:
                                                        addr = vnext[0].get("address", "?")
                                                        port = vnext[0].get("port", "?")
                                                    stream_ob = ob.get("streamSettings", {})
                                                    print(f"    OUTBOUND {tag}: {proto} -> {addr}:{port}")
                                                    print(f"      network={stream_ob.get('network','?')} "
                                                          f"security={stream_ob.get('security','?')}")
                                                    if "tlsSettings" in stream_ob:
                                                        tls = stream_ob["tlsSettings"]
                                                        print(f"      TLS sni={tls.get('serverName','?')}")
                                                elif proto == "freedom":
                                                    print(f"    OUTBOUND {tag}: direct")
                                                elif proto == "blackhole":
                                                    print(f"    OUTBOUND {tag}: blocked")
                                                else:
                                                    print(f"    OUTBOUND {tag}: {proto}")
                                            routing = parsed.get("routing", {})
                                            print(f"    ROUTING ({len(routing.get('rules',[]))} rules):")
                                            for rule in routing.get("rules", [])[:8]:
                                                rtype = rule.get("type", "?")
                                                otag = rule.get("outboundTag", "?")
                                                ips = rule.get("ip", [])[:3]
                                                domains = rule.get("domain", [])[:3]
                                                print(f"      {rtype} -> {otag} ip={ips} domain={domains}")
                                            break  # Found it, stop looking
                                    except json.JSONDecodeError:
                                        pass
                        break  # First working endpoint is enough
                except Exception:
                    pass

        # ── Cross-reference: check if relay target exists ──
        print(f"\n{'=' * 60}")
        print("CROSS-REFERENCE: LTE relay → Frankfurt inbound")
        print(f"{'=' * 60}")
        print("LTE outbound points to: 77.110.100.210 (Frankfurt)")
        if frankfurt_servers:
            print(f"Frankfurt panel has server(s): {[s['label'] for s in frankfurt_servers]}")
        else:
            print("WARNING: No Frankfurt server found in vpn_servers table!")
            print("  The relay target 77.110.100.210 is NOT managed by this bot.")
            print("  Need manual check on Frankfurt panel.")


if __name__ == "__main__":
    asyncio.run(run())
