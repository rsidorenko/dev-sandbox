#!/usr/bin/env bash
# Install the periodic 3x-ui clients-table sync (v3 desync prevention) on a panel.
#
# Why: the bot writes clients only to inbounds.settings JSON; 3x-ui v3 generates
# xray config from the `clients` table, so bot-written clients are rejected until
# mirrored. This timer mirrors settings→table every 5 min and restarts x-ui ONLY
# when the table changed.
#
# Run AS ROOT on each 3x-ui v3 panel (Helsinki / Frankfurt / LA):
#     sudo bash backend/scripts/install_sync_timer.sh
#
# Idempotent — safe to re-run. Verify with:
#     systemctl list-timers sync-clients-table.timer
#     journalctl -u sync-clients-table.service -n 20 --no-pager
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "ERROR: run as root (sudo)." >&2
    exit 1
fi

HERE="$(cd "$(dirname "$0")" && pwd)"
SCRIPT_SRC="$HERE/sync_clients_table.py"

if [[ ! -f "$SCRIPT_SRC" ]]; then
    echo "ERROR: $SCRIPT_SRC not found (run from the repo)." >&2
    exit 1
fi

echo "==> Installing sync_clients_table.py → /usr/local/bin/"
install -m 0755 "$SCRIPT_SRC" /usr/local/bin/sync_clients_table.py

echo "==> Installing systemd unit + timer"
install -m 0644 "$HERE/sync-clients-table.service" /etc/systemd/system/sync-clients-table.service
install -m 0644 "$HERE/sync-clients-table.timer"   /etc/systemd/system/sync-clients-table.timer

systemctl daemon-reload
systemctl enable --now sync-clients-table.timer

echo
echo "==> Installed. Timer:"
systemctl list-timers sync-clients-table.timer --no-pager | sed -n '1,3p'
echo
echo "==> Running once now (no --restart-if-changed, dry read) to verify:"
/usr/bin/python3 /usr/local/bin/sync_clients_table.py || true
echo
echo "Done. Logs: journalctl -u sync-clients-table.service --no-pager"
