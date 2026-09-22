"""Постоянные ключи Xray: транзакция на VPN-хосте, только стандартная библиотека."""

from __future__ import annotations

import base64
import copy
import fcntl
import json
import os
import shlex
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager, suppress
from pathlib import Path

CONFIG_PATH = "/usr/local/etc/xray/config.json"
XRAY_BIN = "/usr/local/bin/xray"
LOCK_TIMEOUT = 10


def decode_vless_account(raw: bytes) -> tuple[str, str]:
    """Строго извлекает UUID и flow, пропуская остальные protobuf-поля."""
    pos = 0
    fields: dict[int, str] = {}

    def read_varint() -> int:
        nonlocal pos
        value = 0
        for shift in range(0, 70, 7):
            if pos >= len(raw):
                raise ValueError("Truncated account")
            byte = raw[pos]
            pos += 1
            value |= (byte & 127) << shift
            if not byte & 128:
                return value
        raise ValueError("Invalid varint")

    while pos < len(raw):
        tag = read_varint()
        field, wire = tag >> 3, tag & 7
        if field == 0 or (field in (1, 2) and wire != 2):
            raise ValueError("Invalid account field")
        if wire == 0:
            read_varint()
            continue
        if wire == 2:
            size = read_varint()
        elif wire in (1, 5):
            size = 8 if wire == 1 else 4
        else:
            raise ValueError("Unsupported account wire type")
        end = pos + size
        if end > len(raw):
            raise ValueError("Truncated account field")
        if field in (1, 2):
            fields[field] = raw[pos:end].decode("utf-8")
        pos = end
    if not fields.get(1):
        raise ValueError("Missing account id")
    return fields[1], fields.get(2, "")


