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

We use it to route the Ozon app family DIRECT from the subscriber's own ISP
IP: Ozon/QRator blocks hosting ASNs at the service level (Delta AS213220
confirmed on both its old and fresh IPs, 2026-08-23), so the only reliable
exit is the user's residential IP — routed around the tunnel on the client.
Server-side split routing cannot help: both legs of the cascade end in a
hosting ASN.

The profile is deliberately minimal: Happ fills every missing/incorrect
parameter from its default profile (per docs), so fewer keys = fewer chances
to break against future profile schema changes. ``GlobalProxy`` "true" keeps
the current behaviour (everything through the tunnel except the direct list).
"""

from __future__ import annotations

import base64
import json

# Ozon app family: storefront/API zones + the media CDN. Client-side only — the
# server-side ru_services list (canonicalize_routing.py) keeps its own copy for
# entry-side direct routing; the two serve different legs and evolve separately.
# ``domain:`` = suffix match (v2rayNG/xray notation), covers all subdomains.
_OZON_DIRECT_SITES: tuple[str, ...] = (
    "domain:ozon.ru",
    "domain:ozone.ru",
    "domain:ozonusercontent.com",
)

# Stable profile name: same-name profiles are overwritten on subscription
# update, which is exactly how list changes propagate to existing imports.
_PROFILE_NAME = "Bravada"


def build_routing_header_value() -> str:
    """``happ://routing/onadd/<base64>`` link for the ``routing`` header."""
    profile = {
        "Name": _PROFILE_NAME,
        "GlobalProxy": "true",
        "DirectSites": list(_OZON_DIRECT_SITES),
    }
    payload = json.dumps(profile, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    encoded = base64.b64encode(payload).decode("ascii")
    return f"happ://routing/onadd/{encoded}"
