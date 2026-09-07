"""Включение per-user статистики трафика (xray StatsService) на bare_xray-флоте.

Rollout-скрипт для коллектора traffic-stats (ветка B): на каждом bare_xray хосте
включает per-user счётчики, мержа РОВНО три ключа в /usr/local/etc/xray/config.json:

  1. ``"stats": {}``            — инстанцирует stats-модуль (без него QueryStats
                                   молча возвращает пустоту);
  2. ``policy.levels["0"]``     — ``statsUserUplink/statsUserDownlink: true`` на
                                   уровне "0" — том же, на котором весь флот
                                   провижинится (encoders шлют level=0), отдельный
                                   уровень не нужен и код провижининга не меняется;
  3. ``api.services += StatsService`` — регистрирует RPC QueryStats рядом с
                                   уже работающим HandlerService.

Идемпотентен: бокс со всеми тремя ключами = no-op (только verify). Отсутствие
api-блока — это не наш кейс (rollback-миграция): скрипт abort с ValueError.

Порядок работы per-target (скелет = canonicalize_routing.py): SSH → sudo cat →
persist живых gRPC-юзеров в settings.clients (провал = abort, рестарт не делаем)
→ merge → backup ``.pre-stats.bak`` → tee → ``xray run -test`` gate (провал =
auto-restore) → restart → verify: is-active + read-back трёх ключей + число
юзеров GetInboundUsers == до рестарта + QueryStats rc=0/валидный JSON (пустой
``stat`` сразу после рестарта — НОРМА: счётчики появляются после первого трафика).

Откат per-host: ``sudo cp config.json.pre-stats.bak config.json && sudo systemctl
restart xray`` (reconcile-таймер дозаполнит юзеров за ~3 мин).

Rollout-порядок (план, оператор согласовал): пилот YT → 24h soak → FI (первый
dual-inbound — валидирует атрибуцию email→строки) → DE/LT/US → CDN-LT/CDN-DE
ТОЛЬКО по явному подтверждению (поэтому CDN-таргеты explicit_only: без --entry
пропускаются; CDN-DE доступен по SSH только с LT/main — jump).

Usage:
    python -m scripts.enable_bare_xray_stats               # 5 entry-таргетов (CDN skip)
    python -m scripts.enable_bare_xray_stats --dry-run     # показать план, ничего не писать
    python -m scripts.enable_bare_xray_stats --entry YT    # один таргет (пилот)
    python -m scripts.enable_bare_xray_stats --entry CDN-LT  # explicit-only таргет
    python -m scripts.enable_bare_xray_stats --list        # список таргетов
"""

from __future__ import annotations

import argparse
import copy
import json
import logging
import sys
from dataclasses import dataclass

# Хелперы и константы — из канонизатора (тот же SSH/gRPC/backup-паттерн, DRY).
from scripts.canonicalize_routing import (
    GRPC_API,
    XRAY_BIN,
    RoutingTarget,
    _persist_users,
    _run,
    parse_grpc_users,
)

_LOGGER = logging.getLogger(__name__)

STATS_SERVICE_NAME = "xray.app.stats.command.StatsService"
BACKUP_SUFFIX = ".pre-stats.bak"

# per-user счётчики включаются на уровне policy "0" — как у всего 3x-ui флота.
_LEVEL0_STATS_FLAGS = ("statsUserUplink", "statsUserDownlink")


@dataclass
class StatsTarget(RoutingTarget):
    """RoutingTarget + флаг «только по явному --entry» (CDN-ноги, см. план rollout)."""

    explicit_only: bool = False


