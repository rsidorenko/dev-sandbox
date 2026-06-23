#!/usr/bin/env python3
"""Wipe all bot-managed clients from 3x-ui panels and rebuild from DB.

Operator script — runs from production backend container.
Requires: DATABASE_URL, FIELD_ENCRYPTION_KEY env vars.

The bot's PostgreSQL database is the source of truth. This script:
1. Reads all active users (state_label='active', vless_uuid IS NOT NULL)
2. Reads all active vpn_servers
3. Wipes bot-managed clients from each panel (preserves relay/service UUIDs)
4. Re-adds every active user on every active server
5. Verifies the result

Usage:
  # Dry run (shows what would happen, no changes):
  python3 rebuild_panel_clients.py --dry-run

  # Full rebuild (wipe + re-add):
  python3 rebuild_panel_clients.py

  # Verify only (check current state, no changes):
  python3 rebuild_panel_clients.py --verify
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from collections import OrderedDict
from datetime import UTC, datetime, timedelta

import asyncpg
import httpx

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRIAL_DEVICE_LIMIT = 5
_DEFAULT_EXPIRY_DAYS = 365
_LOGIN_SESSION_TTL_SECONDS = 300
_DEFAULT_TIMEOUT = 10.0

# Bot-managed email prefixes (see _email_from_internal in xui_vless_provider.py)
_BOT_EMAIL_PREFIXES = ("user-", "x-user-", "cdn-user-")


def _is_bot_email(email: str) -> bool:
    """Check if an email belongs to a bot-managed user."""
    return any(email.startswith(p) for p in _BOT_EMAIL_PREFIXES)


def _email_from_internal(internal_user_id: str, *, transport_type: str = "tcp") -> str:
    """Mirror of xui_vless_provider._email_from_internal."""
    prefix = {"xhttp": "x-", "cdn": "cdn-"}.get(transport_type, "")
    return f"{prefix}user-{internal_user_id[:16]}"


def _vless_uuid_for_transport(internal_user_id: str, transport_type: str) -> str:
    """Mirror of xui_vless_provider._vless_uuid_for_transport — distinct uuid per
    (user, transport) so each inbound's client is unique (fixes v3 client_inbounds)."""
    return str(uuid.uuid5(
        uuid.NAMESPACE_DNS,
        f"vpn.bravada.internal.{internal_user_id}.{transport_type}",
    ))


def _expiry_from_datetime(dt: datetime | None) -> int:
    """Convert datetime to unix timestamp in milliseconds. Fallback: 365 days."""
    if dt is not None and dt > datetime.now(UTC):
        return int(dt.timestamp() * 1000)
    future = datetime.now(UTC) + timedelta(days=_DEFAULT_EXPIRY_DAYS)
    return int(future.timestamp() * 1000)


# ---------------------------------------------------------------------------
# Panel HTTP client (simplified XuiApiClient for script use)
# ---------------------------------------------------------------------------

