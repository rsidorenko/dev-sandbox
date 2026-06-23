#!/usr/bin/env python3
"""Dump the :443 (1.0 tcp/reality) inbound settings + each client's FLOW from config.json.
Run ON the panel. The client 'flow' field must be 'xtls-rprx-vision' for 1.0 keys to work."""
import json, collections
try:
    c = json.load(open("/usr/local/x-ui/bin/config.json"))
except Exception as e:
    print("config read err:", e)
    raise SystemExit
for ib in c.get("inbounds", []):
    ss = ib.get("streamSettings", {}) or {}
    if ss.get("security") == "reality" and ss.get("network") in ("tcp", None):
        rs = ss.get("realitySettings", {}) or {}
        cl = (ib.get("settings", {}) or {}).get("clients") or []
        print(f"=== 1.0 inbound tag={ib.get('tag')} port={ib.get('port')} n_clients={len(cl)} ===")
        print("  realitySettings keys:", list(rs.keys()))
        print("  publicKey:", rs.get("publicKey"))
        print("  shortIds:", rs.get("shortIds"))
        print("  serverNames:", rs.get("serverNames"))
        print("  dest:", rs.get("dest"))
        flow_counts = collections.Counter(str(x.get("flow")) for x in cl)
        print("  CLIENT FLOW distribution:", dict(flow_counts))
        for x in cl[:3]:
            print(f"   sample: flow={x.get('flow')!r} email={x.get('email')}")
