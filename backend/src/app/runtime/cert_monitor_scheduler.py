"""Фоновый мониторинг сроков TLS-сертификатов client-facing хостов.

Причина появления: инцидент 2026-08-20 — сертификат bravada-connect.ru истёк и держал
сайт + бота вниз ~26 ч (cert split-brain, см. docs/ssl_autorenew_runbook.md). Монитор
раз в сутки проверяет сроки всех client-facing сертификатов (main-сайт, egress-релеи,
CDN-origins, camo-серты entry-серверов) и присылает оператору алерт при подходе к
истечению. Состояние пишется в certificate_expiry_state (миграция 078) — только
UPSERT, без удалений.

Два режима проверки:
- ``tls``    — TLS-хендшейк на (host, port) с SNI, notAfter из DER-сертификата пира
               (подходит и для self-signed: валидация цепочки отключена намеренно —
               нам нужна только дата);
- ``ssh-file`` — openssl по SSH на боксе (camo-серты entry слушают 127.0.0.1:10443
               и снаружи не видны).

Формат класса повторяет NotificationScheduler: run()/stop()/interruptible _sleep,
advisory-lock на тик (два рантайма — polling и webhook — не дублируют проверки).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import socket
import ssl
from dataclasses import dataclass
from datetime import UTC, datetime

import asyncpg
import httpx
from cryptography import x509

_LOGGER = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = int(os.environ.get("CERT_MONITOR_INTERVAL_SECONDS", "86400"))
_ADVISED_LOCK_ID = 20260902

# Пороги алертов (дней до истечения). Алерт отправляется при пересечении порога
# вниз; повторные алерты в том же бакете не дублируются. Продление (days_left > 14)
# сбрасывает бакет в NULL.
ALERT_BUCKETS: tuple[int, ...] = (14, 7, 3, 1)

_OPENSSL_DATE_FORMAT = "%b %d %H:%M:%S %Y GMT"
_TLS_TIMEOUT_SECONDS = 10.0
_SSH_TIMEOUT_SECONDS = 15


def cert_monitor_enabled_from_env(env_getter=os.environ.get) -> bool:
    """Kill-switch монитора: включён ТОЛЬКО явным truthy значением (default OFF)."""
    raw = (env_getter("CERT_MONITOR_ENABLE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


def operator_alert_chat_id(env_getter=os.environ.get) -> int | None:
    raw = (env_getter("OPERATOR_ALERT_CHAT_ID") or "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        _LOGGER.warning("cert_monitor: OPERATOR_ALERT_CHAT_ID не является числом: %r", raw)
        return None


@dataclass(frozen=True)
class CertTarget:
    """Один проверяемый сертификат.

    mode='tls': host+port+SNI (пустой SNI = host). mode='ssh-file': ssh_user/ssh_key
    (пустые = CLOUDRU_SSH_USER/CLOUDRU_SSH_KEY из env, как у reconcile-таймера) и
    cert_paths — кандидаты, первый читаемый выигрывает.
    """

    name: str
    mode: str  # 'tls' | 'ssh-file'
    host: str
    port: int = 443
    sni: str = ""
    cert_paths: tuple[str, ...] = ()
    ssh_user: str = ""
    ssh_key: str = ""


def _default_ssh_user() -> str:
    return os.environ.get("CLOUDRU_SSH_USER", "romtan")


def _default_ssh_key() -> str:
    return os.environ.get("CLOUDRU_SSH_KEY", "D:/tmp/cloudru_key.pem")


# Реестр проверяемых сертификатов. tls-цели видимы снаружи; camo-серты entry-серверов
# (127.0.0.1:10443) — только с бокса, поэтому ssh-file со стандартным letsencrypt-путём.
_CERTBOT_LIVE = "/etc/letsencrypt/live"
CERT_TARGETS: tuple[CertTarget, ...] = (
    # main-сайт + webhook (certbot в compose, том certs — см. ssl_autorenew_runbook.md)
    CertTarget(name="main-site", mode="tls", host="bravada-connect.ru", port=443),
    # egress-релеи, терминирующие реальный TLS (SNI обязан совпадать с CN — урок MR !72/!75)
    CertTarget(
        name="egress-frankfurt",
        mode="tls",
        host="77.110.100.210",
        port=10444,
        sni="mgg.bravada-connect.online",
    ),
    CertTarget(
        name="egress-lithuania",
        mode="tls",
        host="2.59.162.163",
        port=443,
        sni="relay.strimingset.online",
    ),
    # Helsinki egress (bare_xray с 2026-09-07): :443/:8443 — reality (SNI mrr.*,
    # серт отдаёт nginx-camo 127.0.0.1:10443), реальный LE-серт на боксе — ssh-file.
    # root-доступ ключом бота (установлен при конвертации), certbot renew + deploy-hook
    # reload nginx (см. /etc/letsencrypt/renewal-hooks/deploy/ на боксе).
    CertTarget(
        name="egress-helsinki",
        mode="ssh-file",
        host="45.137.60.80",
        ssh_user="root",
        cert_paths=(f"{_CERTBOT_LIVE}/mrr.bravada-connect.online/fullchain.pem",),
    ),
    # US egress (bare_xray с 2026-09-07): тот же паттерн — reality заимствует серт
    # с camo 127.0.0.1:10443, LE-серт certbot/nginx, deploy-hook reload nginx установлен.
    CertTarget(
        name="egress-us",
        mode="ssh-file",
        host="77.109.125.209",
        ssh_user="root",
        cert_paths=(f"{_CERTBOT_LIVE}/nkk.bravada-connect.online/fullchain.pem",),
    ),
    # CDN-origins (ноги обхода белых списков): LT — LE до 2026-11-26, DE — self-signed 10 лет
    CertTarget(name="cdn-lt-origin", mode="tls", host="89.169.178.45", port=443),
    CertTarget(name="cdn-de-origin", mode="tls", host="93.77.160.88", port=443),
    # camo-серты entry-серверов (certbot webroot; YT — acme.sh DNS-01, второй кандидат)
    CertTarget(
        name="entry-de-camo",
        mode="ssh-file",
        host="85.208.87.57",
        cert_paths=(f"{_CERTBOT_LIVE}/cloudru.bravada-connect.online/fullchain.pem",),
    ),
    CertTarget(
        name="entry-fi-camo",
        mode="ssh-file",
        host="85.208.85.117",
        cert_paths=(f"{_CERTBOT_LIVE}/clr.mediagenering.online/fullchain.pem",),
    ),
    CertTarget(
        name="entry-lt-camo",
        mode="ssh-file",
        host="45.91.52.204",
        cert_paths=(f"{_CERTBOT_LIVE}/yar.mediagenering.online/fullchain.pem",),
    ),
    CertTarget(
        name="entry-us-camo",
        mode="ssh-file",
        host="176.109.107.226",
        cert_paths=(f"{_CERTBOT_LIVE}/uss.mediagenering.online/fullchain.pem",),
    ),
    CertTarget(
        name="entry-yt-camo",
        mode="ssh-file",
        host="93.77.165.48",
        cert_paths=(
            f"{_CERTBOT_LIVE}/yt.mediagenering.online/fullchain.pem",
            "/root/.acme.sh/yt.mediagenering.online_ecc/fullchain.cer",
        ),
    ),
)


def alert_bucket(days_left: int | None) -> int | None:
    """Самый срочный пересечённый порог (алертим при входе в новый бакет).

    days=10 → 14, days=6 → 7, days=2 → 3, days<=1 → 1 (включая «уже истёк»).
    """
    if days_left is None:
        return None
    if days_left < 0:  # уже истёк — максимально срочный бакет
        days_left = 0
    for bucket in sorted(ALERT_BUCKETS):  # от срочного к старшему: первый пересечённый
        if days_left <= bucket:
            return bucket
    return None


def parse_openssl_not_after(stdout: str) -> datetime | None:
    """Парсит вывод ``openssl x509 -noout -enddate``: ``notAfter=Sep  7 12:00:00 2026 GMT``.

    День с ведущим нулём/двойным пробелом нормализуется схлопыванием пробелов.
    Возвращает наивный-UTC datetime ( openssl всегда печатает GMT).
    """
    for line in stdout.splitlines():
        line = line.strip()
        if not line.lower().startswith("notafter="):
            continue
        raw = line.split("=", 1)[1].strip()
        normalized = " ".join(raw.split())
        with contextlib.suppress(ValueError):
            return datetime.strptime(normalized, _OPENSSL_DATE_FORMAT).replace(tzinfo=UTC)
    return None


def days_left_from(not_after: datetime, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    return int((not_after - now).total_seconds() // 86400)


def probe_tls_not_after(host: str, port: int, sni: str = "") -> datetime | None:
    """notAfter сертификата пира по TLS-хендшейку. Валидация выключена — нужна только дата."""
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    with socket.create_connection((host, port), timeout=_TLS_TIMEOUT_SECONDS) as sock:
        with ctx.wrap_socket(sock, server_hostname=sni or host) as tls:
            der = tls.getpeercert(binary_form=True)
    if not der:
        return None
    return x509.load_der_x509_certificate(der).not_valid_after_utc


def _ssh_exec(host: str, user: str, key: str, command: str) -> tuple[int, str]:
    import paramiko  # lazy — модуль импортируется только при ssh-file целях

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())  # noqa: S507
    client.connect(
        hostname=host,
        username=user,
        key_filename=key,
        look_for_keys=False,
        allow_agent=False,
        timeout=_SSH_TIMEOUT_SECONDS,
        # как в XrayApiClient: TCP-timeout не покрывает banner/auth фазы handshake
        banner_timeout=_SSH_TIMEOUT_SECONDS,
        auth_timeout=_SSH_TIMEOUT_SECONDS,
    )
    try:
        _, stdout, _ = client.exec_command(command, timeout=_SSH_TIMEOUT_SECONDS)
        rc = stdout.channel.recv_exit_status()
        return rc, stdout.read().decode(errors="replace")
    finally:
        client.close()


def probe_ssh_cert_not_after(target: CertTarget) -> datetime | None:
    """notAfter camo-серта на боксе: первый читаемый кандидат из cert_paths."""
    checks = " ; ".join(
        f'sudo test -r "{path}" && sudo openssl x509 -noout -enddate -in "{path}"' for path in (target.cert_paths or ())
    )
    if not checks:
        return None
    rc, out = _ssh_exec(
        host=target.host,
        user=target.ssh_user or _default_ssh_user(),
        key=target.ssh_key or _default_ssh_key(),
        command=f"( {checks} ) | head -n 1",
    )
    if rc != 0:
        return None
    return parse_openssl_not_after(out)


def build_alert_text(target: CertTarget, days_left: int, not_after: datetime) -> str:
    return (
        f"🔒 Сертификат <b>{target.name}</b> истекает через <b>{days_left} дн.</b> "
        f"({not_after:%Y-%m-%d}) — {target.host}:{target.port}"
    )


class CertMonitorScheduler:
    """Ежедневная проверка сроков сертификатов + алерты оператору."""

    def __init__(self, *, pool: asyncpg.Pool, bot_token: str) -> None:
        self._pool = pool
        self._bot_token = bot_token
        self._running = False
        self._http_client: httpx.AsyncClient | None = None

    async def run(self) -> None:
        self._running = True
        while self._running:
            try:
                await self._run_checks()
            except Exception:
                _LOGGER.exception("cert_monitor_check_error")
            await self._sleep(_CHECK_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    async def aclose(self) -> None:
        if self._http_client is not None:
            with contextlib.suppress(Exception):
                await self._http_client.aclose()
            self._http_client = None

    async def _sleep(self, seconds: int) -> None:
        for _ in range(seconds):
            if not self._running:
                return
            await asyncio.sleep(1)

    async def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(timeout=15.0)
        return self._http_client

    async def _run_checks(self) -> None:
        async with self._pool.acquire() as conn:
            locked = await conn.fetchval("SELECT pg_try_advisory_lock($1)", _ADVISED_LOCK_ID)
            if not locked:
                _LOGGER.info("cert_monitor tick skipped — another instance holds the lock")
                return
            try:
                for target in CERT_TARGETS:
                    with contextlib.suppress(Exception):
                        await self._check_target(conn, target)
            finally:
                await conn.execute("SELECT pg_advisory_unlock($1)", _ADVISED_LOCK_ID)

    async def _check_target(self, conn: asyncpg.Connection, target: CertTarget) -> None:
        try:
            if target.mode == "tls":
                not_after = await asyncio.to_thread(probe_tls_not_after, target.host, target.port, target.sni)
            else:
                not_after = await asyncio.to_thread(probe_ssh_cert_not_after, target)
        except Exception as exc:
            _LOGGER.warning("cert_monitor probe failed target=%s host=%s: %r", target.name, target.host, exc)
            await self._persist_probe_failed(conn, target)
            return

        if not_after is None:
            _LOGGER.warning("cert_monitor probe returned no cert target=%s", target.name)
            await self._persist_probe_failed(conn, target)
            return

        now = datetime.now(UTC)
        days = days_left_from(not_after, now)
        bucket = alert_bucket(days)
        prev = await conn.fetchrow(
            "SELECT days_left, last_alert_bucket FROM certificate_expiry_state WHERE name = $1",
            target.name,
        )
        prev_bucket = prev["last_alert_bucket"] if prev else None

        new_bucket = prev_bucket
        if bucket is not None and bucket != prev_bucket:
            sent = await self._send_alert(target, days, not_after)
            if sent:
                new_bucket = bucket
        if days > ALERT_BUCKETS[0]:
            # Продлено/свежий серт — сбрасываем бакет (UPDATE, не DELETE).
            new_bucket = None

        await conn.execute(
            """
            INSERT INTO certificate_expiry_state
                (name, mode, not_after, days_left, checked_at, last_alert_bucket)
            VALUES ($1, $2, $3, $4, NOW(), $5)
            ON CONFLICT (name) DO UPDATE SET
                mode = EXCLUDED.mode,
                not_after = EXCLUDED.not_after,
                days_left = EXCLUDED.days_left,
                checked_at = NOW(),
                last_alert_bucket = EXCLUDED.last_alert_bucket
            """,
            target.name,
            target.mode,
            not_after,
            days,
            new_bucket,
        )
        _LOGGER.info("cert_monitor ok target=%s days_left=%s bucket=%s", target.name, days, new_bucket)

    async def _persist_probe_failed(self, conn: asyncpg.Connection, target: CertTarget) -> None:
        """Probe не удался: обновляем только checked_at, старые значения не трогаем."""
        await conn.execute(
            """
            INSERT INTO certificate_expiry_state (name, mode, checked_at)
            VALUES ($1, $2, NOW())
            ON CONFLICT (name) DO UPDATE SET mode = EXCLUDED.mode, checked_at = NOW()
            """,
            target.name,
            target.mode,
        )

    async def _send_alert(self, target: CertTarget, days_left: int, not_after: datetime) -> bool:
        chat_id = operator_alert_chat_id()
        text = build_alert_text(target, days_left, not_after)
        if chat_id is None:
            _LOGGER.warning(
                "cert_monitor: OPERATOR_ALERT_CHAT_ID не задан — алерт не отправлен: %s",
                text,
            )
            return False
        if not self._bot_token:
            _LOGGER.warning("cert_monitor: bot token пуст — алерт не отправлен: %s", text)
            return False
        try:
            client = await self._get_http_client()
            resp = await client.post(
                f"https://api.telegram.org/bot{self._bot_token}/sendMessage",
                json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            )
            if resp.status_code == 200:
                return True
            _LOGGER.warning("cert_monitor alert send failed target=%s status=%s", target.name, resp.status_code)
            return False
        except Exception:
            _LOGGER.exception("cert_monitor alert send error target=%s", target.name)
            return False


def start_cert_monitor_scheduler(*, pool: asyncpg.Pool, bot_token: str) -> CertMonitorScheduler:
    return CertMonitorScheduler(pool=pool, bot_token=bot_token)


__all__ = [
    "ALERT_BUCKETS",
    "CERT_TARGETS",
    "CertMonitorScheduler",
    "CertTarget",
    "alert_bucket",
    "build_alert_text",
    "cert_monitor_enabled_from_env",
    "days_left_from",
    "operator_alert_chat_id",
    "parse_openssl_not_after",
    "probe_ssh_cert_not_after",
    "probe_tls_not_after",
    "start_cert_monitor_scheduler",
]
