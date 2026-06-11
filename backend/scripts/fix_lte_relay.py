"""Fix LTE relay: edit xray config to remove xtls-rprx-vision flow.

Does NOT restart xray - that's handled by the deploy workflow via sudo.
"""

import json
import sys

CONFIG_PATH = "/usr/local/x-ui/bin/config.json"

with open(CONFIG_PATH) as f:
    c = json.load(f)

fixed = False
for o in c.get("outbounds", []):
    if o.get("tag") == "relay-to-frankfurt":
        for v in o.get("settings", {}).get("vnext", []):
            for u in v.get("users", []):
                flow = u.get("flow", "")
                if flow == "xtls-rprx-vision":
                    u["flow"] = ""
                    fixed = True
                    print("FIXED: removed xtls-rprx-vision flow")
                else:
                    print(f"flow already: {flow!r}")

if fixed:
    with open(CONFIG_PATH, "w") as f:
        json.dump(c, f, indent=2)
    print("Config saved")
else:
    print("No fix needed")

# Verify
with open(CONFIG_PATH) as f:
    c2 = json.load(f)
for o in c2.get("outbounds", []):
    if o.get("tag") == "relay-to-frankfurt":
        for v in o.get("settings", {}).get("vnext", []):
            for u in v.get("users", []):
                print(f"Verified relay flow: {u.get('flow', '')!r}")
