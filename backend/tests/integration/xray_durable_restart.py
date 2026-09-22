"""Изолированный restart-тест настоящего Xray; все порты только loopback, конфиги временные."""

import contextlib
import http.server
import json
import socket
import subprocess
import tempfile
import threading
import time
import uuid
from pathlib import Path

from app.issuance import xray_config_store as store
from app.issuance.xray_api_client import alter_add_payload, alter_remove_payload


def port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def start(path):
    proc = subprocess.Popen(
        [store.XRAY_BIN, "run", "-config", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
    )
    time.sleep(0.5)
    if proc.poll() is not None:
        raise RuntimeError("Isolated Xray failed to start")
    return proc


def stop(proc):
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"durable-key-ok")

    def log_message(self, *_args):
        pass


def main():
    api_port, tcp_port, xhttp_port = port(), port(), port()
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    with tempfile.TemporaryDirectory(prefix="bravada-xray-restart-") as root:
        root = Path(root)
        old, new = str(uuid.uuid4()), str(uuid.uuid4())
        config = {
            "log": {"loglevel": "none"},
            "api": {
                "tag": "api",
                "listen": f"127.0.0.1:{api_port}",
                "services": ["HandlerService", "ReflectionService"],
            },
            "inbounds": [],
            "outbounds": [
                {
                    "protocol": "freedom",
                    "tag": "direct",
                    "settings": {"finalRules": [{"action": "allow", "ip": ["127.0.0.1"]}]},
                }
            ],
        }
        for transport, inbound_port in [("tcp", tcp_port), ("xhttp", xhttp_port)]:
            config["inbounds"].append(
                {
                    "tag": transport,
                    "listen": "127.0.0.1",
                    "port": inbound_port,
                    "protocol": "vless",
                    "settings": {"decryption": "none", "clients": [{"email": "test-user", "id": old}]},
                    "streamSettings": {"network": transport, "security": "none", "xhttpSettings": {"path": "/test"}},
                }
            )
        path = root / "config.json"
        path.write_text(json.dumps(config))
        server = start(path)

        def connects(key, transport, inbound_port):
            socks = port()
            client_path = root / "client.json"
            client_path.write_text(
                json.dumps(
                    {
                        "log": {"loglevel": "none"},
                        "inbounds": [
                            {"listen": "127.0.0.1", "port": socks, "protocol": "socks", "settings": {"auth": "noauth"}}
                        ],
                        "outbounds": [
                            {
                                "protocol": "vless",
                                "settings": {
                                    "vnext": [
                                        {
                                            "address": "127.0.0.1",
                                            "port": inbound_port,
                                            "users": [{"id": key, "encryption": "none"}],
                                        }
                                    ]
                                },
                                "streamSettings": {
                                    "network": transport,
                                    "security": "none",
                                    "xhttpSettings": {"path": "/test", "mode": "auto"},
                                },
                            }
                        ],
                    }
                )
            )
            client = start(client_path)
            try:
                result = subprocess.run(
                    [
                        "/usr/bin/curl",
                        "--silent",
                        "--max-time",
                        "3",
                        "--noproxy",
                        "",
                        "--socks5-hostname",
                        f"127.0.0.1:{socks}",
                        f"http://127.0.0.1:{httpd.server_port}/",
                    ],
                    capture_output=True,
                    timeout=5,
                )
                return result.returncode == 0 and result.stdout == b"durable-key-ok"
            finally:
                stop(client)

        try:
            for transport, inbound_port in [("tcp", tcp_port), ("xhttp", xhttp_port)]:
                assert connects(old, transport, inbound_port), f"baseline {transport}"
                request = {
                    "action": "remove",
                    "tag": transport,
                    "email": "test-user",
                    "api_port": api_port,
                    "rpc": json.loads(alter_remove_payload(tag=transport, email="test-user")),
                }
                assert store.transaction(request, path=path)
                request = {
                    "action": "add",
                    "tag": transport,
                    "clients": [{"email": "test-user", "id": new, "flow": ""}],
                    "api_port": api_port,
                    "rpc": json.loads(alter_add_payload(tag=transport, email="test-user", uuid=new)),
                }
                assert store.transaction(request, path=path)
                assert store.transaction(request, path=path), "repeat add must be idempotent"
            stop(server)
            server = start(path)
            # После restart нет ни backend, ни reconcile: только сохранённый файл.
            for transport, inbound_port in [("tcp", tcp_port), ("xhttp", xhttp_port)]:
                assert not connects(old, transport, inbound_port), f"old key accepted: {transport}"
                assert connects(new, transport, inbound_port), f"new key rejected: {transport}"
                print(f"{transport}: old_rejected=True new_accepted=True", flush=True)
        finally:
            with contextlib.suppress(Exception):
                stop(server)
            httpd.shutdown()
            httpd.server_close()


if __name__ == "__main__":
    main()
