"""Advisory operator preflight for ADM-01/ADM-02 internal admin HTTP composition (in-process, no DB).

Does not read DATABASE_URL or other deployment secrets. Stdout is a single fixed line on success.
On failure, stderr is a single fixed line (no tracebacks, no exception text, no env dumps).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

# Scripts run with cwd typically `backend/`; ensure src layout resolves like pytest.
_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_SRC = _BACKEND_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from app.admin_support.internal_read_gate_checks import run_admin_support_internal_read_gate_checks  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args(argv)
    try:
        asyncio.run(run_admin_support_internal_read_gate_checks())
    except RuntimeError:
        print("admin_support_internal_read_gate: fail", file=sys.stderr, flush=True)
        return 1
    except Exception:
        print("admin_support_internal_read_gate: failed", file=sys.stderr, flush=True)
        return 1
    print("admin_support_internal_read_gate: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