# Актуален на 2026-09-07 (fleet-state). ⚠️ НЕ копировать TARGETS из canonicalize_routing:
# там YT=158.160.82.113 (удалён 2026-08-26) и CDN-LT=158.160.40.11 (устарел) — здесь
# только текущие хосты, проверенные по cert-монитору и памяти флота.
TARGETS: list[StatsTarget] = [
    StatsTarget("DE", "85.208.87.57"),  # cloud.ru, cascade-entry, egress Frankfurt
    StatsTarget("FI", "85.208.85.117"),  # cloud.ru, cascade-entry, egress Helsinki
    StatsTarget("LT", "45.91.52.204", key="D:/tmp/romtan_key.pem"),  # Delta, cascade-entry
    StatsTarget("US", "176.109.107.226", key="D:/TelegramBotVPN/.ssh/yandex-relay"),
    StatsTarget(
        "YT",
        "93.77.165.48",  # albaniya-slava, мигрирован 2026-08-26 (старый 158.160.82.113 удалён)
        key="D:/tmp/yt_mig/new_key.pem",
    ),
    StatsTarget(
        "CDN-LT",
        "89.169.178.45",  # CDN-bypass Литва origin (id=26)
        key="D:/tmp/lt_origin_key",
        explicit_only=True,
    ),
    StatsTarget(
        "CDN-DE",  # CDN-bypass Германия origin (id=34); SSH только с LT/main (SG allowlist)
        "93.77.160.88",
        key="D:/tmp/cdn_de_new/key.pem",
        explicit_only=True,
    ),
]


# --- pure-часть (юнит-тестируется без SSH) -----------------------------------


def stats_enabled(config: dict) -> bool:
    """Все три ключа включения StatsService уже присутствуют в конфиге."""
    if not isinstance(config.get("stats"), dict):
        return False
    level0 = (config.get("policy", {}).get("levels", {}) or {}).get("0")
    if not isinstance(level0, dict):
        return False
    if not all(level0.get(flag) is True for flag in _LEVEL0_STATS_FLAGS):
        return False
    services = (config.get("api", {}) or {}).get("services")
    return isinstance(services, list) and STATS_SERVICE_NAME in services


def merge_stats_keys(config: dict) -> tuple[dict, list[str]]:
    """Pure: копия конфига с включёнными per-user счётчиками + список изменений.

    Три ключа — см. docstring модуля. Существующие настройки не затираются:
    ``stats`` не трогаем если есть; воcпроизводим level-"0" поверх остальных
    флагов уровня; StatsService аппендится к текущему списку api.services.
    ValueError — если api.services нет (HandlerService — необходимый prerequisite).
    """
    cfg = copy.deepcopy(config)
    changes: list[str] = []

    if cfg.get("stats") is None:
        cfg["stats"] = {}
        changes.append('+"stats": {}')

    levels = cfg.setdefault("policy", {}).setdefault("levels", {})
    level0 = levels.get("0")
    if not isinstance(level0, dict):
        level0 = {}
        levels["0"] = level0
    for flag in _LEVEL0_STATS_FLAGS:
        if level0.get(flag) is not True:
            level0[flag] = True
            changes.append(f"+policy.levels.0.{flag}=true")

    api = cfg.get("api")
    if not isinstance(api, dict) or not isinstance(api.get("services"), list):
        raise ValueError("no api.services in config — HandlerService api block is a prerequisite")
    if STATS_SERVICE_NAME not in api["services"]:
        api["services"] = [*api["services"], STATS_SERVICE_NAME]
        changes.append("+api.services+=StatsService")

    return cfg, changes


def vless_inbound_tags(config: dict) -> list[str]:
    """Теги vless-инбаундов (api-инбаунд не считается)."""
    return [
        ib.get("tag") for ib in config.get("inbounds", []) if ib.get("protocol") == "vless" and ib.get("tag") != "api"
    ]


def configured_user_counts(config: dict) -> dict[str, int]:
    """Число юзеров per vless-инбаунд по settings.clients (после persist — эталон
    для сверки после рестарта)."""
    out: dict[str, int] = {}
    for ib in config.get("inbounds", []):
        if ib.get("protocol") == "vless" and ib.get("tag") != "api":
            clients = (ib.get("settings") or {}).get("clients") or []
            out[ib.get("tag")] = len(clients)
    return out


def query_stats_command(pattern: str = "user>>>") -> str:
    """grpcurl-команда QueryStats. ``req`` уже JSON-строка — !r даёт один уровень
    кодирования (двойной json.dumps ломает grpcurl, см. canonicalize_routing)."""
    req = json.dumps({"pattern": [pattern], "reset": False})
    return f"sudo grpcurl -plaintext -d {req!r} {GRPC_API} {STATS_SERVICE_NAME}/QueryStats"


