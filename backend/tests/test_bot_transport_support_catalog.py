"""Tests for static support FAQ and validated contact rendering."""

from __future__ import annotations

import pytest

from app.bot_transport.storefront_config import StorefrontPublicConfig
from app.bot_transport.support_catalog import (
    build_support_contact_text,
    build_support_menu_text,
    get_support_faq_items,
)

_SENSITIVE = ("token", "secret", "dsn", "password")


def test_faq_items_shape_and_nonempty() -> None:
    items = get_support_faq_items()
    assert len(items) >= 3
    keys = {item["key"] for item in items}
    assert keys == {"pricing", "access", "refund"}
    for item in items:
        assert set(item.keys()) == {"key", "question", "answer"}
        assert item["question"].strip()
        assert item["answer"].strip()


def test_faq_and_menu_text_avoid_sensitive_keywords() -> None:
    blob = (build_support_menu_text() + str(get_support_faq_items())).lower()
    for needle in _SENSITIVE:
        assert needle not in blob


def test_menu_text_contains_header_and_contact_hint() -> None:
    text = build_support_menu_text()
    assert text.startswith("Помощь и поддержка")
    assert "/support_contact" in text.lower()


@pytest.mark.parametrize(
    ("cfg", "expected_substrings", "forbidden"),
    (
        (
            StorefrontPublicConfig(
                plan_name=None,
                plan_price=None,
                checkout_url=None,
                renewal_url=None,
                support_url=None,
                support_handle="@team_help",
            ),
            ("@team_help",),
            ("https://",),
        ),
        (
            StorefrontPublicConfig(
                plan_name=None,
                plan_price=None,
                checkout_url=None,
                renewal_url=None,
                support_url="https://example.com/help",
                support_handle=None,
            ),
            ("https://example.com/help",),
            (),
        ),
        (
            StorefrontPublicConfig(
                plan_name=None,
                plan_price=None,
                checkout_url=None,
                renewal_url=None,
                support_url="https://example.com/help",
                support_handle="@team_help",
            ),
            ("https://example.com/help", "@team_help"),
            (),
        ),
        (
            StorefrontPublicConfig(
                plan_name=None,
                plan_price=None,
                checkout_url=None,
                renewal_url=None,
                support_url=None,
                support_handle=None,
            ),
            ("поддержка временно недоступна",),
            ("http://", "https://", "@"),
        ),
    ),
)
def test_support_contact_text_variants(
    cfg: StorefrontPublicConfig,
    expected_substrings: tuple[str, ...],
    forbidden: tuple[str, ...],
) -> None:
    text = build_support_contact_text(cfg).lower()
    for s in expected_substrings:
        assert s in text
    for s in forbidden:
        assert s not in text
    for needle in _SENSITIVE:
        assert needle not in text
