"""Консолидированный пайплайн миграции/подготовки bare_xray ENTRY-серверов.

Один инструмент вместо одноразовых скриптов D:/tmp/mig* (US 2026-08-14, YT
2026-08-26, CDN-LT 2026-08-29 — все отработали LIVE). Подкоманды повторяют
фазы рецепта [[us-entry-migration-2026-08]]:

    survey    — read-only обзор бокса: сервисы, конфиг, gRPC-юзеры, серты,
                nginx, geo-asset'ы, ufw, диск (безопасно в любой момент);
    bundle    — собрать tar-бандл на источнике (xray/grpcurl, конфиг, geoip,
                nginx, letsencrypt, webroot) и забрать sftp на локаль;
    prep      — залить бандл на новый бокс и установить: распаковка БЕЗ
                authorized_keys, apt nginx+certbot, эталонный xray.service,
                ufw, nginx -t, `xray run -test`, enable-но-НЕ-старт, preflight
                DNS/SG (cloud.ru SG открывает только 22 — см. готчи рецепта);
    cutover   — дамп живых gRPC-юзеров со СТАРОГО -> settings.clients НОВОГО ->
                test -> backup -> install -> start xray -> сверка счётчиков/портов;
    db-flip   — на main: UPDATE vpn_servers.server_host для id-строк -> restart
                backend -> лог reconcile (сходится сам за ~3 мин таймером);
    e2e       — pbk/sid/sni из БД бота + uuid живого юзера с бокса -> temp
                socks+xray на probe-хосте -> api.ipify = egress IP;
    health    — certbot dry-run, ошибки journalctl, gRPC-юзеры, живые коннекты.

Готчи, зашитые в скрипт (все пойманы живьём, см. память us-entry-migration):
  * tar-бандл содержит authorized_keys источника — распаковка `tar -C /`
    ПЕРЕЗАТИРАЕТ ключи нового бокса (всегда --exclude, recover_access.py
    в D:/tmp/mig — если уже случилось);
  * cloud.ru SG по умолчанию открывает только 22 — prep делает self-probe
    (curl на собственный публичный IP): 000 = порты закрыты В ПАНЕЛИ, UFW
    на боксе НЕ достаточно;
  * свежая ВМ cloud.ru может иметь сломанный resolv.conf (systemd-stub без
    аплинков) — prep чинит статикой 77.88.8.8 + 1.1.1.8;
  * xray НЕ стартуем до persist-юзеров (gRPC-клиенты in-memory, рестарт их
    сносит) — для этого разделение prep/cutover;
  * pbk надёжнее брать из БД (vpn_servers.reality_pbk), а не `xray x25519`
    (пишет в stderr).

Доступ к main (db-flip, e2e): НИКАКИХ секретов в коде — env MAIN_SSH_KEY
(путь) или MAIN_SSH_PASSWORD, хост MAIN_SSH_HOST (default 109.120.178.49),
юзер MAIN_SSH_USER (default root). Коммит паролей/ключей запрещён.

Usage:
    python -m scripts.bare_xray_provision survey  --host 1.2.3.4 [--key K]
    python -m scripts.bare_xray_provision bundle  --host OLD --out D:/tmp/b.tgz
    python -m scripts.bare_xray_provision prep    --host NEW --bundle D:/tmp/b.tgz
    python -m scripts.bare_xray_provision cutover --old-host OLD --host NEW \
        --tags in-443-tcp,in-8443-xhttp
    python -m scripts.bare_xray_provision db-flip --server-ids 8,9 --new-host H
    python -m scripts.bare_xray_provision e2e     --host NEW --server-id 8 \
        --probe-host OLD --probe-key K
    python -m scripts.bare_xray_provision health  --host NEW [--cert-name NAME]
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys

from scripts.canonicalize_routing import (
    CONFIG_PATH,
    GRPC_API,
    XRAY_BIN,
    _run,
    parse_grpc_users,
)

# --- эталоны (pure, юнит-тестируются) ----------------------------------------

# Что едет в бандле (reцепт US-миграции): бинарри+конфиг+geodata, nginx+LE+webroot.
BUNDLE_PATHS = [
    "usr/local/bin/xray",
    "usr/local/bin/grpcurl",
    "usr/local/etc/xray",
    "usr/local/share/xray",
    "etc/nginx/ssl",
    "etc/nginx/sites-available",
    "etc/nginx/sites-enabled",
    "etc/letsencrypt",
    "var/www",
]

# ⚠️ authorized_keys источника в /home/** может приехать в бандле (etc/... нет,
# но home иногда добавляли вручную) — exclude всегда, распаковка без него
# выкидывала ключ оператора (gotcha #1 US-миграции).
BUNDLE_EXCLUDES = ["home/*/.ssh/authorized_keys", "./home/*/.ssh/authorized_keys"]

DEFAULT_FW_PORTS = ("22", "80", "443", "8443")

XRAY_SERVICE_UNIT = """\
[Unit]
Description=Xray (bare_xray, standalone)
After=network.target