def get_inbound_users_command(tag: str) -> str:
    req = json.dumps({"tag": tag})
    return f"sudo grpcurl -plaintext -d {req!r} {GRPC_API} xray.app.proxyman.command.HandlerService/GetInboundUsers"


# --- SSH/gRPC-часть ------------------------------------------------------------


def grpc_user_count(cli, tag: str) -> int | None:
    """Живое число юзеров инбаунда (None = gRPC-сбой/мусор в ответе)."""
    rc, out, _ = _run(cli, get_inbound_users_command(tag), timeout=15)
    if rc != 0 or not out.strip():
        return None
    try:
        return len(parse_grpc_users(out))
    except Exception:
        return None


def verify_query_stats(cli) -> tuple[bool, str]:
    """QueryStats rc=0 + валидный JSON. Пустой stat — норма сразу после рестарта."""
    rc, out, err = _run(cli, query_stats_command(), timeout=15)
    if rc != 0:
        return False, f"QueryStats rc={rc}: {err.strip()[:120]}"
    try:
        data = json.loads(out)
    except json.JSONDecodeError:
        return False, f"QueryStats non-JSON: {out.strip()[:120]}"
    return True, f"QueryStats ok (stat entries={len(data.get('stat', []))}; 0 сразу после рестарта — норма)"


def _restore_backup_and_restart(cli, target: StatsTarget) -> bool:
    """Откат: восстановить .pre-stats.bak и перезапустить xray. True если активен."""
    _run(cli, f"sudo cp {target.config}{BACKUP_SUFFIX} {target.config}")
    _run(cli, "sudo systemctl restart xray")
    rc, active, _ = _run(cli, "systemctl is-active xray")
    return rc == 0 and active.strip() == "active"


