"""Default raw-update bridge: accept mapping-shaped updates only (no copy, no validation)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast


def accept_mapping_runtime_update(raw_update: object) -> Mapping[str, object] | None:
    if isinstance(raw_update, Mapping):
        return cast("Mapping[str, object]", raw_update)
    return None
