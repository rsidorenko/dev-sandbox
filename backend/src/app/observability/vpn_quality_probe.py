"""Изолированный измеритель: стандартная библиотека, секреты только в памяти/stdin."""

import ipaddress
import json
import math
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

RESULTS = frozenset(
    {
        "ok",
        "route_unknown",
        "route_mismatch",
        "timeout",
        "tls_error",
        "connect_error",
        "http_error",
        "invalid_response",
        "measurement_error",
    }
)
TARGET = "https://www.cloudflare.com/cdn-cgi/trace"


def outbound_from_link(link):
    """Поддерживаем только проверяемые TCP/XHTTP с TLS/Reality."""
    url = urlsplit(link)
    q = {k: v[0] for k, v in parse_qs(url.query).items()}
    if url.scheme != "vless" or not url.hostname or not url.port:
        raise ValueError("unsupported_profile")
    user = {"id": str(UUID(url.username)), "encryption": q.get("encryption", "none")}
    if q.get("flow"):
        user["flow"] = q["flow"]
    network, security = q.get("type", "tcp"), q.get("security")
    if network not in {"tcp", "raw", "xhttp"} or security not in {"tls", "reality"}:
        raise ValueError("unsupported_profile")
    stream = {"network": network, "security": security}
    if security == "reality":
        if not q.get("pbk") or not q.get("sni"):
            raise ValueError("unsupported_profile")
        stream["realitySettings"] = {
            "serverName": q["sni"],
            "publicKey": q["pbk"],
            "shortId": q.get("sid", ""),
            "fingerprint": q.get("fp", "chrome"),
            "spiderX": q.get("spx", "/"),
        }
    else:
        stream["tlsSettings"] = {
            "serverName": q.get("sni", url.hostname),
            "fingerprint": q.get("fp", "chrome"),
            "allowInsecure": False,
        }
    if network == "xhttp":
        x = {"path": q.get("path", "/"), "host": q.get("host", ""), "mode": q.get("mode", "auto")}
        if q.get("extra"):
            x["extra"] = json.loads(q["extra"])
        stream["xhttpSettings"] = x
    return {
        "protocol": "vless",
        "settings": {"vnext": [{"address": url.hostname, "port": url.port, "users": [user]}]},
        "streamSettings": stream,
    }


def interpret(rc, output, expected_ips):
    """Тело, адрес и сообщения curl не входят в результат."""
    result = {
        "result": "measurement_error",
        "http_code": None,
        "tls_ms": None,
        "ttfb_ms": None,
        "total_ms": None,
        "response_bytes": None,
    }
    try:
        body, raw = output.rsplit("\n", 1)
        data = json.loads(raw)
        result["http_code"] = int(data["http_code"])
        result["response_bytes"] = int(data["size_download"])
        if not 0 <= result["http_code"] <= 599 or not 0 <= result["response_bytes"] <= 4096:
            raise ValueError("invalid_metrics")
        for source, target in [
            ("time_appconnect", "tls_ms"),
            ("time_starttransfer", "ttfb_ms"),
            ("time_total", "total_ms"),
        ]:
            value = float(data[source]) * 1000
            if not math.isfinite(value) or not 0 <= value <= 15000:
                raise ValueError("invalid_metrics")
            result[target] = round(value, 3)
    except (ValueError, TypeError, KeyError):
        return {**dict.fromkeys(result), "result": "measurement_error"}
    if rc:
        result["result"] = "timeout" if rc == 28 else "tls_error" if rc in {35, 51, 60} else "connect_error"
    elif result["http_code"] != 200:
        result["result"] = "http_error"
    else:
        try:
            ip = next(line[3:].strip() for line in body.splitlines() if line.startswith("ip="))
            ip = str(ipaddress.ip_address(ip))
            expected = {str(ipaddress.ip_address(v)) for v in expected_ips}
            result["result"] = "route_unknown" if not expected else "ok" if ip in expected else "route_mismatch"
        except (ValueError, StopIteration):
            result["result"] = "invalid_response"
    return result


def terminate(proc):
    """Завершаем только созданную нами группу процессов измерителя."""
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait(timeout=2)


def probe(item):
    if not item.get("outbound"):
        return {"result": "measurement_error"}
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    config = {
        "log": {"loglevel": "none"},
        "inbounds": [{"listen": "127.0.0.1", "port": port, "protocol": "socks", "settings": {"auth": "noauth"}}],
        "outbounds": [item["outbound"]],
    }
    # Независимый timeout ограничивает жизнь Xray даже после SIGKILL/обрыва SSH worker.
    proc = subprocess.Popen(  # noqa: S603 — фиксированный бинарник, credentials только через stdin.
        ["/usr/bin/timeout", "-k", "2", "20", "/usr/local/bin/xray", "run", "-config", "stdin:"],
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
    )
    try:
        proc.stdin.write(json.dumps(config).encode())
        proc.stdin.close()
        for _ in range(20):
            if proc.poll() is not None:
                return {"result": "measurement_error"}
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.05)
        else:
            return {"result": "measurement_error"}
        completed = subprocess.run(  # noqa: S603 — фиксированный HTTPS URL и локальный SOCKS.
            [
                "/usr/bin/curl",
                "-q",
                "-sS",
                "--http1.1",
                "--noproxy",
                "",
                "--max-time",
                "10",
                "--connect-timeout",
                "8",
                "--max-filesize",
                "4096",
                "--socks5-hostname",
                f"127.0.0.1:{port}",
                "-w",
                "\n%{json}",
                TARGET,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=12,
            env={"PATH": "/usr/local/bin:/usr/bin:/bin", "LANG": "C.UTF-8"},
        )
        return interpret(completed.returncode, completed.stdout, item["expected_ips"])
    except (OSError, subprocess.SubprocessError):
        return {"result": "measurement_error"}
    finally:
        terminate(proc)


class MeasurementDeadline(Exception):
    """Общий дедлайн не должен превращаться в ошибку только одной пробы."""


def deadline(*_):
    raise MeasurementDeadline("measurement_deadline")


def main():
    signal.signal(signal.SIGALRM, deadline)
    signal.signal(signal.SIGTERM, deadline)
    signal.signal(signal.SIGINT, deadline)
    signal.alarm(180)
    try:
        items = json.loads(sys.stdin.read(524289))
        if not isinstance(items, list) or not 1 <= len(items) <= 32:
            raise ValueError("invalid_profiles")
        if not shutil.which("timeout") or not shutil.which("curl"):
            raise ValueError("missing_dependency")
        for item in items:
            result = probe(item)
            print(json.dumps({"server_id": item["server_id"], "route": item["route"], **result}), flush=True)
    except (Exception, KeyboardInterrupt):
        print(json.dumps({"measurement_error": True}), flush=True)
        return 1
    finally:
        signal.alarm(0)
    return 0


if __name__ == "__main__":
    sys.exit(main())
