"""Настроить автовыбор Xray на существующем entry-сервере.

Инструмент не содержит адресов и UUID межсерверных соединений. Они передаются
через локальный JSON-файл с правами 0600. По умолчанию выполняется только dry-run.

Примеры:
    python -m scripts.configure_xray_auto_selection --config /secure/auto.json \
        --profile normal
    python -m scripts.configure_xray_auto_selection --config /secure/auto.json \
        --profile normal --apply --publish
"""

from __future__ import annotations

import argparse
import asyncio
import copy
import json
import os
import re
import shlex
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from scripts.bare_xray_provision import connect
from scripts.canonicalize_routing import _persist_users, _run

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,47}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9_.:-]+$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.:-]+$")
_USER_RE = re.compile(r"^[A-Za-z0-9_.-]+$")
_PATH_RE = re.compile(r"^/[A-Za-z0-9_./-]+$")
_INTERVAL_RE = re.compile(r"^[1-9][0-9]*[smh]$")
_MATCH_FIELDS = {
    "domain",
    "domains",
    "ip",
    "port",
    "source",
    "sourceIP",
    "sourcePort",
    "protocol",
    "attrs",
    "user",
    "vlessRoute",
}

_BACKUP_SUFFIX = ".pre-auto-selection.bak"
_DEFAULT_CONFIG_PATH = "/usr/local/etc/xray/config.json"
_DEFAULT_XRAY_BIN = "/usr/local/bin/xray"


@dataclass(frozen=True, slots=True)
class PublishSettings:
    server_ids: tuple[int, ...]
    manual_label: str
    manual_country_flag: str
    manual_country_code: str
    auto_label: str
    auto_country_flag: str
    auto_country_code: str
    surface: str


@dataclass(frozen=True, slots=True)
class AutoSelectionProfile:
    name: str
    host: str
    user: str
    key_path: str
    inbound_tags: tuple[str, ...]
    replace_outbound_tags: tuple[str, ...]
    vless_route: int
    outbound_prefix: str
    balancer_tag: str
    fallback_tag: str
    outbounds: tuple[dict[str, Any], ...]
    probe_url: str
    probe_interval: str
    config_path: str
    xray_bin: str
    service_name: str
    publish: PublishSettings | None


def _required_str(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"поле {field!r} должно быть непустой строкой")
    return value.strip()


def _string_tuple(data: dict[str, Any], field: str) -> tuple[str, ...]:
    value = data.get(field)
    if not isinstance(value, list) or not value or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"поле {field!r} должно быть непустым массивом строк")
    result = tuple(value)
    if len(set(result)) != len(result):
        raise ValueError(f"поле {field!r} содержит дубликаты")
    return result


def _parse_publish(data: Any) -> PublishSettings | None:
    if data is None:
        return None
    if not isinstance(data, dict):
        raise ValueError("поле 'publish' должно быть объектом")
    raw_ids = data.get("server_ids")
    if (
        not isinstance(raw_ids, list)
        or not raw_ids
        or any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in raw_ids)
    ):
        raise ValueError("publish.server_ids должен содержать положительные целые числа")
    if len(set(raw_ids)) != len(raw_ids):
        raise ValueError("publish.server_ids содержит дубликаты")
    surface = _required_str(data, "surface")
    if surface not in {"default", "happ", "both"}:
        raise ValueError("publish.surface должен быть default, happ или both")
    manual_label = _required_str(data, "manual_label")
    manual_country_flag = _required_str(data, "manual_country_flag")
    auto_label = _required_str(data, "auto_label")
    auto_country_flag = _required_str(data, "auto_country_flag")
    manual_country_code = data.get("manual_country_code", "")
    auto_country_code = data.get("auto_country_code", "")
    if not isinstance(manual_country_code, str):
        raise ValueError("publish.manual_country_code должен быть строкой")
    if not isinstance(auto_country_code, str):
        raise ValueError("publish.auto_country_code должен быть строкой")
    if manual_label == auto_label:
        raise ValueError("publish manual_label и auto_label должны различаться")
    return PublishSettings(
        server_ids=tuple(raw_ids),
        manual_label=manual_label,
        manual_country_flag=manual_country_flag,
        manual_country_code=manual_country_code,
        auto_label=auto_label,
        auto_country_flag=auto_country_flag,
        auto_country_code=auto_country_code,
        surface=surface,
    )


