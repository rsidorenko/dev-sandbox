"""UC-01: построение ключа идемпотентности: детерминированное, ограниченное, без персистентности."""

from __future__ import annotations

import hashlib

from app.security.validation import (
    ValidationError,
    validate_telegram_update_id,
    validate_telegram_user_id,
)

_UC01_SCOPE = "uc01_bootstrap_identity"
_MAX_KEY_HEX_LEN = 128


def build_bootstrap_idempotency_key(telegram_user_id: int, update_id: int) -> str:
    """
    Строит детерминированный ключ идемпотентности из безопасных нормализованных входов.

    Секреты не встраиваются; материал ключа хешируется для стабильной длины и сравнения.
    """
    uid = validate_telegram_user_id(telegram_user_id)
    u = validate_telegram_update_id(update_id)
    if u == 0:
        raise ValidationError("update_id must be positive for idempotency key")
    material = f"{_UC01_SCOPE}|{uid}|{u}".encode()
    digest = hashlib.sha256(material).hexdigest()
    if len(digest) > _MAX_KEY_HEX_LEN:
        raise ValidationError("idempotency key material exceeds bounds")
    return digest
