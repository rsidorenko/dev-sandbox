"""Fix LTE relay outbound: remove xtls-rprx-vision flow from relay-to-frankfurt."""

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
                    print(f"FIXED: removed xtls-rprx-vision flow from relay-to-frankfurt")
                else:
                    print(f"flow already: {flow!r}")

if fixed:
    with open(CONFIG_PATH, "w") as f:
        json.dump(c, f, indent=2)
    print("Config saved. Restart xray to apply.")
else:
    print("No fix needed.")

# Verify
with open(CONFIG_PATH) as f:
    c2 = json.load(f)
for o in c2.get("outbounds", []):
    if o.get("tag") == "relay-to-frankfurt":
        print(f"Verified relay outbound: {json.dumps(o, indent=2)}")
