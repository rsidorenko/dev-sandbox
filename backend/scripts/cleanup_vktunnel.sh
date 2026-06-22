#!/bin/bash
# Purge vk-tunnel leftovers from a box (bgg / 84.201.144.227).
# vk-tunnel is dead (VK Access denied, Issue #101). Run via:
#   echo <base64 of this file> | base64 -d | sudo bash
set +e

echo "=== stop + remove vk-tunnel.service ==="
systemctl disable --now vk-tunnel 2>/dev/null
rm -f /etc/systemd/system/vk-tunnel.service
systemctl daemon-reload 2>/dev/null

echo "=== remove the ws inbound from 3x-ui ==="
DB=$(for p in /etc/x-ui/x-ui.db /usr/local/x-ui/x-ui.db /usr/local/x-ui/bin/x-ui.db /opt/x-ui/x-ui.db; do
  [ -f "$p" ] && echo "$p" && break
done)
if [ -n "$DB" ]; then
  sqlite3 "$DB" "DELETE FROM inbounds WHERE tag='in-vk-tunnel-ws' OR port=12345 OR remark='VK-Tunnel-WS';" 2>/dev/null \
    && echo "  removed vk-tunnel inbound from $DB"
fi

echo "=== remove cached OAuth token + domain extractor + cron ==="
rm -rf /var/lib/vk-tunnel
rm -f /usr/local/bin/vk-tunnel-domain.sh /etc/cron.d/vk-tunnel-domain

echo "=== uninstall npm package ==="
npm uninstall -g @vkontakte/vk-tunnel 2>/dev/null

echo "=== restart x-ui (drops the deleted inbound from xray config) ==="
x-ui restart 2>/dev/null || systemctl restart x-ui 2>/dev/null

echo "vk-tunnel cleanup done"
