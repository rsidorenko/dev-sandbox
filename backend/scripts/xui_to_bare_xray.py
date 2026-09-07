"""Конвертер cascade-egress сервера: 3x-ui -> standalone bare_xray.

Флот entries уже 100% bare_xray; egress-релеи (Frankfurt/Helsinki/LT/US) ещё
крутят 3x-ui. Конверсия унифицирует управление (один паттерн gRPC@10085),
убирает панель как поверхность атаки и включает per-user статистику тем же
мержем, что и enable_bare_xray_stats (переиспользуем merge_stats_keys).

Ключевой факт (рецепт [[cascade-egress-migration]]): egress НЕ в vpn_servers,
бот его не трогает — IP живёт только в entry-outbound. Конверсия на самом
egress не меняет НИ ключей, НИ портов: clients/keypair/reality в конфиге
остаются байт-в-байт из рендера x-ui, меняется только СПОСОБ ЗАПУСКА.

Фазы:
    adapt (pure) — конфиг x-ui -> эталон bare_xray: api-блок к виду флота
                   (top-level listen 127.0.0.1:10085, короткие имена сервисов,
                   +ReflectionService/+StatsService), dokodemo api-инбаунд и
                   его routing-правило убираются, stats/policy — merge'ом;
    phase-a      — на боксе: прочитать рендер x-ui, adapt, записать кандидат
                   в /usr/local/etc/xray/config.json, `xray run -test` gate.
                   Нулевое влияние на прод (x-ui продолжает работать);
    phase-b      — установка: копия бинаррия x-ui -> /usr/local/bin/xray,
                   geo-asset'ы -> /usr/local/share/xray/, эталонный xray.service
                   (из bare_xray_provision), grpcurl-чек, бот-ключ в
                   authorized_keys (расширяет покрытие коллектора/монитора).
                   xray НЕ стартуем;
    phase-c      — cutover (~секунды даунтайма): stop+disable x-ui -> start
                   xray -> verify (is-active, GetInboundUsers == clients в
                   конфиге, established conns, QueryStats);
    --rollback   — stop xray -> enable+start x-ui обратно (конфиги не трогаем:
                   x-ui рендерит свой из DB, наш /usr/local/etc/xray остаётся
                   для повторной попытки).

Usage:
    python -m scripts.xui_to_bare_xray phase-a --host H [--key K] \
        [--xui-config /usr/local/x-ui/bin/config.json]
    python -m scripts.xui_to_bare_xray phase-b --host H --bot-key-file pub.key
    python -m scripts.xui_to_bare_xray phase-c --host H
    python -m scripts.xui_to_bare_xray rollback --host H
"""

from __future__ import annotations

import argparse
import copy
import json
import sys

from scripts.bare_xray_provision import XRAY_SERVICE_UNIT, connect
from scripts.canonicalize_routing import CONFIG_PATH, GRPC_API, _run
from scripts.enable_bare_xray_stats import merge_stats_keys, stats_enabled

DEFAULT_XUI_CONFIG = "/usr/local/x-ui/bin/config.json"
XUI_XRAY_BIN = "/usr/local/x-ui/bin/xray"  # бинаррий x-ui (до phase-b /usr/local/bin/xray нет)
BACKUP_SUFFIX = ".pre-bare.bak"

# Эталон api-блока bare_xray-флота (setup_relay.py + живые конфиги entries):
# top-level listen, короткие имена сервисов.
_API_LISTEN = "127.0.0.1:10085"
_HANDLER_SHORT, _HANDLER_FQCN = "HandlerService", "xray.app.proxyman.command.HandlerService"
_REFLECTION_SHORT, _REFLECTION_FQCN = "ReflectionService", "xray.app.reflection.command.ReflectionService"
_STATS_SHORT = "StatsService"


def _ensure_service(services: list[str], short: str, fqcn: str) -> list[str]:
    """Аппенд сервиса в стиле существующего списка (флот — короткие имена,
    x-ui рендерит FQCN; не смешиваем формы без нужды)."""
    if short in services or fqcn in services:
        return services
    use_short = any("." not in str(s) for s in services)
    return [*services, short if use_short else fqcn]