def parse_profile(data: dict[str, Any]) -> AutoSelectionProfile:
    """Разобрать и строго проверить один профиль до любых внешних операций."""
    if not isinstance(data, dict):
        raise ValueError("профиль должен быть JSON-объектом")

    name = _required_str(data, "name")
    host = _required_str(data, "host")
    user = _required_str(data, "user")
    key_path = _required_str(data, "key_path")
    inbound_tags = _string_tuple(data, "inbound_tags")
    replace_outbound_tags = _string_tuple(data, "replace_outbound_tags")
    vless_route = data.get("vless_route")
    outbound_prefix = _required_str(data, "outbound_prefix")
    balancer_tag = _required_str(data, "balancer_tag")
    fallback_tag = _required_str(data, "fallback_tag")
    probe_url = data.get("probe_url", "https://www.gstatic.com/generate_204")
    probe_interval = data.get("probe_interval", "30s")
    config_path = data.get("config_path", _DEFAULT_CONFIG_PATH)
    xray_bin = data.get("xray_bin", _DEFAULT_XRAY_BIN)
    service_name = data.get("service_name", "xray")

    if not _NAME_RE.fullmatch(name):
        raise ValueError("name должен соответствовать [a-z0-9-] и иметь длину 2..48")
    if not _HOST_RE.fullmatch(host):
        raise ValueError("host содержит недопустимые символы")
    if not _USER_RE.fullmatch(user):
        raise ValueError("user содержит недопустимые символы")
    if not Path(key_path).is_absolute():
        raise ValueError("key_path должен быть абсолютным")
    for field_name, value in (
        ("outbound_prefix", outbound_prefix),
        ("balancer_tag", balancer_tag),
        ("service_name", service_name),
        *(("inbound_tags", tag) for tag in inbound_tags),
        *(("replace_outbound_tags", tag) for tag in replace_outbound_tags),
    ):
        if not _TAG_RE.fullmatch(value):
            raise ValueError(f"{field_name} содержит недопустимые символы: {value!r}")
    if outbound_prefix in {"direct", "blocked", "api"} or not outbound_prefix.endswith("-"):
        raise ValueError("outbound_prefix должен быть отдельным пространством тегов и оканчиваться на '-'")
    if balancer_tag.startswith(outbound_prefix):
        raise ValueError("balancer_tag не должен попадать под outbound_prefix")
    if "api" in inbound_tags:
        raise ValueError("служебный inbound 'api' нельзя направлять в автовыбор")
    if not isinstance(vless_route, int) or isinstance(vless_route, bool) or not 61440 <= vless_route <= 65535:
        raise ValueError("vless_route должен быть целым числом 61440..65535")
    if not isinstance(probe_url, str) or not probe_url.startswith("https://"):
        raise ValueError("probe_url должен быть HTTPS URL")
    if not isinstance(probe_interval, str) or not _INTERVAL_RE.fullmatch(probe_interval):
        raise ValueError("probe_interval должен иметь вид 30s, 2m или 1h")
    for field_name, value in (("config_path", config_path), ("xray_bin", xray_bin)):
        if not isinstance(value, str) or not _PATH_RE.fullmatch(value):
            raise ValueError(f"{field_name} должен быть безопасным абсолютным путём")

    raw_outbounds = data.get("outbounds")
    if not isinstance(raw_outbounds, list) or len(raw_outbounds) < 2:
        raise ValueError("для автовыбора нужны минимум два outbound")
    outbounds: list[dict[str, Any]] = []
    tags: list[str] = []
    for item in raw_outbounds:
        if not isinstance(item, dict):
            raise ValueError("каждый outbound должен быть объектом")
        tag = item.get("tag")
        if not isinstance(tag, str) or not tag.startswith(outbound_prefix) or not _TAG_RE.fullmatch(tag):
            raise ValueError(f"тег outbound должен начинаться с {outbound_prefix!r}")
        if item.get("protocol") != "vless":
            raise ValueError(f"outbound {tag!r} должен использовать протокол vless")
        tags.append(tag)
        outbounds.append(copy.deepcopy(item))
    if len(tags) != len(set(tags)):
        raise ValueError("теги outbound должны быть уникальными")
    if fallback_tag not in tags:
        raise ValueError("fallback_tag должен указывать на outbound из этого профиля")

    return AutoSelectionProfile(
        name=name,
        host=host,
        user=user,
        key_path=key_path,
        inbound_tags=inbound_tags,
        replace_outbound_tags=replace_outbound_tags,
        vless_route=vless_route,
        outbound_prefix=outbound_prefix,
        balancer_tag=balancer_tag,
        fallback_tag=fallback_tag,
        outbounds=tuple(outbounds),
        probe_url=probe_url,
        probe_interval=probe_interval,
        config_path=config_path,
        xray_bin=xray_bin,
        service_name=service_name,
        publish=_parse_publish(data.get("publish")),
    )


