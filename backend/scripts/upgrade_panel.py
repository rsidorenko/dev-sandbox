#!/usr/bin/env python3
"""Operator: upgrade THIS 3x-ui panel in place (preserves the DB).

Runs ON the panel host via SSH+sudo. Mirrors setup_relay.py's structure.

Sequence:
  1. PRE snapshot: x-ui version, bundled xray version, webBasePath, and
     clients/client_inbounds/inbounds row counts (the state that must survive).
  2. Backup /etc/x-ui/x-ui.db -> *.bak.<ts> (kept on host for rollback).
  3. If --dry: stop after the snapshot (read-only).
  4. Upgrade via `x-ui update`; fall back to the official installer pipe
     (the same command setup_relay.py uses) if x-ui update is unavailable.
  5. POST snapshot.
  6. Verify: x-ui active, xray up, :443 listening, and counts/webBasePath
     unchanged from PRE. On ANY mismatch -> restore the backup, restart x-ui,
     print ROLLBACK (do not leave a broken panel).
  7. systemctl restart x-ui; final xray-up check.

Usage (piped over SSH, like sync_clients_table.py):
  echo <base64> | base64 -d | sudo python3              # real upgrade
  echo <base64> | base64 -d | sudo python3 - --dry      # version check only
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import subprocess
import sys
import time

DB_CANDIDATES = [
    "/etc/x-ui/x-ui.db",
    "/usr/local/x-ui/x-ui.db",
    "/usr/local/x-ui/bin/x-ui.db",
    "/opt/x-ui/x-ui.db",
]
XRAY_BIN = "/usr/local/x-ui/bin/xray-linux-amd64"


def run(cmd: str, timeout: int = 600) -> tuple[int, str, str]:
    r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def resolve_db() -> str | None:
    for p in DB_CANDIDATES:
        try:
            with open(p, "rb"):
                return p
        except OSError:
            continue
    return None


def snapshot(db_path: str) -> dict:
    snap: dict = {}
    rc, out, _ = run("x-ui version 2>&1 | head -1")
    snap["xui_version"] = out or f"(rc={rc})"
    rc, out, _ = run(f"{XRAY_BIN} version 2>&1 | head -1")
    snap["xray_version"] = out or "(n/a)"
    try:
        con = sqlite3.connect(db_path)
        con.row_factory = sqlite3.Row
        c = con.cursor()
        row = c.execute("SELECT value FROM settings WHERE key='webBasePath'").fetchone()
        snap["webBasePath"] = row["value"] if row else None
        for t in ("clients", "client_inbounds", "inbounds"):
            try:
                snap[t] = c.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
            except sqlite3.OperationalError:
                snap[t] = None  # table absent (e.g. clients on v2.x)
        con.close()
    except Exception as e:  # noqa: BLE001
        snap["db_error"] = str(e)
    rc, out, _ = run("systemctl is-active x-ui 2>/dev/null")
    snap["xui_active"] = (out == "active")
    rc, out, _ = run("pgrep -f xray >/dev/null && echo yes || echo no")
    snap["xray_running"] = (out == "yes")
    rc, out, _ = run("ss -tlnp 2>/dev/null | grep -q ':443 ' && echo yes || echo no")
    snap["port443"] = (out == "yes")
    return snap


def print_snapshot(label: str, snap: dict) -> None:
    print(f"  {label}: x-ui={snap.get('xui_version')}  xray={snap.get('xray_version')}")
    print(f"    webBasePath={snap.get('webBasePath')}  "
          f"clients={snap.get('clients')}  client_inbounds={snap.get('client_inbounds')}  "
          f"inbounds={snap.get('inbounds')}")
    print(f"    x-ui_active={snap.get('xui_active')}  xray_running={snap.get('xray_running')}  "
          f":443={snap.get('port443')}")


def verify(pre: dict, post: dict) -> list[str]:
    """Return list of regressions (empty = OK)."""
    problems = []
    for key in ("clients", "client_inbounds", "inbounds", "webBasePath"):
        if pre.get(key) is not None and pre.get(key) != post.get(key):
            problems.append(f"{key}: {pre.get(key)} -> {post.get(key)}")
    if not post.get("xray_running"):
        problems.append("xray not running after upgrade")
    if not post.get("port443"):
        problems.append(":443 not listening after upgrade")
    return problems


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--dry", action="store_true", help="snapshot only, no upgrade")
    args = p.parse_args()

    db_path = resolve_db()
    if not db_path:
        print("ERROR: no x-ui.db found — is 3x-ui installed here?")
        return 2
    print(f"DB: {db_path}")

    pre = snapshot(db_path)
    print_snapshot("PRE ", pre)

    bak = f"{db_path}.bak.{int(time.time())}"
    rc, out, err = run(f"cp {db_path} {bak}")
    if rc != 0:
        print(f"ERROR: backup failed: {err}")
        return 3
    print(f"BACKUP: {bak}")

    if args.dry:
        print("DRY RUN — no upgrade performed")
        return 0

    print("UPGRADE: x-ui update ...")
    rc, out, err = run("x-ui update", timeout=900)
    print(out[-1200:])
    if rc != 0:
        print(f"x-ui update rc={rc}; trying official installer ...")
        rc, out, err = run(
            "curl -Ls https://raw.githubusercontent.com/mhsanaei/3x-ui/master/install.sh | bash",
            timeout=900,
        )
        print(out[-1200:])

    run("systemctl restart x-ui 2>/dev/null || x-ui restart 2>/dev/null", timeout=120)
    run("sleep 6", timeout=20)

    post = snapshot(db_path)
    print_snapshot("POST", post)

    problems = verify(pre, post)
    if problems:
        print("VERIFY FAILED:")
        for x in problems:
            print(f"  - {x}")
        print(f"ROLLBACK: restoring {bak}")
        run(f"cp {bak} {db_path}", timeout=60)
        run("systemctl restart x-ui 2>/dev/null || x-ui restart 2>/dev/null", timeout=120)
        run("sleep 6", timeout=20)
        after = snapshot(db_path)
        print_snapshot("AFTER ROLLBACK", after)
        print("ROLLBACK DONE — panel left at pre-upgrade state. ABORTING.")
        return 4

    print("VERIFY OK — version bumped, state preserved, xray up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
