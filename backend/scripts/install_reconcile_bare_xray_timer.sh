#!/usr/bin/env bash
# Install the periodic bare_xray desired-state reconcile timer on PROD (the bot host).
#
# Why: bare_xray inbounds (raw xray boxes — Yandex / cloud.ru / Helsinki) drifted out
# of sync — a host lacked grpcurl so new users were never added, an inbound got wiped
# at runtime (Helsinki clr-tcp=0), and gRPC AlterInbound is not persisted to config.json
# so an xray restart loses clients. The live per-event add/remove is best-effort and
# non-recovering. This timer runs reconcile_bare_xray.py (in the backend container)
# every 3 min: it makes each bare_xray inbound's live client list equal the active-user
# set via gRPC AlterInbound (add missing, remove orphans). NO xray restart -> connected
# users feel nothing. Hosts come from vpn_servers, never hardcoded.
#
# Prereq: grpcurl must be present on every bare_xray host (copy the standalone binary
# to /usr/local/bin/grpcurl). Without it that host is skipped with a warning.
#
# Run on PROD as a user with sudo (the deploy user has passwordless sudo):
#     bash /opt/bravada/backend/scripts/install_reconcile_bare_xray_timer.sh
#
# Bulletproof: NEVER exits non-zero (so a timer-install hiccup can't break deploy).
# Idempotent — safe to re-run on every deploy.
set -uo pipefail   # NOTE: intentionally NO `set -e`

HERE="$(cd "$(dirname "$0")" && pwd)"
SVC="$HERE/reconcile-bare-xray.service"
TMR="$HERE/reconcile-bare-xray.timer"

if [ ! -f "$SVC" ] || [ ! -f "$TMR" ]; then
    echo "reconcile-bare-xray-timer: missing unit files in $HERE — skip (nothing to install yet)"
    exit 0
fi

echo "reconcile-bare-xray-timer: installing systemd units"
sudo install -m 0644 "$SVC" /etc/systemd/system/reconcile-bare-xray.service 2>/dev/null \
    || { echo "reconcile-bare-xray-timer: install .service failed (no sudo?) — skip"; exit 0; }
sudo install -m 0644 "$TMR" /etc/systemd/system/reconcile-bare-xray.timer 2>/dev/null \
    || { echo "reconcile-bare-xray-timer: install .timer failed — skip"; exit 0; }

sudo systemctl daemon-reload 2>/dev/null || true
sudo systemctl enable reconcile-bare-xray.timer 2>/dev/null || true
sudo systemctl start  reconcile-bare-xray.timer 2>/dev/null || true

echo "reconcile-bare-xray-timer: enabled (every 3 min). Triggering one run now (heals current users):"
sudo systemctl start reconcile-bare-xray.service 2>/dev/null \
    && echo "reconcile-bare-xray-timer: first run started (see: journalctl -u reconcile-bare-xray.service)" \
    || echo "reconcile-bare-xray-timer: first run did not start (docker/backend not up yet?) — timer will retry in 3 min"

exit 0