def load_profile(path: Path, profile_name: str) -> AutoSelectionProfile:
    """Загрузить профиль из закрытого операторского файла."""
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o077:
        raise ValueError(f"файл {path} должен иметь права 0600 или строже")
    root = json.loads(path.read_text(encoding="utf-8"))
    profiles = root.get("profiles") if isinstance(root, dict) else None
    if not isinstance(profiles, list):
        raise ValueError("корень файла должен содержать массив 'profiles'")
    matches = [item for item in profiles if isinstance(item, dict) and item.get("name") == profile_name]
    if len(matches) != 1:
        raise ValueError(f"ожидался ровно один профиль {profile_name!r}, найдено: {len(matches)}")
    return parse_profile(matches[0])


def _is_terminal_rule(rule: dict[str, Any]) -> bool:
    """Правило без условий назначения является catch-all для своих inbound."""
    return not any(rule.get(field) for field in _MATCH_FIELDS)


def merge_auto_selection(config: dict[str, Any], profile: AutoSelectionProfile) -> dict[str, Any]:
    """Идемпотентно добавить один leastPing-профиль, сохранив чужой конфиг."""
    result = copy.deepcopy(config)
    inbounds = result.get("inbounds")
    if not isinstance(inbounds, list):
        raise ValueError("Xray-конфиг не содержит массива inbounds")
    known_inbounds = {item.get("tag") for item in inbounds if isinstance(item, dict)}
    missing = set(profile.inbound_tags) - known_inbounds
    if missing:
        raise ValueError(f"в Xray-конфиге отсутствуют inbound: {', '.join(sorted(missing))}")

    outbounds = result.setdefault("outbounds", [])
    if not isinstance(outbounds, list):
        raise ValueError("Xray-конфиг содержит некорректный outbounds")
    outbounds[:] = [
        item
        for item in outbounds
        if not (isinstance(item, dict) and str(item.get("tag", "")).startswith(profile.outbound_prefix))
    ]
    outbounds.extend(copy.deepcopy(list(profile.outbounds)))

    routing = result.setdefault("routing", {})
    if not isinstance(routing, dict):
        raise ValueError("Xray-конфиг содержит некорректный routing")
    balancers = routing.setdefault("balancers", [])
    if not isinstance(balancers, list):
        raise ValueError("Xray-конфиг содержит некорректный routing.balancers")
    balancers[:] = [
        item for item in balancers if not (isinstance(item, dict) and item.get("tag") == profile.balancer_tag)
    ]
    balancers.append(
        {
            "tag": profile.balancer_tag,
            "selector": [profile.outbound_prefix],
            "fallbackTag": profile.fallback_tag,
            "strategy": {"type": "leastPing"},
        }
    )

    rules = routing.setdefault("rules", [])
    if not isinstance(rules, list):
        raise ValueError("Xray-конфиг содержит некорректный routing.rules")
    kept_rules: list[dict[str, Any]] = []
    insert_at: int | None = None
    for source_rule in rules:
        if not isinstance(source_rule, dict):
            kept_rules.append(source_rule)
            continue
        rule = copy.deepcopy(source_rule)
        # Удаляем и новые route-scoped, и старые inbound-scoped правила профиля.
        if rule.get("balancerTag") == profile.balancer_tag:
            continue
        if rule.get("outboundTag") in profile.replace_outbound_tags:
            if not _is_terminal_rule(rule):
                scoped = copy.deepcopy(rule)
                scoped.pop("outboundTag", None)
                scoped["vlessRoute"] = str(profile.vless_route)
                scoped["balancerTag"] = profile.balancer_tag
                kept_rules.append(scoped)
        if _is_terminal_rule(rule) and insert_at is None:
            insert_at = len(kept_rules)
        kept_rules.append(rule)
    auto_rule = {
        "type": "field",
        "vlessRoute": str(profile.vless_route),
        "network": "tcp,udp",
        "balancerTag": profile.balancer_tag,
    }
    if insert_at is None:
        kept_rules.append(auto_rule)
    else:
        kept_rules.insert(insert_at, auto_rule)
    routing["rules"] = kept_rules

    observatory = result.setdefault("observatory", {})
    if not isinstance(observatory, dict):
        raise ValueError("Xray-конфиг содержит некорректный observatory")
    selectors = observatory.setdefault("subjectSelector", [])
    if not isinstance(selectors, list) or not all(isinstance(item, str) for item in selectors):
        raise ValueError("Xray-конфиг содержит некорректный observatory.subjectSelector")
    if profile.outbound_prefix not in selectors:
        selectors.append(profile.outbound_prefix)
    observatory.setdefault("probeUrl", profile.probe_url)
    observatory.setdefault("probeInterval", profile.probe_interval)
    observatory["enableConcurrency"] = True
    return result


