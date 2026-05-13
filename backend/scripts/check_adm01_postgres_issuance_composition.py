"""Advisory operator check: ADM-01 internal lookup with Postgres-backed issuance read (in-process, no listen).

Requires explicit opt-in (``ADM01_POSTGRES_ISSUANCE_COMPOSITION_CHECK_ENABLE``) and ``DATABASE_URL``.
Never prints ``DATABASE_URL`` or request/response bodies. Stdout is a single fixed line on success.
On expected check failure: stderr is a single fixed line. On unexpected error: stderr is a different fixed line
(no tracebacks, no exception text, no env dumps).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SRC = _BACKEND_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.admin_support.adm01_postgres_issuance_composition_check import (  # noqa: E402
    run_adm01_postgres_issuance_composition_check,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        asyncio.run(run_adm01_postgres_issuance_composition_check())
    except RuntimeError:
        print("adm01_postgres_issuance_composition: fail", file=sys.stderr, flush=True)
        return 1
    except Exception:
        print("adm01_postgres_issuance_composition: failed", file=sys.stderr, flush=True)
        return 1
    print("adm01_postgres_issuance_composition: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
