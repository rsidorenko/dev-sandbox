"""Shared-secret authentication for internal admin HTTP endpoints.

Fail-closed in production: when ``APP_ENV`` is a production-like value (anything
outside the local/test set — note the codebase treats an UNSET ``APP_ENV`` as
``"development"``/local, matching ``app.security.config``), a missing/empty
``ADM_INTERNAL_SECRET`` rejects every request with 401. In local/test ``APP_ENV``
the secret is optional (admin endpoints are internal/network-isolated), which
preserves local-development and test-suite behavior.
"""

from __future__ import annotations

import hmac
import os

from starlette.requests import Request
from starlette.responses import JSONResponse

_ENV_ADMIN_SECRET = "ADM_INTERNAL_SECRET"
_ENV_APP_ENV = "APP_ENV"
_DEFAULT_APP_ENV = "development"  # matches app.security.config.load_runtime_config
_LOCAL_APP_ENVS = frozenset({"development", "dev", "local", "test"})
_ADMIN_SECRET_HEADER = "X-Admin-Secret"


def _is_local_app_env(app_env: str) -> bool:
    return app_env.strip().lower() in _LOCAL_APP_ENVS


def _unauthorized() -> JSONResponse:
    return JSONResponse({"error": "unauthorized"}, status_code=401)


def verify_internal_admin_secret(request: Request, *, app_env: str | None = None) -> JSONResponse | None:
    """Validate the admin shared secret from the ``X-Admin-Secret`` header.

    Returns an error response on rejection, or ``None`` when authorized.

    Fail-closed in production ``APP_ENV`` (any value outside the local/test set):
    a missing/empty ``ADM_INTERNAL_SECRET`` rejects with 401 — authorization no
    longer silently falls back to "network isolation". In local/test ``APP_ENV``
    (the default when ``APP_ENV`` is unset, matching ``app.security.config``) the
    secret is optional, preserving local-dev and test-suite behavior.
    ``app_env`` is injectable for tests; when omitted it is resolved from
    ``APP_ENV`` (unset ⇒ ``development``).
    """
    secret = os.environ.get(_ENV_ADMIN_SECRET, "").strip()
    if not secret:
        env = app_env if app_env is not None else os.environ.get(_ENV_APP_ENV, _DEFAULT_APP_ENV)
        if _is_local_app_env(env):
            return None
        return _unauthorized()
    header_val = request.headers.get(_ADMIN_SECRET_HEADER, "").strip()
    if not header_val or not hmac.compare_digest(header_val, secret):
        return _unauthorized()
    return None
