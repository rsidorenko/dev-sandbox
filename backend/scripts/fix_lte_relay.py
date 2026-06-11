"""Fix LTE relay: kill xray, edit config, restart xray binary directly.

3x-ui manages config.json and regenerates it from DB on restart.
Strategy: kill ONLY the xray process (not 3x-ui), edit config.json,
then start xray binary directly. 3x-ui won't notice until next panel restart.
"""

import json
import subprocess
import sys

CONFIG_PATH = "/usr/local/x-ui/bin/config.json"
XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64.real"

print("=== Step 1: Kill xray process ===")
result = subprocess.run(["pkill", "-f", "xray-linux-amd64"], capture_output=True, text=True)
print(f"  pkill: rc={result.returncode}")
import time
time.sleep(2)

# Verify it's dead
result = subprocess.run(["pgrep", "-f", "xray-linux-amd64"], capture_output=True, text=True)
if result.returncode == 0:
    print("  WARNING: xray still running!")
    subprocess.run(["pkill", "-9", "-f", "xray-linux-amd64"])
    time.sleep(1)
else:
    print("  xray process killed OK")

print("\n=== Step 2: Edit config.json ===")
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
                    print("  FIXED: removed xtls-rprx-vision flow")
                else:
                    print(f"  flow already: {flow!r}")

if fixed:
    with open(CONFIG_PATH, "w") as f:
        json.dump(c, f, indent=2)
    print("  Config saved")
else:
    print("  No fix needed")

print("\n=== Step 3: Verify config saved ===")
with open(CONFIG_PATH) as f:
    c2 = json.load(f)
for o in c2.get("outbounds", []):
    if o.get("tag") == "relay-to-frankfurt":
        for v in o.get("settings", {}).get("vnext", []):
            for u in v.get("users", []):
                print(f"  relay flow: {u.get('flow', '')!r}")

print("\n=== Step 4: Start xray directly ===")
import os
os.chdir("/usr/local/x-ui/bin")
proc = subprocess.Popen(
    [XRAY_BIN, "-c", "config.json"],
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True
)
print(f"  xray started: PID={proc.pid}")
time.sleep(2)

# Verify
result = subprocess.run(["pgrep", "-f", "xray-linux-amd64"], capture_output=True, text=True)
if result.returncode == 0:
    print(f"  xray running: PID={result.stdout.strip()}")
else:
    print("  ERROR: xray failed to start!")
    sys.exit(1)

print("\n=== DONE ===")
