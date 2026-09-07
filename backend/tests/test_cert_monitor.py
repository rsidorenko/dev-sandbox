"""Unit tests для cert-монитора: бакеты алертов, парсинг дат, persist-логика, TLS-проба.

Сеть не нужна (кроме loopback TLS-сервера в тесте пробы): probe-функции патчатся,
persist-логика проверяется на фейковом соединении, самоподписанный сертификат
генерируется в тесте через cryptography.
"""

from __future__ import annotations

import datetime as dt
import socket
import ssl
import threading
from datetime import UTC, datetime, timedelta

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.runtime import cert_monitor_scheduler as cm
from app.shared.test_helpers import run_async as _run

# ---------------------------------------------------------------------------
# Чистые функции: бакеты, парсинг, даты
# ---------------------------------------------------------------------------


def test_alert_bucket_transitions() -> None:
    # Семантика «самый срочный ПЕРЕСЕЧЁННЫЙ порог»: эскалация при входе в ≤14/≤7/≤3/≤1.
    assert cm.alert_bucket(None) is None
    assert cm.alert_bucket(90) is None
    assert cm.alert_bucket(15) is None
    assert cm.alert_bucket(14) == 14
    assert cm.alert_bucket(8) == 14  # порог 7 ещё не пересечён
    assert cm.alert_bucket(7) == 7
    assert cm.alert_bucket(6) == 7
    assert cm.alert_bucket(4) == 7  # порог 3 ещё не пересечён
    assert cm.alert_bucket(3) == 3
    assert cm.alert_bucket(2) == 3
    assert cm.alert_bucket(1) == 1
    assert cm.alert_bucket(0) == 1
    assert cm.alert_bucket(-5) == 1  # уже истёк — максимально срочный бакет


def test_parse_openssl_not_after_variants() -> None:
    # Двойной пробел после имени месяца с однозначным днём — нормализуем.
    parsed = cm.parse_openssl_not_after("notAfter=Sep  7 12:00:00 2026 GMT")
    assert parsed == datetime(2026, 9, 7, 12, 0, 0, tzinfo=UTC)
    assert cm.parse_openssl_not_after("notAfter=Sep 17 12:00:00 2026 GMT") == datetime(
        2026, 9, 17, 12, 0, 0, tzinfo=UTC
    )
    assert cm.parse_openssl_not_after("depth=0 CN = x\nverify error: ...") is None
    assert cm.parse_openssl_not_after("") is None


def test_days_left_from_rounds_down() -> None:
    now = datetime(2026, 9, 7, 0, 0, 0, tzinfo=UTC)
    assert cm.days_left_from(now + timedelta(days=13, hours=23), now) == 13
    assert cm.days_left_from(now + timedelta(days=1), now) == 1
    assert cm.days_left_from(now - timedelta(days=2), now) == -2


# ---------------------------------------------------------------------------
# _check_target: persist-логика на фейковом соединении
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, prev_row: dict | None) -> None:
        self._prev_row = prev_row
        self.executed: list[tuple[str, tuple]] = []

    async def fetchrow(self, sql: str, *args):
        return self._prev_row

    async def execute(self, sql: str, *args):
        self.executed.append((sql, args))


class _FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class _FakeHttp:
    def __init__(self, status_code: int) -> None:
        self._status = status_code
        self.posts: list[dict] = []

    async def post(self, url: str, *, json: dict) -> _FakeResponse:
        self.posts.append(json)
        return _FakeResponse(self._status)


def _tls_target() -> cm.CertTarget:
    return cm.CertTarget(name="t-main", mode="tls", host="example.test", port=443)


def _patch_probe(monkeypatch, not_after: datetime | None, *, raises: bool = False) -> None:
    def fake_probe(host: str, port: int, sni: str = "") -> datetime | None:
        if raises:
            raise OSError("probe boom")
        return not_after

    monkeypatch.setattr(cm, "probe_tls_not_after", fake_probe)


