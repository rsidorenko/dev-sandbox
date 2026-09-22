"""Общий алгоритм ключей: старые ссылки неизменны до явной ротации."""

import uuid


def vless_uuid_for_transport(internal_user_id: str, transport_type: str, key_seed: str | None = None) -> str:
    name = f"vpn.bravada.internal.{internal_user_id}.{transport_type}"
    if key_seed is not None:
        name = f"vpn.bravada.rotated.{internal_user_id}.{key_seed}.{transport_type}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