def profile_summary(profile: AutoSelectionProfile) -> str:
    """Безопасное описание без адресов и credentials outbound."""
    published = len(profile.publish.server_ids) if profile.publish else 0
    return (
        f"profile={profile.name} host={profile.host} inbounds={','.join(profile.inbound_tags)} "
        f"replace={','.join(profile.replace_outbound_tags)} route={profile.vless_route} "
        f"candidates={len(profile.outbounds)} "
        f"fallback={profile.fallback_tag} publish_rows={published}"
    )


def _restore(cli: Any, profile: AutoSelectionProfile, backup_path: str) -> bool:
    config_q = shlex.quote(profile.config_path)
    backup_q = shlex.quote(backup_path)
    service_q = shlex.quote(profile.service_name)
    _run(cli, f"sudo cp {backup_q} {config_q}")
    _run(cli, f"sudo systemctl restart {service_q}")
    rc, active, _ = _run(cli, f"systemctl is-active {service_q}")
    return rc == 0 and active.strip() == "active"


def apply_profile(profile: AutoSelectionProfile, *, dry_run: bool) -> bool:
    """Проверить или безопасно применить профиль по SSH."""
    try:
        cli = connect(profile.host, profile.user, profile.key_path)
    except Exception as exc:
        print(f"[{profile.name}] SSH-подключение к {profile.host} не удалось: {exc!r}", file=sys.stderr)
        return False
    config_q = shlex.quote(profile.config_path)
    xray_q = shlex.quote(profile.xray_bin)
    service_q = shlex.quote(profile.service_name)
    backup_path = f"{profile.config_path}{_BACKUP_SUFFIX}"
    backup_q = shlex.quote(backup_path)
    remote_tmp = f"/tmp/xray-auto-selection-{profile.name}.json"
    tmp_q = shlex.quote(remote_tmp)
    try:
        rc, raw_config, err = _run(cli, f"sudo cat {config_q}")
        if rc != 0:
            print(f"[{profile.name}] чтение Xray-конфига не удалось: {err.strip()[:200]}", file=sys.stderr)
            return False
        try:
            current = json.loads(raw_config)
            merged = merge_auto_selection(current, profile)
        except (json.JSONDecodeError, ValueError) as exc:
            print(f"[{profile.name}] конфигурация отклонена: {exc}", file=sys.stderr)
            return False

        changed = current != merged
        print(f"[{profile.name}] {'ИЗМЕНИТСЯ' if changed else 'уже настроен'}; {profile_summary(profile)}")
        if dry_run:
            return True

        persisted, persist_failed = _persist_users(cli, current)
        if persist_failed:
            print(
                f"[{profile.name}] сохранение live-users не удалось для {persist_failed} inbound; применение отменено",
                file=sys.stderr,
            )
            return False
        merged = merge_auto_selection(current, profile)
        payload = json.dumps(merged, ensure_ascii=False, indent=2)
        sftp = cli.open_sftp()
        try:
            with sftp.open(remote_tmp, "w") as handle:
                handle.write(payload)
        finally:
            sftp.close()

        rc, _, test_err = _run(cli, f"sudo {xray_q} run -test -config {tmp_q}", timeout=60)
        if rc != 0:
            _run(cli, f"rm -f {tmp_q}")
            print(f"[{profile.name}] xray test не прошёл: {test_err.strip()[:300]}", file=sys.stderr)
            return False

        rc, _, err = _run(
            cli,
            f"sudo cp {config_q} {backup_q} && sudo install -m 600 {tmp_q} {config_q} && rm -f {tmp_q}",
        )
        if rc != 0:
            print(f"[{profile.name}] установка конфига не удалась: {err.strip()[:200]}", file=sys.stderr)
            return False

        _run(cli, f"sudo systemctl restart {service_q}")
        rc, active, _ = _run(cli, f"systemctl is-active {service_q}")
        if rc != 0 or active.strip() != "active":
            restored = _restore(cli, profile, backup_path)
            print(
                f"[{profile.name}] Xray не запустился; backup {'восстановлен' if restored else 'НЕ восстановлен'}",
                file=sys.stderr,
            )
            return False
        print(f"[{profile.name}] OK: xray=active, users_saved={persisted}, backup={backup_path}")
        return True
    finally:
        cli.close()


