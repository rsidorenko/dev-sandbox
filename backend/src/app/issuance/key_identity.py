"""Общий алгоритм ключей: старые ссылки неизменны до явной ротации."""

import uuid


def key_email(internal_user_id: str, transport_type: str = "tcp", email_seed: str | None = None) -> str:
    """Раздельные счётчики поколений; старые служебные email остаются прежними."""
    prefix = "x-" if transport_type == "xhttp" else ""
    base = f"{prefix}user-{internal_user_id[:16]}"
    if email_seed is None:
        return base
    return f"{base}-g{email_seed.replace('-', '')}-t{transport_type}"


def generation_accounts(internal_user_id: str, transport_type: str, state: dict) -> dict[str, str]:
    """Допустимые поколения: старое сохраняется только до подтверждения нового трафика."""
    accounts = {
        key_email(internal_user_id, transport_type, state.get("vless_email_seed")): vless_uuid_for_transport(
            internal_user_id, transport_type, state.get("vless_key_seed")
        )
    }
    if state.get("vless_rotation_phase") in ("provisioning", "waiting"):
        accounts[key_email(internal_user_id, transport_type, state.get("vless_previous_email_seed"))] = (
            vless_uuid_for_transport(internal_user_id, transport_type, state.get("vless_previous_key_seed"))
        )
    return accounts


def vless_uuid_for_transport(internal_user_id: str, transport_type: str, key_seed: str | None = None) -> str:
    name = f"vpn.bravada.internal.{internal_user_id}.{transport_type}"
    if key_seed is not None:
        name = f"vpn.bravada.rotated.{internal_user_id}.{key_seed}.{transport_type}"
    return str(uuid.uuid5(uuid.NAMESPACE_DNS, name))