@contextmanager
def config_lock(path: Path):
    # Отдельный inode: os.replace конфигурации не должен сбрасывать блокировку.
    fd = os.open(str(path) + ".bravada.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "a") as lock:
        deadline = time.monotonic() + LOCK_TIMEOUT
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Config lock unavailable") from None
                time.sleep(0.05)
        yield


def _read_config(path: Path) -> dict:
    if not stat.S_ISREG(path.lstat().st_mode):
        raise ValueError("Config must be a regular file")
    data = json.loads(path.read_text())
    if not isinstance(data, dict) or not isinstance(data.get("inbounds"), list):
        raise ValueError("Invalid config")
    return data


def _inbound(config: dict, tag: str) -> dict:
    found = [ib for ib in config["inbounds"] if ib.get("tag") == tag]
    if len(found) != 1 or found[0].get("protocol") != "vless":
        raise ValueError("Expected exactly one VLESS inbound")
    return found[0]


def _clients(inbound: dict) -> list[dict]:
    clients = inbound.get("settings", {}).get("clients", [])
    if not isinstance(clients, list) or any(not isinstance(c, dict) for c in clients):
        raise ValueError("Invalid clients")
    return clients


def changed_config(config: dict, request: dict) -> dict:
    """Меняет только нужные credentials; операторские обновления сохраняют clients."""
    action = request["action"]
    if action == "replace_config":
        result = copy.deepcopy(request["config"])
        original_tags = {ib["tag"] for ib in config["inbounds"] if ib.get("protocol") == "vless"}
        new_tags = {ib["tag"] for ib in result["inbounds"] if ib.get("protocol") == "vless"}
        if original_tags != new_tags:
            raise ValueError("Changing VLESS topology requires a migration")
        for tag in original_tags:
            _inbound(result, tag).setdefault("settings", {})["clients"] = copy.deepcopy(_clients(_inbound(config, tag)))
        return result
    result = copy.deepcopy(config)
    inbound = _inbound(result, request["tag"])
    clients = _clients(inbound)
    if action == "remove":
        updated = [c for c in clients if c.get("email") != request["email"]]
    elif action in ("add", "snapshot"):
        wanted = request["clients"]
        emails = [c["email"] for c in wanted]
        if len(set(emails)) != len(emails) or any(not isinstance(e, str) or not e for e in emails):
            raise ValueError("Invalid requested emails")
        if action == "add" and len(wanted) != 1:
            raise ValueError("Expected a single client")
        old = {c.get("email"): c for c in clients}
        updated = [] if action == "snapshot" else [c for c in clients if c.get("email") not in emails]
        for c in wanted:
            if not isinstance(c.get("id"), str) or not c["id"] or c.get("flow", "") not in ("", "xtls-rprx-vision"):
                raise ValueError("Invalid requested account")
            updated.append({**old.get(c["email"], {}), **c})
        # Сортировка исключает лишние записи файла из-за порядка строк БД.
        updated.sort(key=lambda c: c.get("email", ""))
    else:
        raise ValueError("Unknown action")
    inbound.setdefault("settings", {})["clients"] = updated
    return result


def write_config(path: Path, config: dict, *, xray_bin: str = XRAY_BIN) -> None:
    """Проверка до replace; после успешного fsync файл пригоден для restart."""
    metadata = path.stat()
    fd, name = tempfile.mkstemp(prefix=".bravada-clients-", suffix=".json", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "w") as stream:
            os.fchown(stream.fileno(), metadata.st_uid, metadata.st_gid)
            os.fchmod(stream.fileno(), stat.S_IMODE(metadata.st_mode))
            json.dump(config, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        env = {**os.environ, "XRAY_LOCATION_ASSET": os.environ.get("XRAY_LOCATION_ASSET", "/usr/local/share/xray")}
        subprocess.run(  # noqa: S603 — фиксированные программы, аргументы без shell
            [xray_bin, "run", "-test", "-config", str(staged)],
            check=True,
            timeout=10,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=env,
        )
        os.replace(staged, path)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        staged.unlink(missing_ok=True)


def sync_config(path: Path) -> None:
    """Повторяет durability barrier даже после прошлого сбоя fsync после replace."""
    with path.open("rb") as stream:
        os.fsync(stream.fileno())
    directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def _rpc(method: str, payload: dict, api_port: int) -> tuple[bool, str]:
    if not isinstance(api_port, int) or not 0 < api_port < 65536:
        raise ValueError("Invalid API port")
    result = subprocess.run(  # noqa: S603 — фиксированные программы, аргументы без shell
        [
            "/usr/local/bin/grpcurl",
            "-max-time",
            "15",
            "-plaintext",
            "-d",
            "@",
            f"localhost:{api_port}",
            f"xray.app.proxyman.command.HandlerService/{method}",
        ],
        input=json.dumps(payload),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        timeout=18,
    )
    return result.returncode == 0, result.stdout


def transaction(request: dict, *, path: Path = Path(CONFIG_PATH), xray_bin: str = XRAY_BIN) -> bool:
    with config_lock(path):
        current = _read_config(path)
        desired = changed_config(current, request)
        if desired != current:
            write_config(path, desired, xray_bin=xray_bin)
        else:
            sync_config(path)
        action = request["action"]
        if action == "snapshot":
            return True
        if action == "replace_config":
            # Откат остаётся под тем же lock: более новая ротация ещё не могла начаться.
            try:
                subprocess.run(  # noqa: S603 — фиксированные программы, аргументы без shell
                    ["/usr/bin/systemctl", "restart", "xray"],
                    check=True,
                    timeout=25,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return True
            except (subprocess.SubprocessError, OSError):
                write_config(path, current, xray_bin=xray_bin)
                subprocess.run(  # noqa: S603 — фиксированные программы, аргументы без shell
                    ["/usr/bin/systemctl", "restart", "xray"],
                    check=False,
                    timeout=25,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                return False
        ok, _ = _rpc("AlterInbound", request["rpc"], request["api_port"])
        if ok:
            return True
        # Ответ мог потеряться или restart уже загрузил нужное состояние.
        ok, out = _rpc("GetInboundUsers", {"tag": request["tag"]}, request["api_port"])
        if not ok:
            return False
        users = json.loads(out).get("users", [])
        email = request["email"] if action == "remove" else request["clients"][0]["email"]
        matches = [u for u in users if u.get("email") == email]
        if action == "remove":
            return not matches
        if len(matches) != 1:
            return False
        account = matches[0]["account"]
        if account["type"] != "xray.proxy.vless.Account":
            return False
        actual = decode_vless_account(base64.b64decode(account["value"], validate=True))
        expected = request["clients"][0]
        return actual == (expected["id"], expected.get("flow", ""))


def _quarantine_remote(ssh) -> None:
    # Неопределённый результат SSH не доказывает завершение удалённого helper.
    # Закрываем канал и ждём полный TTL + kill-after от обнаружения сбоя:
    # SSH/sudo могли задержать фактический запуск относительно локального deadline.
    try:
        ssh.close()
    finally:
        time.sleep(110)


def run_remote_transaction(ssh, request: dict, *, root: bool) -> bool:
    """Одинаковый код на backend и хосте; секретные данные передаются только stdin."""
    source = Path(__file__).read_text()
    prefix = "" if root else "sudo -n "
    cmd = f"{prefix}timeout --kill-after=5s 100s python3 -c {shlex.quote(source)}"
    deadline = time.monotonic() + 110
    streams = []
    uncertain = True
    try:
        stdin, stdout, stderr = ssh.exec_command(cmd, timeout=110)
        streams = [stdin, stdout, stderr]
        stdin.write(json.dumps(request))
        stdin.flush()
        stdin.channel.shutdown_write()
        while not stdout.channel.exit_status_ready():
            if time.monotonic() >= deadline:
                raise TimeoutError("Durable mutation timed out")
            time.sleep(0.05)
        rc = stdout.channel.recv_exit_status()
        # -1 означает отсутствие exit-status, например разрыв транспорта.
        uncertain = rc == -1
        return rc == 0 and stdout.read().strip() == b"ok"
    finally:
        if uncertain:
            _quarantine_remote(ssh)
        for stream in streams:
            with suppress(Exception):
                stream.close()


if __name__ == "__main__":
    try:
        success = transaction(json.load(sys.stdin))
    except Exception:
        # Ни Xray diagnostics, ни содержимое credentials не выводим наружу.
        success = False
    print("ok" if success else "failed")
    sys.exit(0 if success else 1)
