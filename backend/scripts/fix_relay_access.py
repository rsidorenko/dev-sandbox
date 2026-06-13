"""Open ufw on the RU relay for SSH/xray/panel. Minimal: does NOT touch Reality
keys or routing. The relay's host firewall (ufw, INPUT DROP) blocks the 3x-ui
panel port from the production host even when the Yandex security group allows
it. Idempotent.

Runs on the relay via SSH (sudo).
"""
import subprocess


def run(cmd):
    return subprocess.run(cmd, shell=True, capture_output=True, text=True)


# 22 = SSH, 443 = xray VLESS+Reality, 1864 = 3x-ui panel, 2053 = spare panel port
for port in ["22", "443", "1864", "2053"]:
    r = run(f"sudo ufw allow {port}/tcp")
    print(f"ufw allow {port}/tcp: rc={r.returncode} {r.stdout.strip() or r.stderr.strip()[:80]}")

print("\n--- ufw status (relevant) ---")
print(run("sudo ufw status | grep -E '1864|443|22|2053'").stdout or "(no matching rules)")
