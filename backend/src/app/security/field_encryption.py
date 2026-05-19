"""AES-256-GCM encryption for sensitive fields stored in the database.

Uses a 256-bit key derived from the ``FIELD_ENCRYPTION_KEY`` environment variable.
The key must be exactly 32 bytes, base64-encoded (44 chars).

Usage::

    from app.security.field_encryption import encrypt_field, decrypt_field

    encrypted = encrypt_field("plain_password")
    original  = decrypt_field(encrypted)
"""

from __future__ import annotations

import base64
import os

_CRYPTO_AVAILABLE = True
try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    _CRYPTO_AVAILABLE = False

_ENV_KEY = "FIELD_ENCRYPTION_KEY"
_NONCE_SIZE = 12  # 96-bit nonce for AES-GCM
_PREFIX = "enc:v1:"


class EncryptionError(Exception):
    """Raised when encryption/decryption fails or key is missing."""


def _load_key() -> bytes:
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        raise EncryptionError(f"{_ENV_KEY} is not configured")
    try:
        key = base64.b64decode(raw)
    except Exception as exc:
        raise EncryptionError(f"{_ENV_KEY} is not valid base64") from exc
    if len(key) != 32:
        raise EncryptionError(f"{_ENV_KEY} must decode to exactly 32 bytes, got {len(key)}")
    return key


def is_encryption_configured() -> bool:
    """Check whether field encryption is available and configured."""
    if not _CRYPTO_AVAILABLE:
        return False
    raw = os.environ.get(_ENV_KEY, "").strip()
    if not raw:
        return False
    try:
        key = base64.b64decode(raw)
        return len(key) == 32
    except Exception:
        return False


def encrypt_field(plaintext: str) -> str:
    """Encrypt a string value. Returns ``enc:v1:<base64(nonce+ciphertext)>``."""
    if not _CRYPTO_AVAILABLE:
        raise EncryptionError("cryptography package not installed; install with: pip install cryptography")
    key = _load_key()
    nonce = os.urandom(_NONCE_SIZE)
    aesgcm = AESGCM(key)
    ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
    payload = base64.b64encode(nonce + ciphertext).decode("ascii")
    return f"{_PREFIX}{payload}"


def decrypt_field(encrypted: str) -> str:
    """Decrypt a value produced by :func:`encrypt_field`.

    If the value does not start with the encryption prefix, it is returned as-is
    (backward compatibility with plaintext values already in the database).
    """
    if not encrypted.startswith(_PREFIX):
        return encrypted
    if not _CRYPTO_AVAILABLE:
        raise EncryptionError("cryptography package not installed")
    key = _load_key()
    payload = encrypted[len(_PREFIX):]
    try:
        raw = base64.b64decode(payload)
    except Exception as exc:
        raise EncryptionError("invalid encrypted payload") from exc
    if len(raw) < _NONCE_SIZE + 16:
        raise EncryptionError("encrypted payload too short")
    nonce = raw[:_NONCE_SIZE]
    ciphertext = raw[_NONCE_SIZE:]
    aesgcm = AESGCM(key)
    try:
        plaintext = aesgcm.decrypt(nonce, ciphertext, None)
    except Exception as exc:
        raise EncryptionError("decryption failed — wrong key or corrupted data") from exc
    return plaintext.decode("utf-8")