async def publish_profile(pool: Any, profile: AutoSelectionProfile) -> list[dict[str, Any]]:
    """Восстановить ручную строку и включить рядом дополнительный auto alias."""
    settings = profile.publish
    if settings is None:
        raise ValueError("профиль не содержит секцию publish")
    manual_fragment = quote(f"{settings.manual_country_flag} {settings.manual_label}", safe="")
    async with pool.acquire() as connection, connection.transaction():
        rows = await connection.fetch(
            """UPDATE vpn_servers
               SET label = $1,
                   country_flag = $2,
                   country_code = $3,
                   client_surface = $4,
                   auto_selection_route = $5,
                   auto_selection_label = $6,
                   auto_selection_country_flag = $7,
                   auto_selection_country_code = $8,
                   link_template = CASE
                       WHEN COALESCE(link_template, '') = '' THEN link_template
                       ELSE split_part(link_template, '#', 1) || '#' || $10
                   END
               WHERE id = ANY($9::int[])
                 AND is_active = TRUE
                 AND provider_kind = 'bare_xray'
               RETURNING id, label, country_flag, country_code, client_surface,
                         auto_selection_route, auto_selection_label,
                         auto_selection_country_flag, auto_selection_country_code""",
            settings.manual_label,
            settings.manual_country_flag,
            settings.manual_country_code,
            settings.surface,
            profile.vless_route,
            settings.auto_label,
            settings.auto_country_flag,
            settings.auto_country_code,
            list(settings.server_ids),
            manual_fragment,
        )
        if len(rows) != len(settings.server_ids):
            raise RuntimeError(f"ожидалось строк vpn_servers: {len(settings.server_ids)}, обновлено: {len(rows)}")
        return [dict(row) for row in rows]


async def _publish_from_env(profile: AutoSelectionProfile) -> None:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("для --publish требуется DATABASE_URL")
    import asyncpg

    pool = await asyncpg.create_pool(database_url, min_size=1, max_size=2)
    try:
        rows = await publish_profile(pool, profile)
    finally:
        await pool.close()
    print(f"[{profile.name}] опубликовано строк vpn_servers: {len(rows)}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--config", required=True, type=Path, help="локальный JSON-файл с правами 0600")
    parser.add_argument("--profile", required=True, help="имя профиля из массива profiles")
    parser.add_argument("--apply", action="store_true", help="применить; без флага выполняется dry-run")
    parser.add_argument("--publish", action="store_true", help="после успешного apply обновить vpn_servers")
    args = parser.parse_args(argv)
    if args.publish and not args.apply:
        parser.error("--publish разрешён только вместе с --apply")

    try:
        profile = load_profile(args.config, args.profile)
        ok = apply_profile(profile, dry_run=not args.apply)
        if not ok:
            return 1
        if args.publish:
            asyncio.run(_publish_from_env(profile))
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