def adapt_xui_config(config: dict) -> tuple[dict, list[str]]:
    """Pure: рендер x-ui -> эталон конфига bare_xray. Всё содержательное
    (clients/keypair/ports/routing) остаётся нетронутым; меняется только
    api-блок + включается stats. Возвращает (новый конфиг, список изменений).
    """
    cfg = copy.deepcopy(config)
    notes: list[str] = []

    api = cfg.get("api")
    old_api = config.get("api") if isinstance(config.get("api"), dict) else {}
    if not isinstance(api, dict):
        api = {}
        cfg["api"] = api
        notes.append("+api")
    if api.get("listen") != _API_LISTEN:
        api["listen"] = _API_LISTEN
        api["tag"] = "api"
        notes.append(f"api.listen={_API_LISTEN} (было {old_api.get('listen')!r})")
    services = api.get("services")
    if not isinstance(services, list):
        services = []
    before = len(services)
    services = _ensure_service(services, _HANDLER_SHORT, _HANDLER_FQCN)
    services = _ensure_service(services, _REFLECTION_SHORT, _REFLECTION_FQCN)
    services = _ensure_service(services, _STATS_SHORT, "xray.app.stats.command.StatsService")
    api["services"] = services
    if len(services) != before:
        notes.append(f"api.services={services}")

    # dokodemo api-инбаунд x-ui больше не нужен — слушает top-level api.listen
    inbounds = cfg.get("inbounds", [])
    without_api_ib = [ib for ib in inbounds if ib.get("tag") != "api"]
    if len(without_api_ib) != len(inbounds):
        cfg["inbounds"] = without_api_ib
        notes.append("-dokodemo api inbound")

    rules = (cfg.get("routing", {}) or {}).get("rules") or []
    without_api_rule = [r for r in rules if r.get("inboundTag") != ["api"]]
    if len(without_api_rule) != len(rules):
        cfg.setdefault("routing", {})["rules"] = without_api_rule
        notes.append("-routing rule inboundTag=api")

    merged, stats_changes = merge_stats_keys(cfg)
    notes.extend(stats_changes)
    assert stats_enabled(merged)  # контракт adapt: на выходе stats включён
    return merged, notes


# --- SSH-фазы -------------------------------------------------------------------


