"""Точка входа оператора для действий конфиг-выдачи (issue/resend/revoke).

Запуск: ``ISSUANCE_OPERATOR_ENABLE=1`` и
``python -m app.application.issuance_operator_main <action> --internal-user-id <id> --access-profile-key <key> --issue-idempotency-key <key> [--correlation-id <hex32>]``
из ``backend/``.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from collections.abc import Sequence

import asyncpg

from app.issuance.contracts import IssuanceOperationType, IssuanceRequest, IssuanceServiceResult
from app.issuance.fake_provider import FakeIssuanceProvider, FakeProviderMode
from app.issuance.service import IssuanceService
from app.persistence.postgres_issuance_state import PostgresIssuanceStateRepository
from app.security.config import ConfigurationError, load_runtime_config
from app.security.errors import PersistenceDependencyError
from app.security.validation import ValidationError
from app.shared.correlation import is_valid_correlation_id, new_correlation_id
from app.shared.types import SubscriptionSnapshotState

ISSUANCE_OPERATOR_ENABLE = "ISSUANCE_OPERATOR_ENABLE"
ISSUANCE_OPERATOR_FAKE_PROVIDER_MODE = "ISSUANCE_OPERATOR_FAKE_PROVIDER_MODE"

_STDERR_FAIL = "issuance_operator: failed"
_STDOUT_OK_PREFIX = "issuance_operator: ok"
_CATEGORY_KEY = "category"
_SAFE_KEY_RE = re.compile(r"^[\w.\-:]{1,256}$")


def _is_operator_enabled() -> bool:
    raw = os.environ.get(ISSUANCE_OPERATOR_ENABLE, "").strip().lower()
    return raw in ("1", "true", "yes")


def _validate_safe_key(value: str, *, field: str) -> str:
    if not isinstance(value, str):
        raise ValidationError(f"{field} must be a string")
    s = value.strip()
    if not s:
        raise ValidationError(f"{field} is required")
    if _SAFE_KEY_RE.fullmatch(s) is None:
        raise ValidationError(f"{field} has invalid format")
    return s


def _parse_correlation_id(raw: str | None) -> str:
    if raw is None:
        return new_correlation_id()
    candidate = raw.strip()
    if not candidate:
        raise ValidationError("correlation_id is empty")
    if not is_valid_correlation_id(candidate):
        raise ValidationError("correlation_id has invalid format")
    return candidate


def _parse_provider_mode() -> FakeProviderMode:
    raw = os.environ.get(ISSUANCE_OPERATOR_FAKE_PROVIDER_MODE, "").strip().lower()
    if not raw:
        return FakeProviderMode.SUCCESS
    for mode in FakeProviderMode:
        if raw == mode.value:
            return mode
    raise ConfigurationError("invalid configuration: ISSUANCE_OPERATOR_FAKE_PROVIDER_MODE")


def _stderr_fail(category: str) -> None:
    print(f"{_STDERR_FAIL} {_CATEGORY_KEY}={category}", file=sys.stderr, flush=True)


def _state_label(action: IssuanceOperationType, result: IssuanceServiceResult) -> str:
    if result.category.value in ("issued", "already_issued"):
        return "issued"
    if result.category.value == "revoked":
        return "revoked"
    if action is IssuanceOperationType.REVOKE:
        return "none"
    return "none"


def _delivery_label(result: IssuanceServiceResult) -> str:
    if result.category.value == "delivery_ready":
        return "redacted"
    return "none"


def _print_ok(*, action: IssuanceOperationType, result: IssuanceServiceResult) -> None:
    print(
        f"{_STDOUT_OK_PREFIX}"
        f" action={action.value}"
        f" outcome={result.category.value}"
        f" state={_state_label(action, result)}"
        f" delivery={_delivery_label(result)}",
        flush=True,
    )


def _resolve_request(
    *,
    action: IssuanceOperationType,
    internal_user_id: str,
    issue_idempotency_key: str,
    correlation_id: str,
) -> IssuanceRequest:
    if action is IssuanceOperationType.ISSUE:
        return IssuanceRequest(
            internal_user_id=internal_user_id,
            subscription_state=SubscriptionSnapshotState.ACTIVE,
            operation=IssuanceOperationType.ISSUE,
            idempotency_key=issue_idempotency_key,
            correlation_id=correlation_id,
            link_issue_idempotency_key=None,
        )
    return IssuanceRequest(
        internal_user_id=internal_user_id,
        subscription_state=SubscriptionSnapshotState.ACTIVE,
        operation=action,
        idempotency_key=f"{action.value}:{issue_idempotency_key}",
        correlation_id=correlation_id,
        link_issue_idempotency_key=issue_idempotency_key,
    )


def main() -> None:
    raise SystemExit(asyncio.run(async_main()))


async def async_main(argv: Sequence[str] | None = None) -> int:
    qargv = list(sys.argv[1:]) if argv is None else list(argv)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("issue", "resend", "revoke"))
    parser.add_argument("--internal-user-id", required=True, metavar="ID")
    parser.add_argument("--access-profile-key", required=True, metavar="KEY")
    parser.add_argument("--issue-idempotency-key", required=True, metavar="KEY")
    parser.add_argument("--correlation-id", required=False, metavar="HEX32")
    try:
        args = parser.parse_args(qargv)
    except SystemExit as exc:
        code = exc.code
        if code in (0, None):
            return 0
        return 1

    if not _is_operator_enabled():
        _stderr_fail("opt_in")
        return 1

    try:
        action = IssuanceOperationType(args.action)
        internal_user_id = _validate_safe_key(args.internal_user_id, field="internal_user_id")
        _ = _validate_safe_key(args.access_profile_key, field="access_profile_key")
        issue_idem = _validate_safe_key(args.issue_idempotency_key, field="issue_idempotency_key")
        correlation_id = _parse_correlation_id(args.correlation_id)
    except ValidationError:
        _stderr_fail("validation")
        return 1

    try:
        config = load_runtime_config()
        dsn = (config.database_url or "").strip()
        if not dsn:
            raise ConfigurationError("missing or empty configuration: DATABASE_URL")
        provider_mode = _parse_provider_mode()
    except ConfigurationError:
        _stderr_fail("config")
        return 1

    pool: asyncpg.Pool | None = None
    try:
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4)
        provider = FakeIssuanceProvider(provider_mode)
        repo = PostgresIssuanceStateRepository(pool)
        service = IssuanceService(provider, operational_state=repo)
        req = _resolve_request(
            action=action,
            internal_user_id=internal_user_id,
            issue_idempotency_key=issue_idem,
            correlation_id=correlation_id,
        )
        result = await service.execute(req)
    except (PersistenceDependencyError, OSError, asyncpg.PostgresError, TimeoutError):
        _stderr_fail("dependency")
        return 1
    except Exception:
        _stderr_fail("unexpected")
        return 1
    finally:
        if pool is not None:
            await pool.close()

    _print_ok(action=action, result=result)
    return 0


if __name__ == "__main__":
    main()
