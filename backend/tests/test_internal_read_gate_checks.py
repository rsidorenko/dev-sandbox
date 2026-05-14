"""Regression tests for ADM-01/ADM-02 internal read gate checks (no script subprocess)."""

from __future__ import annotations

import pytest

from app.admin_support.internal_read_gate_checks import run_admin_support_internal_read_gate_checks
from app.shared.test_helpers import run_async as _run


def test_internal_read_gate_checks_passes() -> None:
    _run(run_admin_support_internal_read_gate_checks())


@pytest.mark.anyio
async def test_internal_read_gate_checks_passes_anyio() -> None:
    await run_admin_support_internal_read_gate_checks()
