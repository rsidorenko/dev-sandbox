"""``python -m app.internal_admin`` → standalone ADM-01 internal HTTP entry."""

from __future__ import annotations

from app.internal_admin.adm01_http_main import main as _adm01_http_main

if __name__ == "__main__":
    raise SystemExit(_adm01_http_main())