[Service]
Type=simple
Environment=XRAY_LOCATION_ASSET=/usr/local/share/xray
ExecStart=/usr/local/bin/xray run -config /usr/local/etc/xray/config.json
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
"""

_HOST_RE = re.compile(r"^[0-9a-zA-Z._:-]+$")


def bundle_command(out: str = "/tmp/mig_bundle.tgz") -> str:
    """tar-команда сборки бандла на источнике (absolute paths, excludes ALWAYS)."""
    excludes = " ".join(f"--exclude={e}" for e in BUNDLE_EXCLUDES)
    paths = " ".join(BUNDLE_PATHS)
    return f"sudo tar czf {out} {excludes} -C / {paths}"


def flip_sql(server_ids: list[int], new_host: str) -> str:
    """UPDATE vpn_servers.server_host (int-ids + хост валидируются — параметризовать
    psql через docker exec неудобно, поэтому строгая валидация до подстановки)."""
    if not server_ids or any(not isinstance(i, int) or i <= 0 for i in server_ids):
        raise ValueError("server_ids must be positive integers")
    if not _HOST_RE.match(new_host):
        raise ValueError(f"invalid host: {new_host!r}")
    ids = ",".join(str(i) for i in server_ids)
    return f"UPDATE vpn_servers SET server_host='{new_host}' WHERE id IN ({ids}) RETURNING id, label, server_host;"


def patch_config_clients(config: dict, live_users: dict[str, list[dict]]) -> dict:
    """Deep-copy конфига с settings.clients := живые gRPC-юзеры per inbound-тег.

    persist-этап cutover: рестарт xray сносит in-memory gRPC-клиентов, поэтому
    до старта на новом боксе в конфиг записывается дамп живых со старого.
    """
    cfg = copy.deepcopy(config)
    for ib in cfg.get("inbounds", []):
        tag = ib.get("tag")
        if tag in live_users:
            ib.setdefault("settings", {})["clients"] = copy.deepcopy(live_users[tag])
    return cfg


def e2e_test_config(*, uuid: str, pbk: str, sid: str, sni: str, host: str, port: int = 443) -> dict:
    """Pure: temp-конфиг probe (socks :11808 -> vless/reality на проверяемый бокс).
    UUID берём с бокса (gRPC), pbk/sid/sni — из БД бота (авторитет для юзеров)."""
    return {
        "inbounds": [
            {"tag": "socks-in", "listen": "127.0.0.1", "port": 11808, "protocol": "socks", "settings": {"udp": False}}
        ],
        "outbounds": [
            {
                "tag": "test-leg",
                "protocol": "vless",
                "settings": {"vnext": [{"address": host, "port": port, "users": [{"id": uuid, "encryption": "none"}]}]},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "publicKey": pbk,
                        "shortId": sid,
                        "serverName": sni,
                        "fingerprint": "chrome",
                    },
                },
            },
            {"tag": "direct", "protocol": "freedom"},
        ],
    }


def survey_script() -> str:
    """Read-only обзор бокса (safe в любой момент)."""
    return r"""
