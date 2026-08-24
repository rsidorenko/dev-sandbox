"""Happ routing-profile link for /sub/.

Happ does NOT honour ``route`` rules from a full JSON config (a JSON body is
imported as a single picker-less "custom" entry — tried and rolled back
2026-08-23). Client-side routing in Happ is managed through its own *routing
profiles*, and a profile can be delivered WITH the subscription itself: as the
``routing`` HTTP response header (``happ://routing/onadd/<base64>``) or as a
line in the body. ``onadd`` adds AND activates the profile; a same-named
profile is overwritten on every subscription refresh, so the domain list
evolves server-side and clients pick it up on update.

Docs: https://www.happ.su/main/dev-docs/routing

Direct-список — RU-приложения, которым жилой IP клиента даёт лучший (или
единственный рабочий) выход, а датацентровая нога каскада — бан/детект:

* Озон — QRator блокирует хостинг-ASN на уровне сервиса (Delta AS213220
  подтверждён и на старом, и на новом LT IP, 2026-08-23); серверный сплит
  не помогает: обе ноги каскада заканчиваются хостинговой ASN.
* MAX — единая политика «MAX и его IP-echo проверки одним путём» (см.
  canonicalize_routing.py): max.ru видит жилой IP, и echo-ответы совпадают
  с ним; расхождение = детект сплита (habr 1006666).
* VK — RU-мэйджор, серверно уже идёт через RU-ногу (VK_DOMAINS на всех
  RU split-серверах); клиентски прямой выход быстрее и не светит
  датацентровый ASN.
* Яндекс.Музыка — приложение живёт на music.yandex.ru (плюс паспорт/стримы
  на поддоменах yandex.ru), медиа-CDN на yandex.net/yastatic.net; серверно
  семейство уже идёт через RU-ногу.

⚠️ Профиль глобален для приложения Happ: он действует независимо от того,
какой сервер выбран в приложении, включая CDN whitelist-инозы (surface
both/happ). Per-server скоупа механизм профилей не даёт — на практике он и
не нужен: в whitelist-режимах провайдера RU-мэйджоры (VK/MAX/Яндекс) сами
находятся в белом списке, т.е. direct-выход работает и там.

The profile is deliberately minimal: Happ fills every missing/incorrect
parameter from its default profile (per docs), so fewer keys = fewer chances
to break against future profile schema changes. ``GlobalProxy`` "true" keeps
the current behaviour (everything through the tunnel except the direct list).
"""

from __future__ import annotations

import base64
import json

# Семейство Озона: витрина/API + медиа-CDN. ``domain:`` = суффикс-матч
# (нотация v2rayNG/xray), покрывает домен и все поддомены. Это клиентская
# копия; серверный ru_services (canonicalize_routing.py) держит свою для
# direct на входной ноге — списки обслуживают разные ноги, эволюционируют
# раздельно.
_OZON_DIRECT_SITES: tuple[str, ...] = (
    "domain:ozon.ru",
    "domain:ozone.ru",
    "domain:ozonusercontent.com",
)

# MAX + его IP-echo проверки (зеркало MAX_DOMAINS + MAX_IPCHECK_DOMAINS из
# scripts/canonicalize_routing.py — держать синхронно). Одна политика: max.ru
# и все echo-эндпоинты обязаны видеть ОДИН И ТОТ ЖЕ IP, иначе MAX детектит
# сплит на расхождении ответов; клиентски это жилой IP пользователя.
_MAX_DIRECT_SITES: tuple[str, ...] = (
    "domain:max.ru",
    "domain:oneme.ru",
    "domain:okcdn.ru",
    "domain:apptracer.ru",
    "domain:api.ipify.org",
    "domain:checkip.amazonaws.com",
    "domain:ifconfig.me",
    "domain:ip.mail.ru",
    "domain:ipv4-internet.yandex.net",
    "domain:ipv6-internet.yandex.net",
)

# VK (зеркало VK_DOMAINS из scripts/canonicalize_routing.py — держать
# синхронно): сайт/приложение/авторизация + медиа-CDN. Часть доменов живёт на
# зарубежных инфраструктурах (vkvideo.com → DE/SEDO, vkuserapi.com →
# Cloudflare, vk-cdn.com → US), поэтому перечислены явно, а не «поймается
# geoip» — клиентский профиль вообще не имеет geoip-фолбэка.
_VK_DIRECT_SITES: tuple[str, ...] = (
    "domain:vk.com",
    "domain:vk.ru",
    "domain:vk.me",
    "domain:vk.org",
    "domain:vk.cc",
    "domain:vkontakte.ru",
    "domain:vkontakte.com",
    "domain:vk.link",
    "domain:vk.design",
    "domain:vk.company",
    "domain:vkcompany.com",
    "domain:vk-portal.net",
    "domain:userapi.com",
    "domain:vkuserapi.com",
    "domain:mycdn.me",
    "domain:vk-cdn.com",
    "domain:vkuservideo.net",
    "domain:vkuservideo.com",
    "domain:vkuservideo.ru",
    "domain:vkuseraudio.net",
    "domain:vkuseraudio.com",
    "domain:vkuseraudio.ru",
    "domain:vkuserlive.net",
    "domain:vkuserlive.com",
    "domain:vkuserlive.ru",
    "domain:vk.video",
    "domain:vkvideo.ru",
    "domain:vkvideo.com",
    "domain:mvk.ru",
    "domain:mvk.com",
)

# Яндекс.Музыка: суффикс yandex.ru покрывает music./passport./api./стримы;
# yandex.net + yastatic.net — медиа-CDN/статика (совпадает с серверным
# YANDEX_DOMAINS), yandex.com — зеркало портала.
_YANDEX_DIRECT_SITES: tuple[str, ...] = (
    "domain:yandex.ru",
    "domain:yandex.com",
    "domain:yandex.net",
    "domain:yastatic.net",
)

# Итоговый DirectSites профиля; порядок стабилен → payload детерминирован.
_DIRECT_SITES: tuple[str, ...] = _OZON_DIRECT_SITES + _MAX_DIRECT_SITES + _VK_DIRECT_SITES + _YANDEX_DIRECT_SITES

# Stable profile name: same-name profiles are overwritten on subscription
# update, which is exactly how list changes propagate to existing imports.
_PROFILE_NAME = "Bravada"


def build_routing_header_value() -> str:
    """``happ://routing/onadd/<base64>`` link for the ``routing`` header."""
    profile = {
        "Name": _PROFILE_NAME,
        "GlobalProxy": "true",
        "DirectSites": list(_DIRECT_SITES),
    }
    payload = json.dumps(profile, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"happ://routing/onadd/{encoded}"
