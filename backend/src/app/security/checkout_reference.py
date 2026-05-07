"""Помощники подписанных checkout-ссылок (провайдер-независимые, HMAC SHA-256)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime

from app.security.validation import ValidationError, validate_telegram_user_id

_SCHEMA_VERSION = 1
_MAX_REFERENCE_ID_LEN = 2048
_MAX_REFERENCE_PROOF_LEN = 256
DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
DEFAULT_CHECKOUT_REFERENCE_MAX_FUTURE_SECONDS = 5 * 60


@dataclass(frozen=True, slots=True)
class CheckoutReferencePayload:
    schema_version: int
    issued_at: str
    telegram_user_id: int
    internal_user_id: str | None


@dataclass(frozen=True, slots=True)
class SignedCheckoutReference:
    reference_id: str
    reference_proof: str
    payload: CheckoutReferencePayload


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(encoded: str) -> bytes:
    pad = "=" * ((4 - len(encoded) % 4) % 4)
    return base64.urlsafe_b64decode((encoded + pad).encode("ascii"))


def _normalize_internal_user_id(value: str | None) -> str | None:
    if value is None:
        return None
    trimmed = value.strip()
    return trimmed or None


def _signature_hex(secret: str, reference_id: str) -> str:
    mac = hmac.new(secret.encode("utf-8"), reference_id.encode("utf-8"), hashlib.sha256)
    return mac.hexdigest()


def create_signed_checkout_reference(
    *,
    telegram_user_id: int,
    internal_user_id: str | None,
    secret: str,
    now: datetime | None = None,
) -> SignedCheckoutReference:
    """Создаёт подписанную референс-ссылку для корреляции клиента в метаданных checkout."""
    normalized_user_id = validate_telegram_user_id(telegram_user_id)
    normalized_internal = _normalize_internal_user_id(internal_user_id)
    dt = now or datetime.now(UTC)
    issued_at = dt.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    payload_dict: dict[str, object] = {
        "schema_version": _SCHEMA_VERSION,
        "issued_at": issued_at,
        "telegram_user_id": normalized_user_id,
    }
    if normalized_internal is not None:
        payload_dict["internal_user_id"] = normalized_internal
    raw_payload = json.dumps(payload_dict, separators=(",", ":"), sort_keys=True).encode("utf-8")
    reference_id = _b64url_encode(raw_payload)
    reference_proof = _signature_hex(secret, reference_id)
    return SignedCheckoutReference(
        reference_id=reference_id,
        reference_proof=reference_proof,
        payload=CheckoutReferencePayload(
            schema_version=_SCHEMA_VERSION,
            issued_at=issued_at,
            telegram_user_id=normalized_user_id,
            internal_user_id=normalized_internal,
        ),
    )


def verify_signed_checkout_reference(
    *,
    reference_id: str,
    reference_proof: str,
    secret: str,
    now: datetime | None = None,
    max_age_seconds: int = DEFAULT_CHECKOUT_REFERENCE_MAX_AGE_SECONDS,
    max_future_seconds: int = DEFAULT_CHECKOUT_REFERENCE_MAX_FUTURE_SECONDS,
) -> CheckoutReferencePayload:
    """Проверяет и декодирует checkout-референс (сравнение proof в постоянное время)."""
    if max_age_seconds <= 0:
        raise ValidationError("checkout reference max age must be positive")
    if max_future_seconds < 0:
        raise ValidationError("checkout reference max future skew must be non-negative")
    ref_id = reference_id.strip()
    ref_proof = reference_proof.strip().lower()
    if not ref_id:
        raise ValidationError("client_reference_id is required")
    if len(ref_id) > _MAX_REFERENCE_ID_LEN:
        raise ValidationError("client_reference_id exceeds maximum length")
    if not ref_proof:
        raise ValidationError("client_reference_proof is required")
    if len(ref_proof) > _MAX_REFERENCE_PROOF_LEN:
        raise ValidationError("client_reference_proof exceeds maximum length")
    expected = _signature_hex(secret, ref_id)
    if not hmac.compare_digest(ref_proof, expected):
        raise ValidationError("client_reference_proof is invalid")
    try:
        payload_obj = json.loads(_b64url_decode(ref_id).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValidationError("client_reference_id is not valid") from exc
    if not isinstance(payload_obj, dict):
        raise ValidationError("client_reference_id payload must be object")
    if payload_obj.get("schema_version") != _SCHEMA_VERSION:
        raise ValidationError("client_reference_id schema_version must be 1")
    issued_at = payload_obj.get("issued_at")
    if not isinstance(issued_at, str) or not issued_at.strip():
        raise ValidationError("client_reference_id issued_at is required")
    issued_at_raw = issued_at.strip()
    issued_at_for_parse = issued_at_raw[:-1] + "+00:00" if issued_at_raw.endswith(("Z", "z")) else issued_at_raw
    try:
        issued_at_dt = datetime.fromisoformat(issued_at_for_parse)
    except ValueError as exc:
        raise ValidationError("client_reference_id issued_at must be valid ISO-8601 timestamp") from exc
    if issued_at_dt.tzinfo is None:
        raise ValidationError("client_reference_id issued_at must include timezone offset")
    issued_at_utc = issued_at_dt.astimezone(UTC)
    now_utc = (now or datetime.now(UTC)).astimezone(UTC)
    age_seconds = (now_utc - issued_at_utc).total_seconds()
    if age_seconds > max_age_seconds:
        raise ValidationError("client_reference_id is expired")
    if age_seconds < -max_future_seconds:
        raise ValidationError("client_reference_id is from the future")
    telegram_user_id = validate_telegram_user_id(payload_obj.get("telegram_user_id"))
    raw_internal = payload_obj.get("internal_user_id")
    if raw_internal is not None and not isinstance(raw_internal, str):
        raise ValidationError("client_reference_id internal_user_id must be string")
    internal_user_id = _normalize_internal_user_id(raw_internal if isinstance(raw_internal, str) else None)
    return CheckoutReferencePayload(
        schema_version=_SCHEMA_VERSION,
        issued_at=issued_at_raw,
        telegram_user_id=telegram_user_id,
        internal_user_id=internal_user_id,
    )