def test_check_target_first_alert_persists_bucket(monkeypatch) -> None:
    # +12ч запаса против microseconds-floor при вычислении days_left в шедулере.
    _patch_probe(monkeypatch, datetime.now(UTC) + timedelta(days=10, hours=12))
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")  # pool не используется тут
    monkeypatch.setattr(sched, "_send_alert", lambda *a, **k: _immediate(True))
    conn = _FakeConn(prev_row=None)
    _run(sched._check_target(conn, _tls_target()))
    ((sql, args),) = conn.executed
    assert "INSERT INTO certificate_expiry_state" in sql
    name, mode, not_after, days, bucket = args
    assert (name, mode, days, bucket) == ("t-main", "tls", 10, 14)


def test_check_target_same_bucket_does_not_refire(monkeypatch) -> None:
    _patch_probe(monkeypatch, datetime.now(UTC) + timedelta(days=10, hours=12))
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")
    fired: list[tuple] = []

    async def fake_send(*args, **kwargs) -> bool:
        fired.append(args)
        return True

    monkeypatch.setattr(sched, "_send_alert", fake_send)
    conn = _FakeConn(prev_row={"days_left": 10, "last_alert_bucket": 14})
    _run(sched._check_target(conn, _tls_target()))
    assert fired == []  # тот же бакет — не дублируем
    _, args = conn.executed[0]
    assert args[4] == 14  # бакет сохранён


def test_check_target_bucket_escalation(monkeypatch) -> None:
    # days=6 остаётся 6 и после microseconds-floor.
    _patch_probe(monkeypatch, datetime.now(UTC) + timedelta(days=6, hours=12))
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")
    monkeypatch.setattr(sched, "_send_alert", lambda *a, **k: _immediate(True))
    conn = _FakeConn(prev_row={"days_left": 10, "last_alert_bucket": 14})
    _run(sched._check_target(conn, _tls_target()))
    _, args = conn.executed[0]
    assert args[4] == 7  # 14 → 7 эскалация


def test_check_target_send_failure_keeps_prev_bucket(monkeypatch) -> None:
    _patch_probe(monkeypatch, datetime.now(UTC) + timedelta(days=10))
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")
    monkeypatch.setattr(sched, "_send_alert", lambda *a, **k: _immediate(False))
    conn = _FakeConn(prev_row=None)  # прошлой записи нет → бакет остаётся None
    _run(sched._check_target(conn, _tls_target()))
    _, args = conn.executed[0]
    assert args[4] is None  # отправка не удалась — не персистим бакет, повторим в следующий тик


def test_check_target_renewed_resets_bucket(monkeypatch) -> None:
    _patch_probe(monkeypatch, datetime.now(UTC) + timedelta(days=60))
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")
    monkeypatch.setattr(sched, "_send_alert", lambda *a, **k: _immediate(True))
    conn = _FakeConn(prev_row={"days_left": 6, "last_alert_bucket": 7})
    _run(sched._check_target(conn, _tls_target()))
    _, args = conn.executed[0]
    assert args[4] is None  # продлено → сброс (UPDATE, не DELETE)


def test_check_target_probe_failure_touches_checked_at_only(monkeypatch) -> None:
    _patch_probe(monkeypatch, None, raises=True)
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")
    conn = _FakeConn(prev_row={"days_left": 5, "last_alert_bucket": 7})
    _run(sched._check_target(conn, _tls_target()))
    sql, args = conn.executed[0]
    # Только mode/checked_at — старые not_after/days/bucket не затираем.
    assert "DO UPDATE SET mode = EXCLUDED.mode, checked_at = NOW()" in sql
    assert args == ("t-main", "tls")


def _immediate(value: bool):
    async def _coro(*args, **kwargs) -> bool:
        return value

    return _coro()


# ---------------------------------------------------------------------------
# _send_alert: chat_id из env, HTTP-результат
# ---------------------------------------------------------------------------


def test_send_alert_without_chat_id_returns_false(monkeypatch) -> None:
    monkeypatch.delenv("OPERATOR_ALERT_CHAT_ID", raising=False)
    sched = cm.CertMonitorScheduler(pool=object(), bot_token="t")
    sent = _run(sched._send_alert(_tls_target(), 10, datetime.now(UTC) + timedelta(days=10)))
    assert sent is False