def apply_to_target(target: StatsTarget, *, dry_run: bool) -> bool:
    """SSH → persist users → merge трёх ключей → backup → test → restart → verify.

    Safe by construction: persist-users до рестарта (юзеров не теряем), test-gate
    с auto-restore, после рестарта — сверка read-back/юзеров/QueryStats. Если после
    рестарта xray не активен — авто-откат бэкапа. Уже-включённый бокс = verify-only.
    """
    import paramiko

    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        cli.connect(
            target.host,
            port=22,
            username=target.user,
            key_filename=target.key,
            look_for_keys=False,
            allow_agent=False,
            timeout=15,
        )
    except Exception as exc:
        print(f"[{target.name}] SSH connect FAILED: {exc!r}", file=sys.stderr)
        return False

    try:
        rc, cur_json, err = _run(cli, f"sudo cat {target.config}")
        if rc != 0:
            print(f"[{target.name}] read config FAILED rc={rc}: {err.strip()[:200]}", file=sys.stderr)
            return False
        try:
            config = json.loads(cur_json)
        except json.JSONDecodeError as exc:
            print(f"[{target.name}] config parse FAILED: {exc!r}", file=sys.stderr)
            return False

        if stats_enabled(config):
            rc, active, _ = _run(cli, "systemctl is-active xray")
            ok, msg = verify_query_stats(cli)
            ok = ok and rc == 0 and active.strip() == "active"
            print(
                f"[{target.name}] {'OK' if ok else 'VERIFY-WARN'} (already enabled, no-op): xray={active.strip()} {msg}"
            )
            return ok

        try:
            _, preview = merge_stats_keys(config)
        except ValueError as exc:
            print(f"[{target.name}] ABORT: {exc}", file=sys.stderr)
            return False

        if dry_run:
            print(
                f"[{target.name}] DRY-RUN: would merge {len(preview)} key(s): "
                f"{'; '.join(preview)}; restart required "
                f"(vless inbounds={len(vless_inbound_tags(config))})"
            )
            return True

        # живые gRPC-юзеры — в settings.clients, чтобы рестарт их не снёс
        persisted, persist_failed = _persist_users(cli, config)
        if persist_failed:
            print(
                f"[{target.name}] ABORT: gRPC persist failed on {persist_failed} inbound(s); no write, no restart",
                file=sys.stderr,
            )
            return False

        try:
            merged, changes = merge_stats_keys(config)
        except ValueError as exc:  # pragma: no cover - preview уже прогнал этот кейс
            print(f"[{target.name}] ABORT: {exc}", file=sys.stderr)
            return False

        counts_before = configured_user_counts(merged)

        # backup + write + test (auto-restore on failure)
        _run(cli, f"sudo cp {target.config} {target.config}{BACKUP_SUFFIX}")
        _run(
            cli,
            f"sudo tee {target.config} > /dev/null <<'__CFG__'\n"
            f"{json.dumps(merged, ensure_ascii=False, indent=2)}\n__CFG__",
        )
        rc, _, test_err = _run(cli, f"sudo {XRAY_BIN} run -test -config {target.config}")
        if rc != 0:
            _run(cli, f"sudo cp {target.config}{BACKUP_SUFFIX} {target.config}")
            print(f"[{target.name}] xray test FAILED (restored backup): {test_err.strip()[:300]}", file=sys.stderr)
            return False

        # restart
        _run(cli, "sudo systemctl restart xray")
        rc, active, _ = _run(cli, "systemctl is-active xray")
        if rc != 0 or active.strip() != "active":
            restored = _restore_backup_and_restart(cli, target)
            print(
                f"[{target.name}] FAILED: xray not active after restart; "
                f"backup restore attempted -> {'active (rolled back)' if restored else 'STILL DOWN — manual action needed'}",
                file=sys.stderr,
            )
            return False

        # verify: read-back трёх ключей
        rc, rb_json, _ = _run(cli, f"sudo cat {target.config}")
        rb_ok = rc == 0
        if rb_ok:
            try:
                rb_ok = stats_enabled(json.loads(rb_json))
            except json.JSONDecodeError:
                rb_ok = False

        # verify: юзеры пережили рестарт (in-memory восстановлены из конфига)
        user_problems: list[str] = []
        for tag, before in counts_before.items():
            after = grpc_user_count(cli, tag)
            if after != before:
                user_problems.append(f"{tag}: {before}->{after}")

        query_ok, query_msg = verify_query_stats(cli)

        ok = rb_ok and not user_problems and query_ok
        detail = f"readback3keys={rb_ok} users={'ok' if not user_problems else ','.join(user_problems)}"
        print(
            f"[{target.name}] {'OK' if ok else 'VERIFY-WARN'}: xray={active.strip()} "
            f"changes[{len(changes)}]={'; '.join(changes)} persisted={persisted} {detail} {query_msg}"
        )
        if user_problems:
            print(
                f"[{target.name}] NOTE: reconcile-таймер дозаполнит расхождения за ~3 мин "
                f"(bare_xray reconcile каждые 3 мин); откат — restore {BACKUP_SUFFIX}",
                file=sys.stderr,
            )
        return ok
    finally:
        cli.close()


def main() -> int:
    logging.basicConfig(level=logging.WARNING, format="%(message)s")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true", help="показать план мержа, ничего не писать")
    ap.add_argument("--entry", default="", help="применить только к таргетам, чьё имя содержит это")
    ap.add_argument("--list", action="store_true", help="список таргетов и выход")
    args = ap.parse_args()

    if args.list:
        for t in TARGETS:
            flag = "  [explicit-only]" if t.explicit_only else ""
            print(f"{t.name:8} {t.host}  key={t.key}{flag}")
        return 0

    # точное имя выигрывает ("DE" не должен задевать "CDN-DE"); потом substring
    targets = [t for t in TARGETS if t.name.lower() == args.entry.lower()]
    if not targets:
        targets = [t for t in TARGETS if args.entry.lower() in t.name.lower()]
    if not targets:
        print(f"no target matches --entry '{args.entry}'", file=sys.stderr)
        return 2

    if not args.entry:
        # CDN-ноги (explicit_only) без явного --entry не трогаем — план rollout:
        # пилот YT → флот → CDN только по подтверждению оператора.
        skipped = [t.name for t in targets if t.explicit_only]
        targets = [t for t in targets if not t.explicit_only]
        if skipped:
            print(f"skipped (explicit-only, требуют --entry): {', '.join(skipped)}")

    ok = True
    for target in targets:
        ok = apply_to_target(target, dry_run=args.dry_run) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