def cmd_phase_a(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        rc, raw, err = _run(cli, f"sudo cat {args.xui_config}")
        if rc != 0 or not raw.strip():
            print(f"cannot read x-ui config {args.xui_config}: {err[:200]}", file=sys.stderr)
            return 1
        adapted, notes = adapt_xui_config(json.loads(raw))
        print(f"adapt: {len(notes)} change(s): {'; '.join(notes)}")

        payload = json.dumps(adapted, ensure_ascii=False, indent=2)
        script = (
            "set -e\n"
            f"sudo mkdir -p {CONFIG_PATH.rsplit('/', 1)[0]}\n"
            # кандидат пишем в /tmp; финальный config.json — только после test-gate
            "cat > /tmp/config.bare.json <<'__CFG__'\n" + payload + "\n__CFG__\n"
            f"sudo {XUI_XRAY_BIN} run -test -config /tmp/config.bare.json 2>&1 | tail -2\n"
        )
        rc, out, err = _run(cli, script, timeout=120)
        print(out)
        if err.strip():
            print("[stderr] " + err[:500], file=sys.stderr)
        if rc != 0 or "Configuration OK" not in out:
            print("phase-a: test gate FAILED — candidate NOT installed", file=sys.stderr)
            return 1
        # test прошёл -> install как финальный конфиг + бэкап пути x-ui рендера
        rc, out, err = _run(
            cli,
            f"sudo cp {args.xui_config} {args.xui_config}{BACKUP_SUFFIX} && "
            f"sudo install -m 600 /tmp/config.bare.json {CONFIG_PATH} && rm -f /tmp/config.bare.json && "
            f"echo installed {CONFIG_PATH}",
            timeout=60,
        )
        print(out.strip() or err.strip())
        return rc
    finally:
        cli.close()


def cmd_phase_b(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        if args.bot_key_file:
            sftp = cli.open_sftp()
            sftp.put(args.bot_key_file, "/tmp/bot_key.pub")
            sftp.close()
        script = (
            "set -e\n"
            "echo '--- xray binary + assets (из дистрибутива x-ui)'\n"
            "sudo cp /usr/local/x-ui/bin/xray /usr/local/bin/xray\n"
            "sudo mkdir -p /usr/local/share/xray\n"
            "sudo cp /usr/local/x-ui/bin/*.dat /usr/local/share/xray/ 2>/dev/null || echo 'WARN: no .dat assets'\n"
            "echo '--- xray.service etalon'\n"
            "sudo tee /etc/systemd/system/xray.service >/dev/null <<'UNIT'\n" + XRAY_SERVICE_UNIT + "UNIT\n"
            "sudo systemctl daemon-reload\n"
            "sudo systemctl enable xray >/dev/null 2>&1 && echo 'xray enabled (not started)'\n"
            "echo '--- grpcurl:'\n"
            "test -x /usr/local/bin/grpcurl && echo present || "
            "echo 'MISSING: поставь grpcurl вручную (curl -L github grpcurl release -> /usr/local/bin)'\n"
        )
        if args.bot_key_file:
            script += (
                "echo '--- bot key (idempotent append):'\n"
                "grep -qFf /tmp/bot_key.pub ~/.ssh/authorized_keys 2>/dev/null || "
                "cat /tmp/bot_key.pub >> ~/.ssh/authorized_keys\n"
                "rm -f /tmp/bot_key.pub\n"
                "ssh-keygen -lf ~/.ssh/authorized_keys | tail -5\n"
            )
        rc, out, err = _run(cli, script, timeout=300)
        print(out[-4000:])
        if err.strip():
            print("[stderr] " + err[:1000], file=sys.stderr)
        return rc
    finally:
        cli.close()


def cmd_phase_c(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        rc, out, err = _run(
            cli,
            "set -e\n"
            "sudo systemctl stop x-ui\n"
            "sudo systemctl disable x-ui >/dev/null 2>&1 || true\n"
            "sudo systemctl start xray\n"
            "sleep 2\n"
            "systemctl is-active xray x-ui\n"
            "sudo ss -tlnp | grep -E ':(443|8443|10085) ' | head -5\n",
            timeout=120,
        )
        print(out)
        if err.strip():
            print("[stderr] " + err[:600], file=sys.stderr)
        first_line = out.splitlines()[0].strip() if out else ""
        if first_line != "active":
            print("phase-c: xray not active — ROLLBACK: python -m scripts.xui_to_bare_xray rollback", file=sys.stderr)
            return 1

        # verify: юзеры в памяти == clients в конфиге; QueryStats живой
        rc, cfg_raw, _ = _run(cli, f"sudo cat {CONFIG_PATH}")
        counts = {}
        if rc == 0:
            for ib in json.loads(cfg_raw).get("inbounds", []):
                if ib.get("protocol") == "vless":
                    clients = (ib.get("settings") or {}).get("clients") or []
                    counts[ib.get("tag")] = len(clients)
        ok = bool(counts)
        for tag, expected in counts.items():
            req = json.dumps({"tag": tag})
            rc, out, _ = _run(
                cli,
                f"sudo grpcurl -plaintext -d {req!r} {GRPC_API} "
                "xray.app.proxyman.command.HandlerService/GetInboundUsers",
                timeout=15,
            )
            served = out.count('"email"') if rc == 0 else -1
            match = served == expected
            ok = ok and match
            print(f"{tag}: served={served} configured={expected} {'ok' if match else 'MISMATCH'}")

        rc, out, _ = _run(
            cli,
            'sudo grpcurl -plaintext -d \'{"pattern":["user>>>"],"reset":false}\' '
            f"{GRPC_API} xray.app.stats.command.StatsService/QueryStats",
            timeout=15,
        )
        stats_ok = rc == 0
        print(f"QueryStats: {'ok' if stats_ok else 'FAILED'}")
        return 0 if ok and stats_ok else 1
    finally:
        cli.close()


def cmd_rollback(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        rc, out, err = _run(
            cli,
            "sudo systemctl stop xray\n"
            "sudo systemctl enable x-ui >/dev/null 2>&1 || true\n"
            "sudo systemctl start x-ui\n"
            "sleep 2\n"
            "systemctl is-active x-ui xray\n",
            timeout=120,
        )
        print(out)
        if err.strip():
            print("[stderr] " + err[:600], file=sys.stderr)
        # x-ui рендерит свой конфиг из DB — наш /usr/local/etc/xray/config.json
        # остаётся на месте для повторной попытки phase-c.
        return 0 if rc == 0 and out.splitlines() and out.splitlines()[0].strip() == "active" else 1
    finally:
        cli.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", required=True)
        p.add_argument("--user", default="romtan")
        p.add_argument("--key", default="D:/tmp/cloudru_key.pem")

    for name, fn, help_ in (
        ("phase-a", cmd_phase_a, "adapt конфига x-ui + test-gate (прод не трогаем)"),
        ("phase-b", cmd_phase_b, "установка бинаррия/сервиса/ключа (xray не стартуем)"),
        ("phase-c", cmd_phase_c, "cutover: stop x-ui -> start xray -> verify"),
        ("rollback", cmd_rollback, "stop xray -> start x-ui обратно"),
    ):
        p = sub.add_parser(name, help=help_)
        _common(p)
        if name == "phase-a":
            p.add_argument("--xui-config", default=DEFAULT_XUI_CONFIG)
        if name == "phase-b":
            p.add_argument("--bot-key-file", default="", help="локальный pub-ключ бота (cloudru.pub)")
        p.set_defaults(fn=fn)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
