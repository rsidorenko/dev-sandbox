#!/usr/bin/env bash
# Prod-side deploy script. Runs ON the prod server via `ssh root@prod 'bash -s' < deploy_remote.sh`.
# Артефакт уже залит в /opt/bravada-release (artifact). Скрипт: бэкап БД → сохранение .env/ssl →
# swap → восстановление .env/ssl → docker compose build/up. БД в named-volume pgdata не трогается.
set -e

echo "=== Pre-deploy DB backup (страховка; БД в named-volume pgdata всё равно цела) ==="
mkdir -p /opt/bravada-backups
# Шифрование дампов age (публичный ключ на сервере, секретный — только у оператора):
# PD-аудит 2026-09-11, устранение plaintext-бэкапов ПД. age/pubkey отсутствуют → plaintext + предупреждение.
BACKUP_AGE_PUBKEY_FILE=/root/.config/age/bravada-backup.pub
if command -v age >/dev/null 2>&1 && [ -s "$BACKUP_AGE_PUBKEY_FILE" ]; then
  docker compose -f /opt/bravada/docker-compose.prod.yml exec -T postgres pg_dump -U vpnbot vpnbot 2>/dev/null \
    | gzip | age -r "$(cat "$BACKUP_AGE_PUBKEY_FILE")" > "/opt/bravada-backups/db-$(date +%Y%m%d-%H%M%S).sql.gz.age" \
    || echo "  pg_dump skipped (non-fatal; БД в volume всё равно цела)"
else
  echo "  WARNING: age или $BACKUP_AGE_PUBKEY_FILE не найдены — бэкап НЕ шифруется (plaintext)"
  docker compose -f /opt/bravada/docker-compose.prod.yml exec -T postgres pg_dump -U vpnbot vpnbot 2>/dev/null \
    | gzip > "/opt/bravada-backups/db-$(date +%Y%m%d-%H%M%S).sql.gz" \
    || echo "  pg_dump skipped (non-fatal; БД в volume всё равно цела)"
fi
# Храним последние 10 дампов
ls -t /opt/bravada-backups/db-*.sql.gz /opt/bravada-backups/db-*.sql.gz.age 2>/dev/null | tail -n +11 | xargs -r rm -f

echo "=== Preserve server-side state (.env, ssl) ==="
cp -p /opt/bravada/backend/.env /tmp/backend.env.backup 2>/dev/null || true
cp -p /opt/bravada/.env /tmp/root.env.backup 2>/dev/null || true
# rm обязателен: при существующем /tmp/ssl.backup cp -rp кладёт каталог ВНУТРЬ него,
# и каждый деплой добавлял уровень вложенности ssl/ssl/ssl/... (сотни уровней к 2026-08).
rm -rf /tmp/ssl.backup
cp -rp /opt/bravada/ssl /tmp/ssl.backup 2>/dev/null || true

echo "=== Swap artifact into place ==="
cd /
rm -rf /opt/bravada
mv /opt/bravada-release /opt/bravada

echo "=== Restore server-side state ==="
cp -p /tmp/backend.env.backup /opt/bravada/backend/.env 2>/dev/null || true
cp -p /tmp/root.env.backup /opt/bravada/.env 2>/dev/null || true
mkdir -p /opt/bravada/ssl
cp -a /tmp/ssl.backup/. /opt/bravada/ssl/ 2>/dev/null || true

echo "=== Verify excluded dev paths are absent on server ==="
test ! -e /opt/bravada/.codegraph || { echo "FAIL: .codegraph on server"; exit 1; }
test ! -e /opt/bravada/.claude || { echo "FAIL: .claude on server"; exit 1; }
test ! -e /opt/bravada/scripts/codegraph || { echo "FAIL: scripts/codegraph on server"; exit 1; }

echo "=== Build + start containers ==="
cd /opt/bravada
docker compose -f docker-compose.prod.yml build --no-cache
docker compose -f docker-compose.prod.yml up -d --remove-orphans --force-recreate \
  || docker compose -f docker-compose.prod.yml up -d --remove-orphans
docker image prune -f
docker builder prune -f || true
echo "Deploy complete: $(date)"

echo "=== Refresh systemd timers (reprovision/reconcile) ==="
bash /opt/bravada/backend/scripts/install_reprovision_timer.sh || true
bash /opt/bravada/backend/scripts/install_reconcile_bare_xray_timer.sh || true
