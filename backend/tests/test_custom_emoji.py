"""Тесты слоя кастомных эмодзи (tg-emoji) для текстов и кнопок бота."""

from __future__ import annotations

from app.bot_transport.custom_emoji import (
    EMOJI_TO_CUSTOM_ID,
    TRIAL_BUTTON_CUSTOM_EMOJI_ID,
    apply_custom_emoji,
    apply_keyboard_custom_emoji,
    button_icon_custom_emoji_id,
    custom_emoji_enabled_from_env,
)
from app.bot_transport.storefront_ui import main_menu_keyboard


class TestFlag:
    def test_disabled_by_default(self) -> None:
        import os

        saved = os.environ.pop("TELEGRAM_CUSTOM_EMOJI_ENABLE", None)
        try:
            assert custom_emoji_enabled_from_env() is False
        finally:
            if saved is not None:
                os.environ["TELEGRAM_CUSTOM_EMOJI_ENABLE"] = saved

    def test_enabled_variants(self, monkeypatch) -> None:  # type: ignore[no-untyped-def]
        for raw in ("1", "true", "YES", " True "):
            monkeypatch.setenv("TELEGRAM_CUSTOM_EMOJI_ENABLE", raw)
            assert custom_emoji_enabled_from_env() is True
        for raw in ("", "0", "false", "no"):
            monkeypatch.setenv("TELEGRAM_CUSTOM_EMOJI_ENABLE", raw)
            assert custom_emoji_enabled_from_env() is False


class TestApplyCustomEmoji:
    def test_disabled_is_noop(self) -> None:
        text = "🎁 Забрать 3 дня бесплатно"
        assert apply_custom_emoji(text, enabled=False) == text

    def test_wraps_known_emoji(self) -> None:
        result = apply_custom_emoji("🚀 Готово", enabled=True)
        assert result == '<tg-emoji emoji-id="5445284980978621387">🚀</tg-emoji> Готово'

    def test_variation_selector_matched_before_base_glyph(self) -> None:
        # ⚙️ (с selector) должен уйти целиком в тег, без хвоста «️»
        result = apply_custom_emoji("⚙️ Настройки", enabled=True)
        assert result == '<tg-emoji emoji-id="5449428597922079323">⚙️</tg-emoji> Настройки'

    def test_unknown_emoji_left_as_is(self) -> None:
        # → и ↩ нет в мапе (нет замены в паке) — остаются юникодом
        text = "→ вперёд, ↩️ назад, ⚪ маркер"
        assert apply_custom_emoji(text, enabled=True) == text

    def test_idempotent(self) -> None:
        once = apply_custom_emoji("🔑 Ключ: ✅ ок", enabled=True)
        assert apply_custom_emoji(once, enabled=True) == once

    def test_html_tags_preserved(self) -> None:
        # tg-emoji вставляется в финальный HTML: соседние теги не ломаются
        result = apply_custom_emoji("<b>🏠 Главное меню</b>", enabled=True)
        assert result == '<b><tg-emoji emoji-id="5465226866321268133">🏠</tg-emoji> Главное меню</b>'

    def test_multiple_emojis_all_wrapped(self) -> None:
        result = apply_custom_emoji("🎁 3 дня · 🔑 VPN", enabled=True)
        assert result.count("<tg-emoji") == 2
        assert "5199749070830197566" in result
        assert "5330115548900501467" in result


class TestKeyboardIcons:
    def test_trial_button_uses_explicit_override_id(self) -> None:
        kb = main_menu_keyboard(trial_available=True)
        apply_keyboard_custom_emoji(kb, enabled=True)
        (button,) = kb["inline_keyboard"][0]
        assert button["callback_data"] == "start_trial"
        assert button["icon_custom_emoji_id"] == TRIAL_BUTTON_CUSTOM_EMOJI_ID

    def test_regular_button_icon_from_first_emoji(self) -> None:
        button = {"text": "🔑 Купить VPN", "callback_data": "buy_vpn"}
        assert button_icon_custom_emoji_id(button) == EMOJI_TO_CUSTOM_ID["🔑"]

    def test_button_without_emoji_gets_no_icon(self) -> None:
        assert button_icon_custom_emoji_id({"text": "Назад", "callback_data": "menu"}) is None

    def test_apply_keyboard_disabled_is_noop(self) -> None:
        kb = {"inline_keyboard": [[{"text": "🔑 Купить", "callback_data": "buy"}]]}
        before = repr(kb)
        apply_keyboard_custom_emoji(kb, enabled=False)
        assert repr(kb) == before

    def test_apply_keyboard_adds_icons(self) -> None:
        kb = {
            "inline_keyboard": [
                [{"text": "🔑 Купить", "callback_data": "buy"}],
                [{"text": "Без эмодзи", "callback_data": "x"}],
            ]
        }
        apply_keyboard_custom_emoji(kb, enabled=True)
        assert kb["inline_keyboard"][0][0]["icon_custom_emoji_id"] == EMOJI_TO_CUSTOM_ID["🔑"]
        assert "icon_custom_emoji_id" not in kb["inline_keyboard"][1][0]

    def test_apply_keyboard_preserves_existing_icon(self) -> None:
        kb = {"inline_keyboard": [[{"text": "🔑", "callback_data": "b", "icon_custom_emoji_id": "1"}]]}
        apply_keyboard_custom_emoji(kb, enabled=True)
        assert kb["inline_keyboard"][0][0]["icon_custom_emoji_id"] == "1"

    def test_apply_keyboard_handles_none_and_foreign_shapes(self) -> None:
        assert apply_keyboard_custom_emoji(None, enabled=True) is None
        assert apply_keyboard_custom_emoji({}, enabled=True) == {}
        assert apply_keyboard_custom_emoji({"inline_keyboard": "не список"}, enabled=True) == {
            "inline_keyboard": "не список"
        }


class TestPlanEmojiScale:
    """Шкала тарифов «путешествие» вместо цветных шариков."""

    def test_plan_scale_emojis_have_custom_ids(self) -> None:
        from app.bot_transport.storefront_ui import _PLAN_EMOJI

        for plan_id, emoji in _PLAN_EMOJI.items():
            assert emoji in EMOJI_TO_CUSTOM_ID, f"{plan_id}: {emoji} нет в мапе кастомных ID"

    def test_no_color_circles_left(self) -> None:
        from pathlib import Path

        ui = Path("src/app/bot_transport/storefront_ui.py").read_text(encoding="utf-8")
        for circle in ("⚪", "🟠", "🔵", "🟣", "🟡", "🟢"):
            in_code = [line for line in ui.splitlines() if circle in line and not line.strip().startswith("#")]
            assert not in_code, f"{circle} остался в коде: {in_code}"