def test_send_alert_http_success_and_failure(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_ALERT_CHAT_ID", "424242")
    target = _tls_target()
    not_after = datetime.now(UTC) + timedelta(days=10)

    ok_sched = cm.CertMonitorScheduler(pool=object(), bot_token="tok")
    ok_http = _FakeHttp(200)
    # instance-атрибут: bound-lookup не передаёт self — lambda без аргументов.
    monkeypatch.setattr(ok_sched, "_get_http_client", lambda: _immediate_client(ok_http))
    assert _run(ok_sched._send_alert(target, 10, not_after)) is True
    assert ok_http.posts[0]["chat_id"] == 424242
    assert "t-main" in ok_http.posts[0]["text"]

    bad_sched = cm.CertMonitorScheduler(pool=object(), bot_token="tok")
    monkeypatch.setattr(bad_sched, "_get_http_client", lambda: _immediate_client(_FakeHttp(500)))
    assert _run(bad_sched._send_alert(target, 10, not_after)) is False


def _immediate_client(client: _FakeHttp):
    async def _coro():
        return client

    return _coro()


def test_env_flags_default_off(monkeypatch) -> None:
    monkeypatch.delenv("CERT_MONITOR_ENABLE", raising=False)
    assert cm.cert_monitor_enabled_from_env() is False
    monkeypatch.setenv("CERT_MONITOR_ENABLE", "1")
    assert cm.cert_monitor_enabled_from_env() is True
    monkeypatch.setenv("CERT_MONITOR_ENABLE", "0")
    assert cm.cert_monitor_enabled_from_env() is False


def test_operator_alert_chat_id_parses(monkeypatch) -> None:
    monkeypatch.delenv("OPERATOR_ALERT_CHAT_ID", raising=False)
    assert cm.operator_alert_chat_id() is None
    monkeypatch.setenv("OPERATOR_ALERT_CHAT_ID", " 424242 ")
    assert cm.operator_alert_chat_id() == 424242
    monkeypatch.setenv("OPERATOR_ALERT_CHAT_ID", "not-a-number")
    assert cm.operator_alert_chat_id() is None


# ---------------------------------------------------------------------------
# probe_tls_not_after: loopback TLS-сервер с самоподписанным сертификатом
# ---------------------------------------------------------------------------


def _generate_self_signed(not_after: datetime) -> tuple[bytes, bytes]:
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(days=1))
        .not_valid_after(not_after)
        .sign(key, hashes.SHA256())
    )
    return (
        cert.public_bytes(serialization.Encoding.PEM),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ),
    )


def test_probe_tls_not_after_real_handshake(tmp_path) -> None:
    not_after = datetime.now(UTC) + timedelta(days=45)
    cert_pem, key_pem = _generate_self_signed(not_after)
    cert_file = tmp_path / "cert.pem"
    key_file = tmp_path / "key.pem"
    cert_file.write_bytes(cert_pem)
    key_file.write_bytes(key_pem)

    server_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_ctx.load_cert_chain(certfile=str(cert_file), keyfile=str(key_file))
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def _serve() -> None:
        try:
            conn, _ = listener.accept()
            with server_ctx.wrap_socket(conn, server_side=True) as tls:
                tls.recv(1)
        except OSError:
            pass

    thread = threading.Thread(target=_serve, daemon=True)
    thread.start()
    try:
        probed = cm.probe_tls_not_after("127.0.0.1", port, sni="localhost")
    finally:
        listener.close()
        thread.join(timeout=5)
    assert probed is not None
    delta = abs(probed - not_after)
    assert delta < dt.timedelta(seconds=5)  # GEN-время vs not_valid_after_utc


def test_registry_contains_bare_xray_egress_helsinki() -> None:
    """Helsinki egress конвертирован в bare_xray (2026-09-07): цель обязана быть
    ssh-file ( reality :443/:8443 серт отдаёт nginx-camo, снаружи не виден ),
    root-доступ ключом бота, LE-путь mrr.bravada-connect.online."""
    target = next(t for t in cm.CERT_TARGETS if t.name == "egress-helsinki")
    assert target.mode == "ssh-file"
    assert target.host == "45.137.60.80"
    assert target.ssh_user == "root"
    assert any("mrr.bravada-connect.online" in p for p in target.cert_paths)
