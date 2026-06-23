#!/usr/bin/env bash
# Install the periodic active-user re-provision timer on PROD (the bot host).
#
# Why: when a user renews, the bot updates DB active_until but sometimes not the
# 3x-ui panel client expiryTime -> x-ui auto-disables the client (enable=false)
# -> "VPN not working" despite an active sub. This timer runs reprovision_active.py
# (in the backend container) every 30 min, which idempotently upserts every active
# user's client with enable=True + correct expiry. No xray restart -> connected
# users feel nothing.
#
# Run on PROD as a user with sudo (the deploy user has passwordless sudo):
#     bash /opt/bravada/backend/scripts/install_reprovision_timer.sh
#
# Bulletproof: NEVER exits non-zero (so a timer-install hiccup can't break deploy).
# Idempotent — safe to re-run on every deploy.
set -uo pipefail   # NOTE: intentionally NO `set -e`

HERE="$(cd "$(dirname "$0")" && pwd)"
SVC="$HERE/reprovision-active.service"
TMR="$HERE/reprovision-active.timer"

if [ ! -f "$SVC" ] || [ ! -f "$TMR" ]; then
    echo "reprovision-timer: missing unit files in $HERE — skip (nothing to install yet)"
    exit 0
fi

echo "reprovision-timer: installing systemd units"
sudo install -m 0644 "$SVC" /etc/systemd/system/reprovision-active.service 2>/dev/null \
    || { echo "reprovision-timer: install .service failed (no sudo?) — skip"; exit 0; }
sudo install -m 0644 "$TMR" /etc/systemd/system/reprovision-active.timer 2>/dev/null \
    || { echo "reprovision-timer: install .timer failed — skip"; exit 0; }

sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl enable reprovision-active.timer 2>/dev/null || true
sudo systemctl start  reprovision-active.timer 2>/dev/null || true

echo "reprovision-timer: enabled (every 30 min). Triggering one run now (heals current users):"
sudo systemctl start reprovision-active.service 2>/dev/null \
    && echo "reprovision-timer: first run started (see: journalctl -u reprovision-active.service)" \
    || echo "reprovision-timer: first run did not start (docker/backend not up yet?) — timer will retry in 3 min"

exit 0
