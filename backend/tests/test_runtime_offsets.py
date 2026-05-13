"""Tests for :mod:`app.runtime.offsets` (pure polling offset helpers)."""

from __future__ import annotations

from pathlib import Path

import pytest

import app.runtime as runtime_pkg
import app.runtime.offsets as offsets_mod
from app.runtime import advance_polling_offset, extract_next_offset_from_raw_updates
from app.runtime.offsets import advance_polling_offset as advance_direct
from app.runtime.offsets import extract_next_offset_from_raw_updates as extract_direct


def _u(update_id: object | None = None, **extra: object) -> dict[str, object]:
    m: dict[str, object] = dict(extra)
    if update_id is not None:
        m["update_id"] = update_id
    return m


def test_extract_empty_batch_returns_none() -> None:
    assert extract_direct(()) is None


def test_extract_single_valid_update_plus_one() -> None:
    assert extract_direct((_u(update_id=1),)) == 2


def test_extract_mixed_mappings_uses_max_valid_update_id() -> None:
    batch = (
        _u(update_id=1),
        _u(update_id=5),
        _u(update_id=3),
    )
    assert extract_direct(batch) == 6


def test_extract_ignores_invalid_missing_bool_update_id() -> None:
    batch = (
        _u(),
        _u(update_id="1"),
        _u(update_id=True),
        _u(update_id=False),
        _u(update_id=0),
        _u(update_id=-1),
        _u(update_id=3.0),
        _u(update_id=7),
    )
    assert extract_direct(batch) == 8


def test_advance_none_current_works() -> None:
    assert advance_direct(None, (_u(update_id=2),)) == 3


def test_advance_no_valid_ids_returns_current() -> None:
    assert advance_direct(10, (_u(), _u(update_id="x"))) == 10


def test_advance_never_decreases() -> None:
    assert advance_direct(100, (_u(update_id=1),)) == 100


def test_advance_current_zero_valueerror() -> None:
    with pytest.raises(ValueError):
        advance_direct(0, ())


def test_advance_negative_current_valueerror() -> None:
    with pytest.raises(ValueError):
        advance_direct(-1, ())


def test_advance_non_int_current_typeerror() -> None:
    with pytest.raises(TypeError):
        advance_direct("1", ())  # type: ignore[arg-type]


def test_advance_bool_current_typeerror() -> None:
    with pytest.raises(TypeError):
        advance_direct(True, ())  # type: ignore[arg-type]


def test_package_exports() -> None:
    assert extract_next_offset_from_raw_updates is extract_direct
    assert advance_polling_offset is advance_direct
    assert runtime_pkg.extract_next_offset_from_raw_updates is extract_direct
    assert runtime_pkg.advance_polling_offset is advance_direct


def test_offsets_source_has_no_forbidden_substrings() -> None:
    text = Path(offsets_mod.__file__).read_text(encoding="utf-8").lower()
    for bad in ("billing", "issuance", "admin", "webhook", "sdk"):
        assert bad not in text