class PanelClient:
    """Minimal 3x-ui panel HTTP client for rebuild operations."""

    def __init__(self, panel_url: str, username: str, password: str, inbound_id: int,
                 server_id: int, label: str, transport_type: str,
                 server_host: str, server_port: int):
        self.base = panel_url.rstrip("/")
        self.username = username
        self.password = password
        self.inbound_id = inbound_id
        self.server_id = server_id
        self.label = label
        self.transport_type = transport_type
        self.server_host = server_host
        self.server_port = server_port
        self._client: httpx.AsyncClient | None = None
        self._last_login_ts: float = 0.0
        self._csrf_token: str = ""

    async def _get_http(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(verify=False, timeout=_DEFAULT_TIMEOUT)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _login(self) -> bool:
        client = await self._get_http()
        try:
            # Get CSRF token
            await client.get(f"{self.base}/")
            csrf_token = ""
            try:
                csrf_resp = await client.get(f"{self.base}/csrf-token")
                if csrf_resp.status_code == 200:
                    csrf_data = csrf_resp.json()
                    csrf_token = csrf_data.get("obj", "") or csrf_data.get("token", "")
            except Exception:
                pass
            if not csrf_token:
                page = await client.get(f"{self.base}/")
                csrf_match = re.search(r'csrf-token.*?content="([^"]+)"', page.text)
                csrf_token = csrf_match.group(1) if csrf_match else ""

            headers = {}
            if csrf_token:
                headers["X-CSRF-Token"] = csrf_token

            resp = await client.post(
                f"{self.base}/login",
                json={"username": self.username, "password": self.password},
                headers=headers,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success", False):
                    self._last_login_ts = time.monotonic()
                    self._csrf_token = csrf_token
                    return True
            print(f"  ❌ LOGIN FAILED server={self.server_id} url={self.base} status={resp.status_code}")
            return False
        except Exception as e:
            print(f"  ❌ LOGIN ERROR server={self.server_id}: {e}")
            return False

    async def _ensure_session(self) -> bool:
        if time.monotonic() - self._last_login_ts < _LOGIN_SESSION_TTL_SECONDS:
            return True
        return await self._login()

    def _headers(self) -> dict:
        h = {}
        if self._csrf_token:
            h["X-CSRF-Token"] = self._csrf_token
        return h

    async def get_inbound(self) -> dict | None:
        """Fetch full inbound object from panel."""
        if not await self._ensure_session():
            return None
        client = await self._get_http()
        try:
            resp = await client.get(
                f"{self.base}/panel/api/inbounds/get/{self.inbound_id}",
                headers=self._headers(),
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("success"):
                    return body["obj"]
        except Exception as e:
            print(f"  ❌ get_inbound error server={self.server_id}: {e}")
        return None

    async def update_inbound(self, inbound: dict) -> bool:
        """Update inbound with modified settings."""
        if not await self._ensure_session():
            return False
        client = await self._get_http()
        payload = {
            "id": inbound["id"],
            "settings": json.dumps(inbound.get("settings", {}))
                if isinstance(inbound.get("settings"), dict)
                else inbound.get("settings", ""),
            "streamSettings": json.dumps(inbound["streamSettings"])
                if isinstance(inbound.get("streamSettings"), dict)
                else inbound.get("streamSettings", ""),
            "sniffing": json.dumps(inbound["sniffing"])
                if isinstance(inbound.get("sniffing"), dict)
                else inbound.get("sniffing", ""),
            "protocol": inbound["protocol"],
            "port": inbound["port"],
            "listen": inbound.get("listen", ""),
            "tag": inbound.get("tag", ""),
            "remark": inbound.get("remark", ""),
            "enable": inbound.get("enable", True),
            "expiryTime": inbound.get("expiryTime", 0),
            "total": inbound.get("total", 0),
            "up": inbound.get("up", 0),
            "down": inbound.get("down", 0),
        }
        try:
            resp = await client.post(
                f"{self.base}/panel/api/inbounds/update/{self.inbound_id}",
                json=payload,
                headers=self._headers(),
            )
            if resp.status_code == 200:
                body = resp.json()
                return body.get("success", False)
            print(f"  ❌ update_inbound failed server={self.server_id} status={resp.status_code}")
        except Exception as e:
            print(f"  ❌ update_inbound error server={self.server_id}: {e}")
        return False

    async def add_client(self, *, user_uuid: str, email: str,
                         expiry_ts: int, enable: bool = True,
                         limit_ip: int = 0) -> bool:
        """Add a single client via read-modify-write (v3 compatible)."""
        inbound = await self.get_inbound()
        if not inbound:
            return False

        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)

        clients = settings.setdefault("clients", [])

        new_client = {
            "id": user_uuid,
            "email": email,
            "enable": enable,
            "expiryTime": expiry_ts,
            "flow": "xtls-rprx-vision" if self.transport_type == "tcp" else "",
            "limitIp": limit_ip,
            "totalGB": 0,
            "tgId": "",
            "subId": "",
        }

        # Upsert: replace existing with same email/uuid, else append
        replaced = False
        for i, c in enumerate(clients):
            if c.get("email") == email or c.get("id") == user_uuid:
                clients[i] = new_client
                replaced = True
                break
        if not replaced:
            clients.append(new_client)

        inbound["settings"] = settings
        return await self.update_inbound(inbound)

    async def resolve_client_uuid(self, *, email: str) -> str | None:
        """Find a client's UUID by email on this panel."""
        inbound = await self.get_inbound()
        if not inbound:
            return None
        settings = inbound.get("settings", {})
        if isinstance(settings, str):
            settings = json.loads(settings)
        for c in settings.get("clients", []):
            if c.get("email") == email:
                return c.get("id")
        return None


# ---------------------------------------------------------------------------
# DB helpers
# ---------------------------------------------------------------------------

async def _load_active_users(pool: asyncpg.Pool) -> list[dict]:
    """Load all active users. VLESS UUIDs are derived per-transport (uuid5), so we
    do NOT filter on user_identities.vless_uuid (it is NULL for everyone since
    PR #320); the per-transport uuid is computed via _vless_uuid_for_transport."""
    rows = await pool.fetch(
        """SELECT i.internal_user_id, i.vless_uuid,
                  s.device_count, s.active_until_utc
           FROM user_identities i
           JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
           WHERE s.state_label = 'active'
           ORDER BY i.internal_user_id"""
    )
    return [
        {
            "internal_user_id": r["internal_user_id"],
            "vless_uuid": r["vless_uuid"],
            "device_count": r["device_count"] or 0,
            "active_until_utc": r["active_until_utc"],
        }
        for r in rows
    ]


async def _load_server_configs(pool: asyncpg.Pool) -> list[dict]:
    """Load all active server configs."""
    from app.security.field_encryption import decrypt_field

    rows = await pool.fetch(
        """SELECT id, label, server_host, server_port,
                  panel_url, panel_username, panel_password,
                  COALESCE(encrypted_password, '') AS encrypted_password,
                  inbound_id, COALESCE(transport_type, 'tcp') AS transport_type
           FROM vpn_servers WHERE is_active = TRUE ORDER BY id"""
    )
    configs = []
    for r in rows:
        encrypted = r.get("encrypted_password", "")
        if encrypted:
            password = decrypt_field(encrypted)
        else:
            password = r.get("panel_password", "")
        configs.append({
            "server_id": r["id"],
            "label": r["label"],
            "server_host": r["server_host"],
            "server_port": r["server_port"],
            "panel_url": r["panel_url"],
            "panel_username": r["panel_username"],
            "panel_password": password,
            "inbound_id": r["inbound_id"],
            "transport_type": r["transport_type"],
        })
    return configs


# ---------------------------------------------------------------------------
# Main phases
# ---------------------------------------------------------------------------

async def phase_wipe(panels: list[PanelClient], dry_run: bool = False) -> dict:
    """Wipe bot-managed clients from all panels. Preserve relay/service UUIDs."""
    print("\n" + "=" * 60)
    print("PHASE: WIPE BOT-MANAGED CLIENTS")
    print("=" * 60)

    summary: dict[int, dict] = {}

    # Group by panel_url to run sequential per-panel
    by_panel: OrderedDict[str, list[PanelClient]] = OrderedDict()
    for p in panels:
        by_panel.setdefault(p.base, []).append(p)

    for panel_url, panel_clients in by_panel.items():
        print(f"\n--- Panel: {panel_url} ({len(panel_clients)} inbounds) ---")
        for pc in panel_clients:
            inbound = await pc.get_inbound()
            if inbound is None:
                print(f"  ❌ Could not fetch inbound {pc.inbound_id} on server {pc.server_id}")
                summary[pc.server_id] = {"wiped": 0, "preserved": 0, "error": "fetch_failed"}
                continue

            settings = inbound.get("settings", {})
            if isinstance(settings, str):
                settings = json.loads(settings)

            clients = settings.get("clients", [])
            bot_clients = [c for c in clients if _is_bot_email(c.get("email", ""))]
            preserved = [c for c in clients if not _is_bot_email(c.get("email", ""))]

            print(f"  Server {pc.server_id} ({pc.label}) inbound {pc.inbound_id}: "
                  f"total={len(clients)} bot={len(bot_clients)} preserved={len(preserved)}")

            for bc in bot_clients:
                print(f"    - WIPE: {bc.get('email', '?')} uuid={bc.get('id', '?')[:8]}...")
            for pc2 in preserved:
                print(f"    ✓ KEEP: {pc2.get('email', '?')} uuid={pc2.get('id', '?')[:8]}...")

            if dry_run:
                print(f"  [DRY RUN] Would wipe {len(bot_clients)} clients, keep {len(preserved)}")
                summary[pc.server_id] = {"wiped": len(bot_clients), "preserved": len(preserved), "dry_run": True}
                continue

            # Replace clients with only preserved ones
            settings["clients"] = preserved
            inbound["settings"] = settings
            ok = await pc.update_inbound(inbound)
            if ok:
                print(f"  ✅ Wiped {len(bot_clients)} bot clients, kept {len(preserved)} service clients")
                summary[pc.server_id] = {"wiped": len(bot_clients), "preserved": len(preserved)}
            else:
                print(f"  ❌ Failed to update inbound on server {pc.server_id}")
                summary[pc.server_id] = {"wiped": 0, "preserved": len(preserved), "error": "update_failed"}

    return summary


async def phase_rebuild(users: list[dict], panels: list[PanelClient],
                        dry_run: bool = False) -> dict:
    """Re-add all active users to all panels."""
    print("\n" + "=" * 60)
    print(f"PHASE: REBUILD ({len(users)} users × {len(panels)} servers)")
    print("=" * 60)

    if dry_run:
        print("  [DRY RUN] Would add the following users to each panel:")
        for u in users:
            print(f"    user={u['internal_user_id'][:16]} "
                  f"devices={u['device_count']} until={u['active_until_utc']}")
        return {"dry_run": True, "users": len(users), "panels": len(panels)}

    # Group panels by URL (sequential per-panel, parallel across panels)
    by_panel: OrderedDict[str, list[PanelClient]] = OrderedDict()
    for p in panels:
        by_panel.setdefault(p.base, []).append(p)

    total_added = 0
    total_failed = 0
    per_server_stats: dict[int, dict] = {p.server_id: {"added": 0, "failed": 0} for p in panels}

    async def _rebuild_panel(panel_clients: list[PanelClient]) -> None:
        nonlocal total_added, total_failed
        for pc in panel_clients:
            print(f"\n  Server {pc.server_id} ({pc.label}) inbound {pc.inbound_id} [{pc.transport_type}]:")
            for u in users:
                uid = u["internal_user_id"]
                uuid = _vless_uuid_for_transport(uid, pc.transport_type)
                email = _email_from_internal(uid, transport_type=pc.transport_type)
                expiry_ts = _expiry_from_datetime(u["active_until_utc"])
                limit_ip = u["device_count"] if u["device_count"] > 0 else _TRIAL_DEVICE_LIMIT

                ok = await pc.add_client(
                    user_uuid=uuid,
                    email=email,
                    expiry_ts=expiry_ts,
                    enable=True,
                    limit_ip=limit_ip,
                )
                if ok:
                    total_added += 1
                    per_server_stats[pc.server_id]["added"] += 1
                else:
                    total_failed += 1
                    per_server_stats[pc.server_id]["failed"] += 1
                    print(f"    ❌ FAILED: user={uid[:16]} uuid={uuid[:8]}...")

            stats = per_server_stats[pc.server_id]
            print(f"  → Server {pc.server_id} done: {stats['added']} added, {stats['failed']} failed")

    # Run panels in parallel (sequential within each panel)
    await asyncio.gather(*[_rebuild_panel(pcs) for pcs in by_panel.values()])

    print(f"\n  TOTAL: {total_added} added, {total_failed} failed "
          f"({len(users)} users × {len(panels)} servers = {len(users) * len(panels)} expected)")

    return {
        "added": total_added,
        "failed": total_failed,
        "users": len(users),
        "panels": len(panels),
        "per_server": per_server_stats,
    }


async def phase_verify(users: list[dict], panels: list[PanelClient]) -> dict:
    """Verify all users exist on all panels."""
    print("\n" + "=" * 60)
    print(f"PHASE: VERIFY ({len(users)} users × {len(panels)} servers)")
    print("=" * 60)

    total_ok = 0
    total_missing = 0
    per_server: dict[int, dict] = {p.server_id: {"ok": 0, "missing": 0, "uuid_mismatch": 0} for p in panels}

    for u in users:
        uid = u["internal_user_id"]

        for pc in panels:
            email = _email_from_internal(uid, transport_type=pc.transport_type)
            # Expected uuid is DERIVED per (user, transport), not the stored
            # user_identities.vless_uuid (NULL since PR #320).
            expected_uuid = _vless_uuid_for_transport(uid, pc.transport_type)
            found_uuid = await pc.resolve_client_uuid(email=email)
            if found_uuid is None:
                total_missing += 1
                per_server[pc.server_id]["missing"] += 1
            elif found_uuid != expected_uuid:
                total_missing += 1
                per_server[pc.server_id]["uuid_mismatch"] += 1
                print(f"  ⚠️  UUID MISMATCH: server={pc.server_id} user={uid[:16]} "
                      f"expected={expected_uuid[:8]}... got={found_uuid[:8]}...")
            else:
                total_ok += 1
                per_server[pc.server_id]["ok"] += 1

    # Per-server summary
    for pc in panels:
        s = per_server[pc.server_id]
        print(f"  Server {pc.server_id} ({pc.label}): ok={s['ok']} missing={s['missing']} mismatch={s['uuid_mismatch']}")

    print(f"\n  TOTAL: {total_ok} OK, {total_missing} MISSING "
          f"({len(users)} users × {len(panels)} servers = {len(users) * len(panels)} expected)")

    return {"ok": total_ok, "missing": total_missing, "per_server": per_server}


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def phase_stats(pool: asyncpg.Pool) -> None:
    """Print user statistics from DB."""
    print("\n" + "=" * 60)
    print("USER STATISTICS")
    print("=" * 60)

    total = await pool.fetchval("SELECT COUNT(*) FROM user_identities")
    print(f"  Всего пользователей: {total}")

    with_uuid = await pool.fetchval("SELECT COUNT(*) FROM user_identities WHERE vless_uuid IS NOT NULL")
    print(f"  С VLESS UUID: {with_uuid}")

    states = await pool.fetch("""
        SELECT s.state_label, COUNT(*) as cnt, COUNT(i.vless_uuid) as with_uuid
        FROM subscription_snapshots s
        LEFT JOIN user_identities i ON i.internal_user_id = s.internal_user_id
        GROUP BY s.state_label ORDER BY cnt DESC
    """)
    print("\n  Состояния подписок:")
    for r in states:
        print(f"    {r['state_label']}: {r['cnt']} пользователей (с UUID: {r['with_uuid']})")

    expired_active = await pool.fetchval(
        "SELECT COUNT(*) FROM subscription_snapshots WHERE active_until_utc < NOW() AND state_label = 'active'"
    )
    print(f"\n  Просроченных, но state_label=active: {expired_active}")

    no_snap = await pool.fetchval("""
        SELECT COUNT(*) FROM user_identities i
        WHERE NOT EXISTS (SELECT 1 FROM subscription_snapshots s WHERE s.internal_user_id = i.internal_user_id)
    """)
    print(f"  Без подписки вообще: {no_snap}")

    inactive_with_uuid = await pool.fetchval("""
        SELECT COUNT(*) FROM user_identities i
        JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
        WHERE s.state_label != 'active' AND i.vless_uuid IS NOT NULL
    """)
    print(f"  Неактивных с UUID (ключи висят на панелях): {inactive_with_uuid}")

    # Expiry dates of active users
    rows = await pool.fetch("""
        SELECT active_until_utc::date as d, COUNT(*) as cnt
        FROM subscription_snapshots WHERE state_label = 'active'
        GROUP BY d ORDER BY d
    """)
    print("\n  Даты истечения активных подписок:")
    now_date = datetime.now(UTC).date()
    for r in rows:
        marker = " ⚠️ EXPIRED" if r["d"] < now_date else ""
        print(f"    {r['d']}: {r['cnt']} пользователей{marker}")


async def phase_extend_expired(pool: asyncpg.Pool, days: int = 30, dry_run: bool = False) -> int:
    """Extend expired subscriptions by N days. Returns count of extended users."""
    print("\n" + "=" * 60)
    print(f"PHASE: EXTEND EXPIRED (+{days} days)")
    print("=" * 60)

    rows = await pool.fetch(
        """SELECT i.internal_user_id, s.active_until_utc, s.state_label
           FROM user_identities i
           JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
           WHERE s.state_label != 'active' AND i.vless_uuid IS NOT NULL
           ORDER BY i.internal_user_id"""
    )

    if not rows:
        print("  No expired/inactive users found.")
        return 0

    new_until = datetime.now(UTC) + timedelta(days=days)
    print(f"  Found {len(rows)} expired/inactive users, extending to {new_until.date()}")

    for r in rows:
        print(f"    {r['internal_user_id'][:20]} state={r['state_label']} "
              f"old_until={r['active_until_utc']} → new_until={new_until.date()}")

    if dry_run:
        print(f"  [DRY RUN] Would extend {len(rows)} users")
        return len(rows)

    result = await pool.execute(
        """UPDATE subscription_snapshots
           SET state_label = 'active',
               active_until_utc = $1,
               keys_deactivated_at = NULL,
               keys_deleted_at = NULL,
               updated_at = NOW()
           WHERE internal_user_id IN (
               SELECT i.internal_user_id FROM user_identities i
               JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id
               WHERE s.state_label != 'active' AND i.vless_uuid IS NOT NULL
           )""",
        new_until,
    )
    print(f"  ✅ Extended {result} — now state_label='active', until={new_until.date()}")
    return len(rows)


async def main() -> None:
    parser = argparse.ArgumentParser(description="Wipe and rebuild panel clients from DB")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen without making changes")
    parser.add_argument("--verify", action="store_true", help="Only verify current state")
    parser.add_argument("--stats", action="store_true", help="Print user statistics only")
    parser.add_argument("--extend-expired", type=int, default=0, metavar="DAYS",
                        help="Extend expired subscriptions by N days, then rebuild")
    args = parser.parse_args()

    dry_run = args.dry_run
    verify_only = args.verify
    stats_only = args.stats
    extend_days = args.extend_expired

    if extend_days > 0:
        mode = f"EXTEND EXPIRED +{extend_days}d then REBUILD"
    elif stats_only:
        mode = "STATS"
    elif dry_run:
        mode = "DRY RUN"
    elif verify_only:
        mode = "VERIFY ONLY"
    else:
        mode = "FULL REBUILD"

    print("=" * 60)
    print("REBUILD PANEL CLIENTS")
    print(f"Mode: {mode}")
    print(f"Time: {datetime.now(UTC).isoformat()}")
    print("=" * 60)

    # Connect to DB
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("ERROR: DATABASE_URL env var not set")
        sys.exit(1)

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=3)
    try:
        # Always print stats
        await phase_stats(pool)

        if stats_only:
            await pool.close()
            return

        # Extend expired users if requested
        if extend_days > 0:
            await phase_extend_expired(pool, days=extend_days, dry_run=dry_run)
            # Print updated stats
            await phase_stats(pool)

        # Phase 1: Load data
        print("\n--- Loading data from DB ---")
        users = await _load_active_users(pool)
        server_configs = await _load_server_configs(pool)
        print(f"  Active users: {len(users)}")
        print(f"  Active servers: {len(server_configs)}")

        if not users:
            print("No active users found. Nothing to do.")
            return

        if not server_configs:
            print("No active servers found. Nothing to do.")
            return

        # Print user summary
        for u in users[:5]:
            print(f"  user={u['internal_user_id'][:16]}... uuid={u['vless_uuid'][:8]}... "
                  f"devices={u['device_count']} until={u['active_until_utc']}")
        if len(users) > 5:
            print(f"  ... and {len(users) - 5} more")

        # Print server summary
        for s in server_configs:
            print(f"  server={s['server_id']} {s['label']} {s['server_host']}:{s['server_port']} "
                  f"inbound={s['inbound_id']} type={s['transport_type']}")

        # Create panel clients
        panels: list[PanelClient] = []
        for s in server_configs:
            panels.append(PanelClient(
                panel_url=s["panel_url"],
                username=s["panel_username"],
                password=s["panel_password"],
                inbound_id=s["inbound_id"],
                server_id=s["server_id"],
                label=s["label"],
                transport_type=s["transport_type"],
                server_host=s["server_host"],
                server_port=s["server_port"],
            ))

        # Login test
        print("\n--- Testing panel logins ---")
        for p in panels:
            ok = await p._login()
            status = "✅" if ok else "❌"
            print(f"  {status} Server {p.server_id} ({p.label}): {p.base}")

        failed_logins = [p for p in panels if p._last_login_ts == 0.0]
        if failed_logins:
            print(f"\n⚠️  {len(failed_logins)} panels failed login. Aborting.")
            for p in panels:
                await p.aclose()
            await pool.close()
            sys.exit(1)

        # Verify mode
        if verify_only:
            result = await phase_verify(users, panels)
            for p in panels:
                await p.aclose()
            await pool.close()
            sys.exit(0 if result["missing"] == 0 else 1)

        # Phase 2: Wipe
        wipe_result = await phase_wipe(panels, dry_run=dry_run)

        # Phase 3: Rebuild
        rebuild_result = await phase_rebuild(users, panels, dry_run=dry_run)

        # Phase 4: Verify (only if not dry run)
        if not dry_run:
            verify_result = await phase_verify(users, panels)
            if verify_result["missing"] > 0:
                print(f"\n⚠️  VERIFICATION FAILED: {verify_result['missing']} clients missing!")
                for p in panels:
                    await p.aclose()
                await pool.close()
                sys.exit(1)

        # Final summary
        print("\n" + "=" * 60)
        if dry_run:
            print("DRY RUN COMPLETE — no changes were made")
        else:
            print("REBUILD COMPLETE")
        print(f"  Users: {len(users)}")
        print(f"  Servers: {len(panels)}")
        print(f"  Expected client entries: {len(users) * len(panels)}")
        if not dry_run and "added" in rebuild_result:
            print(f"  Added: {rebuild_result['added']}")
            print(f"  Failed: {rebuild_result['failed']}")
        print("=" * 60)

        for p in panels:
            await p.aclose()
    finally:
        await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
