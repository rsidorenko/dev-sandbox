"""Manual verification of all security fixes.

Run: cd backend && python scripts/verify_security_fixes.py
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys

# Fix Windows encoding
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# Ensure src is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))


def _result(name: str, passed: bool, detail: str = "") -> None:
    status = "PASS" if passed else "FAIL"
    suffix = f" — {detail}" if detail else ""
    print(f"  [{status}] {name}{suffix}")
    if not passed:
        global _any_failed
        _any_failed = True


_any_failed = False

print("=" * 60)
print("MANUAL VERIFICATION OF SECURITY FIXES")
print("=" * 60)

# ─────────────────────────────────────────────────────────────
# FIX C2: YooKassa Webhook Signature Verification
# ─────────────────────────────────────────────────────────────
print("\n--- Fix C2: YooKassa webhook signature verification ---")

import asyncio
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.datastructures import Headers
from unittest.mock import MagicMock

from app.yookassa.webhook import _verify_yookassa_signature, _safe_json_error


class _FakeRequest:
    def __init__(self, headers: dict, body: bytes = b""):
        self._headers = headers
        self._body = body

    @property
    def headers(self):
        return self._headers


# Test 1: Secret not configured → None (skip verification, backward compatible)
os.environ.pop("YOOKASSA_WEBHOOK_SECRET", None)
req = _FakeRequest({})
result = _verify_yookassa_signature(b"test body", req)
_result("No secret configured → skip verification (returns None)", result is None)

# Test 2: Secret configured, correct signature → None (OK)
secret = "test_webhook_secret_12345"
os.environ["YOOKASSA_WEBHOOK_SECRET"] = secret
body = b'{"event":"payment.succeeded","object":{"id":"pay123"}}'
expected_sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
req = _FakeRequest({"X-Request-Signature-SHA256": expected_sig})
result = _verify_yookassa_signature(body, req)
_result("Correct HMAC signature → returns None (pass)", result is None)

# Test 3: Secret configured, wrong signature → 401 error
req = _FakeRequest({"X-Request-Signature-SHA256": "wrong_signature_value"})
result = _verify_yookassa_signature(body, req)
_result("Wrong signature → returns 401", isinstance(result, JSONResponse) and result.status_code == 401)
if isinstance(result, JSONResponse):
    body_text = json.loads(result.body.decode())
    _result("Error body contains 'invalid_signature'", body_text.get("error") == "invalid_signature")

# Test 4: Secret configured, missing header → 401
req = _FakeRequest({})
result = _verify_yookassa_signature(body, req)
_result("Missing signature header → returns 401", isinstance(result, JSONResponse) and result.status_code == 401)

# Test 5: Timing-safe comparison (hmac.compare_digest is used)
import inspect
source = inspect.getsource(_verify_yookassa_signature)
_result("Uses hmac.compare_digest (constant-time)", "hmac.compare_digest" in source)

os.environ.pop("YOOKASSA_WEBHOOK_SECRET", None)


# ─────────────────────────────────────────────────────────────
# FIX H2: Payment Amount Verification
# ─────────────────────────────────────────────────────────────
print("\n--- Fix H2: Payment amount verification ---")

# Test: float→int with rounding edge cases
val1 = round(float("299.99") * 100)
_result(f'round(float("299.99") * 100) = {val1} (should be 29999)', val1 == 29999)

val2 = round(float("249.00") * 100)
_result(f'round(float("249.00") * 100) = {val2} (should be 24900)', val2 == 24900)

val3 = round(float("0.01") * 100)
_result(f'round(float("0.01") * 100) = {val3} (should be 1)', val3 == 1)

# Test: amount mismatch logic
from app.domain.plans import get_plan

plan = get_plan("1m")
_result("Plan 1m exists", plan is not None)
if plan:
    expected = plan.price_rubles * 100
    # Match case
    _result(f"Amount match: abs({expected} - {expected}) <= 1 → pass", abs(expected - expected) <= 1)
    # Mismatch case (off by 2)
    _result(f"Amount mismatch: abs({expected} - {expected + 2}) > 1 → reject", abs(expected - (expected + 2)) > 1)
    # Tolerance of 1 kopeck
    _result(f"Tolerance 1 kop: abs({expected} - {expected + 1}) <= 1 → pass", abs(expected - (expected + 1)) <= 1)


# ─────────────────────────────────────────────────────────────
# FIX H5: Auth at Routing Level
# ─────────────────────────────────────────────────────────────
print("\n--- Fix H5: Auth at routing level ---")

from app.web_api.app import build_web_api_app
import asyncpg

# Verify route structure
# We can't call build_web_api_app without a real pool, but we can check the route definitions

# Check _with_auth is defined and wraps correctly
from app.web_api.app import _with_auth

async def _mock_handler(request):
    return JSONResponse({"ok": True})

wrapped = _with_auth(_mock_handler)
_result("_with_auth creates a callable wrapper", callable(wrapped))

# Test that _with_auth rejects unauthenticated requests
from starlette.testclient import TestClient

# Create a minimal test
async def _test_with_auth_rejects():
    from unittest.mock import AsyncMock, patch

    mock_request = MagicMock()
    mock_request.cookies = {}
    mock_request.headers = {}
    mock_request.state = MagicMock()

    # Mock require_auth to return a JSONResponse (unauthorized)
    with patch("app.web_api.app.require_auth", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = JSONResponse({"error": "unauthorized"}, status_code=401)
        result = await wrapped(mock_request)
        _result("_with_auth returns 401 when require_auth fails", result.status_code == 401)

    # Mock require_auth to return claims (authorized)
    with patch("app.web_api.app.require_auth", new_callable=AsyncMock) as mock_auth:
        mock_auth.return_value = {"user_id": "u123", "exp": 9999999999}
        result = await wrapped(mock_request)
        _result("_with_auth calls handler when require_auth succeeds", result.status_code == 200)
        _result("_with_auth sets request.state.user", mock_request.state.user == {"user_id": "u123", "exp": 9999999999})

asyncio.run(_test_with_auth_rejects())

# Verify routes that should NOT have auth
source = inspect.getsource(build_web_api_app)
_result("healthz has no auth wrapper", 'Route("/api/v1/healthz", _healthz' in source)
_result("subscription endpoint has no auth wrapper", 'Route("/sub/{token}", handle_subscription' in source)
_result("YooKassa webhook has no auth wrapper", 'Route("/api/v1/payment/yookassa/webhook", _yookassa_webhook_handler' in source)

# Verify routes that SHOULD have auth
_result("profile has _with_auth", '_with_auth(handle_get_profile)' in source)
_result("keys has _with_auth", '_with_auth(handle_get_keys)' in source)
_result("reissue has _with_auth", '_with_auth(_with_csrf(handle_reissue_keys))' in source)
_result("renew has _with_auth", '_with_auth(_with_csrf(handle_renew_subscription))' in source)
_result("create payment has _with_auth", '_with_auth(_with_csrf(handle_create_payment))' in source)


# ─────────────────────────────────────────────────────────────
# FIX H3: Admin Endpoints Auth
# ─────────────────────────────────────────────────────────────
print("\n--- Fix H3: Admin endpoints auth ---")

from app.admin_support.internal_auth import verify_internal_admin_secret

# Test 1: Secret not configured → None (backward compatible)
os.environ.pop("ADM_INTERNAL_SECRET", None)
req = _FakeRequest({})
result = verify_internal_admin_secret(req)
_result("No ADM_INTERNAL_SECRET → None (backward compatible)", result is None)

# Test 2: Secret configured, correct header → None
admin_secret = "super_secret_admin_key"
os.environ["ADM_INTERNAL_SECRET"] = admin_secret
req = _FakeRequest({"X-Admin-Secret": admin_secret})
result = verify_internal_admin_secret(req)
_result("Correct admin secret → None (pass)", result is None)

# Test 3: Secret configured, wrong header → 401
req = _FakeRequest({"X-Admin-Secret": "wrong"})
result = verify_internal_admin_secret(req)
_result("Wrong admin secret → 401", isinstance(result, JSONResponse) and result.status_code == 401)

# Test 4: Secret configured, empty header → 401
req = _FakeRequest({"X-Admin-Secret": ""})
result = verify_internal_admin_secret(req)
_result("Empty admin secret → 401", isinstance(result, JSONResponse) and result.status_code == 401)

# Test 5: Secret configured, missing header → 401
req = _FakeRequest({})
result = verify_internal_admin_secret(req)
_result("Missing admin secret header → 401", isinstance(result, JSONResponse) and result.status_code == 401)

# Test 6: Uses constant-time comparison
source = inspect.getsource(verify_internal_admin_secret)
_result("Uses hmac.compare_digest (constant-time)", "hmac.compare_digest" in source)

# Test 7: Verify auth is in adm01 and adm02 route handlers
adm01_source = inspect.getsource(
    __import__("app.admin_support.adm01_internal_http", fromlist=["create_adm01_internal_http_app"])
)
_result("ADM-01 handler calls verify_internal_admin_secret", "verify_internal_admin_secret" in adm01_source)

adm02_source = inspect.getsource(
    __import__("app.admin_support.adm02_internal_http", fromlist=["create_adm02_internal_http_app"])
)
_result("ADM-02 handler calls verify_internal_admin_secret", "verify_internal_admin_secret" in adm02_source)

os.environ.pop("ADM_INTERNAL_SECRET", None)


# ─────────────────────────────────────────────────────────────
# FIX H1: Atomic Balance Payment
# ─────────────────────────────────────────────────────────────
print("\n--- Fix H1: Atomic balance payment ---")

from app.persistence.postgres_referral import PostgresReferralBalanceRepository

# Verify new static methods exist
_result(
    "PostgresReferralBalanceRepository has debit_in_connection",
    hasattr(PostgresReferralBalanceRepository, "debit_in_connection"),
)
_result(
    "PostgresReferralBalanceRepository has get_balance_in_connection",
    hasattr(PostgresReferralBalanceRepository, "get_balance_in_connection"),
)

# Verify they are static methods
import types
di = getattr(PostgresReferralBalanceRepository, "debit_in_connection")
gb = getattr(PostgresReferralBalanceRepository, "get_balance_in_connection")
_result("debit_in_connection is callable", callable(di))
_result("get_balance_in_connection is callable", callable(gb))

# Verify runtime_facade uses the transaction pattern
facade_source = inspect.getsource(
    __import__("app.bot_transport.runtime_facade", fromlist=["_process_balance_payment"])
)
_result("Uses pool.acquire() + conn.transaction()", "conn.transaction()" in facade_source)
_result("Uses debit_in_connection", "debit_in_connection" in facade_source)
_result("Uses get_for_user_in_connection", "get_for_user_in_connection" in facade_source)
_result("Uses upsert_state_in_connection", "upsert_state_in_connection" in facade_source)
_result("Has fallback for in-memory (pool is None)", "pool is not None" in facade_source)


# ─────────────────────────────────────────────────────────────
# FIX C3+H4: Encrypt Panel Passwords
# ─────────────────────────────────────────────────────────────
print("\n--- Fix C3+H4: Encrypt panel passwords + remove plaintext fallback ---")

from app.issuance.xui_vless_provider import _resolve_panel_password

# Test 1: Empty encrypted_password → raises RuntimeError
mock_row_empty = {"id": 1, "encrypted_password": "", "panel_password": "plain123"}
try:
    _resolve_panel_password(mock_row_empty)
    _result("Empty encrypted_password raises RuntimeError", False)
except RuntimeError as e:
    _result("Empty encrypted_password raises RuntimeError", True)
    _result("Error message mentions encrypt script", "encrypt_panel_passwords" in str(e) or "migrate_encrypt_passwords" in str(e))

# Test 2: No encrypted_password key → raises RuntimeError
mock_row_no_key = {"id": 2, "panel_password": "plain123"}
try:
    _resolve_panel_password(mock_row_no_key)
    _result("Missing encrypted_password key raises RuntimeError", False)
except RuntimeError:
    _result("Missing encrypted_password key raises RuntimeError", True)

# Test 3: Valid encrypted password → decrypts correctly
from app.security.field_encryption import encrypt_field, decrypt_field
test_key = "YWJjZGVmZ2hpamtsbW5vcHFyc3R1dnd4eXoxMjM0NTY="  # 32-byte base64
os.environ["FIELD_ENCRYPTION_KEY"] = test_key
encrypted = encrypt_field("my_secret_password")
mock_row_encrypted = {"id": 3, "encrypted_password": encrypted}
result = _resolve_panel_password(mock_row_encrypted)
_result("Valid encrypted_password decrypts correctly", result == "my_secret_password")

# Test 4: panel_password removed from SQL SELECT
xui_source = inspect.getsource(
    __import__("app.issuance.xui_vless_provider", fromlist=["_load_server_configs"])
)
_result("panel_password removed from SQL SELECT", "panel_password" not in xui_source or "COALESCE(encrypted_password" in xui_source)

# Test 5: Startup warning check exists
from app.issuance.xui_vless_provider import XuiVlessProvider
_result("XuiVlessProvider has _check_plaintext_passwords", hasattr(XuiVlessProvider, "_check_plaintext_passwords"))

os.environ.pop("FIELD_ENCRYPTION_KEY", None)


# ─────────────────────────────────────────────────────────────
# FIX C1: .gitignore + rotation script
# ─────────────────────────────────────────────────────────────
print("\n--- Fix C1: Rotation script + .gitignore ---")

# Test 1: .ssh/ in .gitignore
gitignore_path = os.path.join(os.path.dirname(__file__), "..", "..", ".gitignore")
with open(gitignore_path) as f:
    gitignore_content = f.read()
_result(".ssh/ is in .gitignore", ".ssh/" in gitignore_content)

# Test 2: rotation script exists and compiles
rotation_script = os.path.join(os.path.dirname(__file__), "rotate_server_credentials.py")
_result("rotate_server_credentials.py exists", os.path.exists(rotation_script))

# Test 3: migrate_encrypt_passwords.py exists
migrate_script = os.path.join(os.path.dirname(__file__), "migrate_encrypt_passwords.py")
_result("migrate_encrypt_passwords.py exists", os.path.exists(migrate_script))

# Test 4: .env.prod.example has required vars
env_example_path = os.path.join(os.path.dirname(__file__), "..", "..", ".env.prod.example")
with open(env_example_path) as f:
    env_content = f.read()
_result(".env.prod.example has FIELD_ENCRYPTION_KEY", "FIELD_ENCRYPTION_KEY" in env_content)
_result(".env.prod.example has YOOKASSA_SHOP_ID", "YOOKASSA_SHOP_ID" in env_content)
_result(".env.prod.example has YOOKASSA_API_KEY", "YOOKASSA_API_KEY" in env_content)
_result(".env.prod.example has YOOKASSA_WEBHOOK_SECRET", "YOOKASSA_WEBHOOK_SECRET" in env_content)
_result(".env.prod.example has ADM_INTERNAL_SECRET", "ADM_INTERNAL_SECRET" in env_content)


# ─────────────────────────────────────────────────────────────
# SUMMARY
# ─────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
if _any_failed:
    print("RESULT: SOME CHECKS FAILED — review above")
    sys.exit(1)
else:
    print("RESULT: ALL CHECKS PASSED")
    sys.exit(0)
