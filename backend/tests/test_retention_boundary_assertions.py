from __future__ import annotations

import pytest

from tests.retention_boundary_assertions import (
    RETENTION_FORBIDDEN_OUTPUT_FRAGMENTS,
    assert_no_retention_secret_fragments,
    assert_no_retention_success_summary,
    assert_retention_failure_output_safe,
)


def test_assert_no_retention_secret_fragments_accepts_safe_text() -> None:
    assert_no_retention_secret_fragments("retention failed due to timeout, no sensitive payload")


@pytest.mark.parametrize("fragment", RETENTION_FORBIDDEN_OUTPUT_FRAGMENTS)
def test_assert_no_retention_secret_fragments_rejects_forbidden_fragment(fragment: str) -> None:
    with pytest.raises(AssertionError):
        assert_no_retention_secret_fragments(f"synthetic output contains {fragment} marker")


@pytest.mark.parametrize(
    ("fragment", "variant"),
    (
        ("postgres://", "PoStGrEs://"),
        ("postgresql://", "POSTGRESQL://"),
        ("Bearer ", "bearer "),
        ("PRIVATE KEY", "private key"),
        ("TOP_SECRET", "Top_Secret"),
        ("SECRET", "secret"),
        ("TOKEN", "token"),
    ),
)
def test_assert_no_retention_secret_fragments_rejects_mixed_case_forbidden_variants(
    fragment: str,
    variant: str,
) -> None:
    with pytest.raises(AssertionError):
        assert_no_retention_secret_fragments(f"synthetic output contains mixed-case {variant} variant of {fragment}")


def test_assert_no_retention_success_summary_accepts_text_without_markers() -> None:
    assert_no_retention_success_summary(
        stdout="retention command failed before summary output",
        stderr="dependency unavailable",
    )


def test_assert_no_retention_success_summary_rejects_default_success_marker() -> None:
    with pytest.raises(AssertionError):
        assert_no_retention_success_summary(
            stdout="slice1_retention_cleanup dry_run=True",
            stderr="",
        )


def test_assert_no_retention_success_summary_supports_custom_markers() -> None:
    with pytest.raises(AssertionError):
        assert_no_retention_success_summary(
            stdout="custom summary marker found",
            markers=("custom summary marker",),
        )


def test_assert_retention_failure_output_safe_accepts_mixed_safe_parts() -> None:
    assert_retention_failure_output_safe(
        None,
        RuntimeError("synthetic retention failure"),
        "safe failure output without credentials",
        summary_stdout="",
        summary_stderr="",
    )


def test_assert_retention_failure_output_safe_rejects_forbidden_fragment_in_any_part() -> None:
    with pytest.raises(AssertionError):
        assert_retention_failure_output_safe(
            "safe text",
            Exception("saw Bearer placeholder marker in output"),
            summary_stdout="",
            summary_stderr="",
        )


@pytest.mark.parametrize("variant", ("bearer ", "BeArEr ", "TOKEN", "tOkEn"))
def test_assert_retention_failure_output_safe_rejects_mixed_case_forbidden_fragment_in_any_part(
    variant: str,
) -> None:
    with pytest.raises(AssertionError):
        assert_retention_failure_output_safe(
            "safe text",
            f"synthetic mixed-case marker appears: {variant}",
            summary_stdout="",
            summary_stderr="",
        )


def test_assert_retention_failure_output_safe_rejects_summary_markers() -> None:
    with pytest.raises(AssertionError):
        assert_retention_failure_output_safe(
            "safe failure text",
            summary_stdout="slice1_retention_scheduled_cleanup dry_run=False",
            summary_stderr="",
        )