echo "=== host ==="; hostname; uptime; uname -r
echo "--- services:"; systemctl is-active xray x-ui 2>/dev/null; systemctl is-enabled xray x-ui 2>/dev/null
/usr/local/bin/xray version 2>/dev/null | head -1
echo "--- config summary:"
sudo python3 -c "
import json
cfg=json.load(open('/usr/local/etc/xray/config.json'))
print('top-level api:', bool(cfg.get('api')), 'stats:', bool(cfg.get('stats')))
for ib in cfg.get('inbounds',[]):
    rs=(ib.get('streamSettings',{}).get('realitySettings',{}) or {})
    print('IN ', ib.get('tag'), ib.get('port'), ib.get('protocol'), rs.get('serverNames'), 'dest=',rs.get('dest'))
    print('    clients_static:', len((ib.get('settings',{}) or {}).get('clients',[]) or []))
for ob in cfg.get('outbounds',[]):
    v=((ob.get('settings',{}) or {}).get('vnext') or [{}])[0]
    print('OUT', ob.get('tag'), ob.get('protocol'), v.get('address'), v.get('port'))
print('rules:', len(cfg.get('routing',{}).get('rules',[])))
"
echo "--- live gRPC users:"
for tag in $(sudo python3 -c "
import json
cfg=json.load(open('/usr/local/etc/xray/config.json'))
print(' '.join(ib.get('tag','') for ib in cfg.get('inbounds',[]) if ib.get('protocol')=='vless'))"); do
  n=$(sudo grpcurl -plaintext -d "{\"tag\":\"$tag\"}" localhost:10085 \
      xray.app.proxyman.command.HandlerService/GetInboundUsers 2>/dev/null | grep -c '"email"' || true)
  echo "$tag: $n"
done
echo "--- certs:"; sudo ls /etc/letsencrypt/live/ 2>/dev/null; ls ~/.acme.sh/ 2>/dev/null | head -5
echo "--- nginx:"; sudo nginx -t 2>&1 | tail -1; systemctl is-active nginx
echo "--- assets:"; ls /usr/local/share/xray/ 2>/dev/null | head -6
ls -la /usr/local/bin/grpcurl /usr/local/bin/xray 2>/dev/null
echo "--- ufw:"; sudo ufw status 2>/dev/null | head -12
echo "--- disk:"; df -h / | tail -1
"""


def ufw_script(ports: tuple[str, ...] = DEFAULT_FW_PORTS) -> str:
    """UFW: 22 ПЕРВОЙ (не закрыть себе дверь), потом остальные."""
    lines = [f"sudo ufw allow {p}/tcp" for p in sorted(ports, key=lambda p: p != "22")]
    lines.append("sudo ufw --force enable")
    lines.append("sudo ufw status | head -12")
    return "\n".join(lines)


# --- SSH-core ------------------------------------------------------------------


def _default_key() -> str:
    return os.environ.get("CLOUDRU_SSH_KEY", "D:/tmp/cloudru_key.pem")


def connect(host: str, user: str, key: str):
    import paramiko

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {"port": 22, "username": user, "key_filename": key,
                    "look_for_keys": False, "allow_agent": False, "timeout": 20}
    # Jump через основной хост: у части egress-боксов :22 фильтруется на уровне
    # провайдера (SG main-only, ufw при этом открыт — US egress). Секреты только
    # из env (паттерн connect_main): SSH_JUMP_HOST/USER + SSH_JUMP_KEY|PASSWORD.
    jump_host = os.environ.get("SSH_JUMP_HOST")
    if jump_host:
        jump = paramiko.SSHClient()
        jump.set_missing_host_key_policy(paramiko.AutoAddPolicy())
        jump_kw: dict = {"port": 22, "look_for_keys": False, "allow_agent": False,
                         "timeout": 25, "banner_timeout": 20, "auth_timeout": 20}
        if os.environ.get("SSH_JUMP_KEY"):
            jump_kw["key_filename"] = os.environ["SSH_JUMP_KEY"]
        elif os.environ.get("SSH_JUMP_PASSWORD"):
            jump_kw["password"] = os.environ["SSH_JUMP_PASSWORD"]
        else:
            raise SystemExit("SSH_JUMP_HOST задан — нужен SSH_JUMP_KEY или SSH_JUMP_PASSWORD")
        jump.connect(jump_host, username=os.environ.get("SSH_JUMP_USER", "root"), **jump_kw)
        chan = jump.get_transport().open_channel("direct-tcpip", (host, 22), ("127.0.0.1", 0))
        cli.connect(host, sock=chan, **kwargs)
        # держим ссылку на jump-транспорт: канал жив, пока жив transport
        cli._jump_client = jump
        return cli
    cli.connect(host, **kwargs)
    return cli


def connect_main():
    """Доступ к main для db-flip/e2e. Секреты ТОЛЬКО из env, никогда в коде."""
    import paramiko

    host = os.environ.get("MAIN_SSH_HOST", "109.120.178.49")
    user = os.environ.get("MAIN_SSH_USER", "root")
    password = os.environ.get("MAIN_SSH_PASSWORD", "")
    key = os.environ.get("MAIN_SSH_KEY", "")
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    kwargs: dict = {"look_for_keys": False, "allow_agent": False, "timeout": 25}
    if key:
        kwargs["key_filename"] = key
    elif password:
        kwargs["password"] = password
    else:
        raise SystemExit("set MAIN_SSH_KEY or MAIN_SSH_PASSWORD env (no secrets in code)")
    cli.connect(host, port=22, username=user, **kwargs)
    return cli


def _require(ok: bool, msg: str) -> None:
    if not ok:
        print(f"FAILED: {msg}", file=sys.stderr)
        raise SystemExit(1)


# --- подкоманды ----------------------------------------------------------------


def cmd_survey(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        rc, out, err = _run(cli, survey_script(), timeout=120)
        print(out)
        if err.strip():
            print("[stderr] " + err[:600], file=sys.stderr)
        return rc
    finally:
        cli.close()


def cmd_bundle(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        rc, out, err = _run(cli, bundle_command(), timeout=600)
        _require(rc == 0, f"bundle build rc={rc}: {err[:300]}")
        print(f"bundle built on {args.host}: /tmp/mig_bundle.tgz")
        sftp = cli.open_sftp()
        sftp.get("/tmp/mig_bundle.tgz", args.out)
        sftp.close()
        _run(cli, "sudo rm -f /tmp/mig_bundle.tgz")
        print(f"downloaded -> {args.out}")
        return 0
    finally:
        cli.close()


def cmd_prep(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        sftp = cli.open_sftp()
        sftp.put(args.bundle, "/tmp/mig_bundle.tgz")
        sftp.close()
        print("bundle uploaded")
        ports = tuple(p.strip() for p in args.fw_ports.split(",") if p.strip())
        install = f"""
set -e
echo "--- extract (authorized_keys excluded ALWAYS)"
sudo tar xzf /tmp/mig_bundle.tgz -C / {" ".join(f"--exclude={e}" for e in BUNDLE_EXCLUDES)}
echo "--- DNS preflight (cloud.ru stub-resolver gotcha)"
grep -q '^nameserver 77.88.8.8' /etc/resolv.conf 2>/dev/null || \
    printf 'nameserver 77.88.8.8\\nnameserver 1.1.1.8\\n' | sudo tee /etc/resolv.conf >/dev/null
curl -s --max-time 8 -o /dev/null -w 'apt-host resolve: %{{http_code}}\\n' https://deb.debian.org/ || echo "WARN: no outbound DNS/HTTP yet"
echo "--- install nginx/certbot/curl"
export DEBIAN_FRONTEND=noninteractive
sudo apt-get update -qq 2>&1 | tail -1
sudo apt-get install -y -qq nginx certbot curl 2>&1 | tail -1
echo "--- xray.service etalon"
sudo tee /etc/systemd/system/xray.service >/dev/null <<'UNIT'
{XRAY_SERVICE_UNIT}UNIT
sudo systemctl daemon-reload
echo "--- keys intact:"
ssh-keygen -lf $HOME/.ssh/authorized_keys || echo "WARN: authorized_keys missing/empty!"
echo "--- nginx (migrated config)"
sudo nginx -t && sudo systemctl enable nginx >/dev/null 2>&1 && sudo systemctl restart nginx
systemctl is-active nginx
echo "--- ufw (22 first)"
{ufw_script(ports)}
echo "--- xray config test (NOT started — cutover will)"
sudo {XRAY_BIN} run -test -config {CONFIG_PATH} 2>&1 | tail -2
sudo systemctl enable xray >/dev/null 2>&1 && echo "xray enabled (not started)"
echo "--- SG self-probe (000 = ports closed in PROVIDER panel, UFW is not enough):"
PUB=$(curl -s --max-time 6 https://api.ipify.org || echo 127.0.0.1)
for p in {" ".join(p for p in ports if p != "22")}; do
  curl -sk --max-time 5 -o /dev/null -w "port $p self: %{{http_code}}\\n" https://$PUB:$p/ || echo "port $p self: unreachable"
done
rm -f /tmp/mig_bundle.tgz
"""
        rc, out, err = _run(cli, install, timeout=900)
        print(out[-8000:])
        if err.strip():
            print("[stderr] " + err[-1500:], file=sys.stderr)
        return rc
    finally:
        cli.close()


def cmd_cutover(args) -> int:
    tags = [t.strip() for t in args.tags.split(",") if t.strip()]
    _require(bool(tags), "--tags required (vless inbound tags, см. survey)")
    old = connect(args.old_host, args.old_user, args.old_key)
    new = connect(args.host, args.user, args.key)
    try:
        # 1) дамп живых юзеров со старого (in-memory gRPC — единственный авторитет)
        live: dict[str, list[dict]] = {}
        for tag in tags:
            req = json.dumps({"tag": tag})
            rc, out, err = _run(
                old,
                f"sudo grpcurl -plaintext -d {req!r} {GRPC_API} "
                "xray.app.proxyman.command.HandlerService/GetInboundUsers",
                timeout=15,
            )
            _require(rc == 0, f"GetInboundUsers old {tag}: {err[:200]}")
            live[tag] = parse_grpc_users(out)
            print(f"OLD {tag}: {len(live[tag])} live users")

        # 2) патч конфига нового
        rc, cfg_raw, err = _run(new, f"sudo cat {CONFIG_PATH}")
        _require(rc == 0 and cfg_raw.strip(), f"read config new: {err[:200]}")
        patched = patch_config_clients(json.loads(cfg_raw), live)
        payload = json.dumps(patched, ensure_ascii=False, indent=2)

        sftp = new.open_sftp()
        with sftp.open("/tmp/config.patched.json", "w") as f:
            f.write(payload)
        sftp.close()

        # 3) test -> backup -> install -> start -> verify (gotcha: старт без persist сносит юзеров)
        rc, out, err = _run(
            new,
            "set -e\n"
            f"sudo {XRAY_BIN} run -test -config /tmp/config.patched.json 2>&1 | tail -1\n"
            f"sudo cp {CONFIG_PATH} {CONFIG_PATH}.bak-pre-cutover\n"
            "sudo install -m 600 /tmp/config.patched.json "
            f"{CONFIG_PATH}\n"
            "rm -f /tmp/config.patched.json\n"
            "sudo systemctl start xray\n"
            "sleep 2\n"
            "systemctl is-active xray\n"
            "sudo ss -tlnp | grep -E ':(443|8443|10085) ' | head -5\n",
            timeout=120,
        )
        print(out)
        if err.strip():
            print("[stderr] " + err[:800], file=sys.stderr)
        _require("active" in out, "xray not active after start")

        # 4) сверка: новый бокc отдаёт столько же юзеров per tag
        ok = True
        for tag in tags:
            count = grpc_count(new, tag)
            status = "ok" if count == len(live[tag]) else "MISMATCH"
            ok = ok and count == len(live[tag])
            print(f"NEW {tag}: {count} served (expected {len(live[tag])}) {status}")
        return 0 if ok else 1
    finally:
        old.close()
        new.close()


def cmd_db_flip(args) -> int:
    ids = [int(x) for x in args.server_ids.split(",") if x.strip()]
    sql = flip_sql(ids, args.new_host)
    cli = connect_main()
    try:
        psql = "docker exec bravada-postgres-1 psql -U vpnbot -d vpnbot -P pager=off -c"
        ids_sql = ",".join(str(i) for i in ids)
        rc, out, err = _run(
            cli,
            f'{psql} "SELECT id, label, server_host, is_active FROM vpn_servers WHERE id IN ({ids_sql});"',
            timeout=60,
        )
        print("=== before:\n" + (out or err))
        rc, out, err = _run(cli, f'{psql} "{sql}"', timeout=60)
        print("=== flip:\n" + (out or err))
        _require(rc == 0 and "UPDATE" in out, f"UPDATE failed: {err[:300]}")
        rc, out, err = _run(cli, "docker restart bravada-backend-1", timeout=120)
        print(f"backend restart: {out.strip() or err.strip()}")
        import time

        time.sleep(25)
        rc, out, err = _run(
            cli,
            "docker logs bravada-backend-1 --since 3m 2>&1 | grep -iE 'reconcile|bare_xray' | tail -20",
            timeout=60,
        )
        print("=== reconcile log:\n" + (out or "(no reconcile lines yet — 3-мин таймер догонит)"))
        return 0
    finally:
        cli.close()


def cmd_e2e(args) -> int:
    # pbk/sid/sni — из БД бота (авторитет для юзерских клиентов)
    main = connect_main()
    try:
        rc, out, err = _run(
            main,
            "docker exec bravada-postgres-1 psql -U vpnbot -d vpnbot -t -A -F'|' -c "
            f'"SELECT reality_pbk, reality_sid, reality_sni FROM vpn_servers WHERE id={int(args.server_id)};"',
            timeout=60,
        )
        parts = out.strip().split("|")
        _require(len(parts) == 3 and all(parts), f"db fetch failed: {out!r} {err[:200]}")
        pbk, sid, sni = parts
    finally:
        main.close()

    target = connect(args.host, args.user, args.key)
    try:
        # uuid живого юзера с бокса (первый попавшийся валидный)
        req = json.dumps({"tag": args.tag})
        rc, out, err = _run(
            target,
            f"sudo grpcurl -plaintext -d {req!r} {GRPC_API} "
            'xray.app.proxyman.command.HandlerService/GetInboundUsers | python3 -c "\n'
            "import sys,json,base64,re\n"
            "d=json.load(sys.stdin)\n"
            "for u in d.get('users',[]):\n"
            "    v=(u.get('account') or {}).get('value','')\n"
            "    try: raw=base64.b64decode(v)\n"
            "    except Exception: continue\n"
            "    m=re.search(rb'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}',raw)\n"
            "    if m: print(m.group(0).decode()); break\n"
            '"',
            timeout=30,
        )
        uuid = out.strip().splitlines()[0] if out.strip() else ""
        _require(bool(uuid), f"uuid fetch failed: {err[:200]}")
        print(f"pbk={pbk[:10]}... sid={sid} sni={sni} uuid={uuid[:8]}...")
    finally:
        target.close()

    probe = connect(args.probe_host, args.probe_user, args.probe_key)
    try:
        cfg = json.dumps(
            e2e_test_config(uuid=uuid, pbk=pbk, sid=sid, sni=sni, host=args.host, port=args.port),
            indent=2,
        )
        test = (
            "cat > /tmp/e2e_test.json <<'CFG'\n" + cfg + "\nCFG\n"
            f"{XRAY_BIN} run -c /tmp/e2e_test.json >/tmp/e2e_xray.log 2>&1 & \n"
            "XPID=$!\n"
            "sleep 2\n"
            'echo "=== foreign leg via target (expect egress IP):"\n'
            "curl -4 -s --max-time 20 --socks5-hostname 127.0.0.1:11808 https://api.ipify.org; echo\n"
            "kill $XPID 2>/dev/null || true\n"
            "sleep 1\n"
            "tail -5 /tmp/e2e_xray.log\n"
            "rm -f /tmp/e2e_test.json /tmp/e2e_xray.log\n"
        )
        rc, out, err = _run(probe, test, timeout=90)
        print(out)
        if err.strip():
            print("[stderr] " + err[:400], file=sys.stderr)
        return rc
    finally:
        probe.close()


def cmd_health(args) -> int:
    cli = connect(args.host, args.user, args.key)
    try:
        cert = f" --cert-name {args.cert_name}" if args.cert_name else ""
        script = f"""
echo "=== certbot dry-run:"
sudo certbot renew --dry-run{cert} 2>&1 | grep -Ei "simulating|congratulat|failed|error|not yet due|success" | tail -3
echo "=== xray errors since start:"
sudo journalctl -u xray --since "-40 min" --no-pager 2>/dev/null | grep -ciE "\\[Error\\]|fatal|panic" || true
echo "=== live users:"
for tag in $(sudo python3 -c "
import json
cfg=json.load(open('/usr/local/etc/xray/config.json'))
print(' '.join(ib.get('tag','') for ib in cfg.get('inbounds',[]) if ib.get('protocol')=='vless'))"); do
  n=$(sudo grpcurl -plaintext -d "{{\\"tag\\":\\"$tag\\"}}" {GRPC_API} \
      xray.app.proxyman.command.HandlerService/GetInboundUsers 2>/dev/null | grep -c '"email"' || true)
  echo "$tag: $n"
done
echo "=== established conns on 443/8443:"
sudo ss -tn state established '( sport = :443 or sport = :8443 )' | tail -n +2 | wc -l
echo "=== stats (QueryStats):"
sudo grpcurl -plaintext -d '{{"pattern": ["user>>>"], "reset": false}}' {GRPC_API} \
    xray.app.stats.command.StatsService/QueryStats 2>/dev/null | head -c 200; echo
"""
        rc, out, err = _run(cli, script, timeout=180)
        print(out)
        if err.strip():
            print("[stderr] " + err[:600], file=sys.stderr)
        return rc
    finally:
        cli.close()


def grpc_count(cli, tag: str) -> int:
    req = json.dumps({"tag": tag})
    rc, out, _ = _run(
        cli,
        f"sudo grpcurl -plaintext -d {req!r} {GRPC_API} xray.app.proxyman.command.HandlerService/GetInboundUsers",
        timeout=15,
    )
    if rc != 0:
        return -1
    try:
        return len(parse_grpc_users(out))
    except Exception:
        return -1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def _common(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", required=True)
        p.add_argument("--user", default="romtan")
        p.add_argument("--key", default=_default_key())

    p = sub.add_parser("survey", help="read-only обзор бокса")
    _common(p)
    p.set_defaults(fn=cmd_survey)

    p = sub.add_parser("bundle", help="собрать бандл на источнике и скачать")
    _common(p)
    p.add_argument("--out", default="D:/tmp/mig_bundle.tgz")
    p.set_defaults(fn=cmd_bundle)

    p = sub.add_parser("prep", help="залить бандл и установить на новом боксе")
    _common(p)
    p.add_argument("--bundle", default="D:/tmp/mig_bundle.tgz")
    p.add_argument("--fw-ports", default=",".join(DEFAULT_FW_PORTS))
    p.set_defaults(fn=cmd_prep)

    p = sub.add_parser("cutover", help="persist живых юзеров со старого + start на новом")
    _common(p)
    p.add_argument("--old-host", required=True)
    p.add_argument("--old-user", default="romtan")
    p.add_argument("--old-key", default=_default_key())
    p.add_argument("--tags", required=True, help="vless inbound tags через запятую")
    p.set_defaults(fn=cmd_cutover)

    p = sub.add_parser("db-flip", help="переключить vpn_servers на новый хост (на main)")
    p.add_argument("--server-ids", required=True, help="id строк через запятую, напр. 8,9")
    p.add_argument("--new-host", required=True)
    p.set_defaults(fn=cmd_db_flip)

    p = sub.add_parser("e2e", help="end-to-end проверка ноги через новый бокс")
    _common(p)
    p.add_argument("--server-id", type=int, required=True, help="id строки vpn_servers для pbk/sid/sni")
    p.add_argument("--tag", default="", help="тег инбаунда для uuid (пусто = первый vless)")
    p.add_argument("--probe-host", required=True)
    p.add_argument("--probe-user", default="romtan")
    p.add_argument("--probe-key", default=_default_key())
    p.add_argument("--port", type=int, default=443)
    p.set_defaults(fn=cmd_e2e)

    p = sub.add_parser("health", help="certbot/journal/gRPC/conns после cutover")
    _common(p)
    p.add_argument("--cert-name", default="")
    p.set_defaults(fn=cmd_health)

    args = ap.parse_args()
    if args.cmd == "e2e" and not args.tag:
        # первый vless-тег конфига бокса
        cli = connect(args.host, args.user, args.key)
        try:
            probe = (
                f"sudo python3 -c \"import json;cfg=json.load(open('{CONFIG_PATH}'));"
                "print(next(ib['tag'] for ib in cfg.get('inbounds',[]) if ib.get('protocol')=='vless'))\""
            )
            rc, out, _ = _run(
                cli,
                probe,
                timeout=15,
            )
            args.tag = out.strip()
        finally:
            cli.close()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
