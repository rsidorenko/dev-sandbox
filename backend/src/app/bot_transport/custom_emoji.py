"""Кастомные эмодзи Telegram (tg-emoji) для текстов и кнопок бота.

Слой подстановки: юникод-эмодзи в HTML-текстах оборачиваются в
``<tg-emoji emoji-id="...">эмодзи</tg-emoji>`` (Bot API 9.4+), кнопкам добавляется
``icon_custom_emoji_id`` по первому эмодзи текста.

Требование Bot API: у владельца бота должен быть Telegram Premium (либо collectible
username на Fragment) — иначе Telegram отклонит entities. Фича закрыта флагом
``TELEGRAM_CUSTOM_EMOJI_ENABLE`` (по умолчанию off) для безопасного выката/отката.

ID взяты из пака «Telegram iOS Icons» (t.me/addemoji/tgiosicons, 399 анимированных
иконок, выкачаны через getStickerSet 2026-09-01); для эмодзи, которых в паке нет,
оставлены ID официального системного пака RestrictedEmoji
(t.me/addemoji/RestrictedEmoji, датасет github.com/uuigww/telegram_emoji_for_llm).
Fallback-эмодзи (содержимое тега) показывается в системных уведомлениях и при
пересылке non-premium-пользователем — degradation встроен в протокол.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from typing import Any

TELEGRAM_CUSTOM_EMOJI_ENABLE = "TELEGRAM_CUSTOM_EMOJI_ENABLE"


def custom_emoji_enabled_from_env(env_getter: Mapping[str, str] | None = None) -> bool:
    """Флаг включён, когда переменная окружения — 1/true/yes (как TELEGRAM_ACCESS_RESEND_ENABLE)."""
    raw = (
        os.environ.get(TELEGRAM_CUSTOM_EMOJI_ENABLE, "")
        if env_getter is None
        else env_getter.get(TELEGRAM_CUSTOM_EMOJI_ENABLE, "") or ""
    )
    return raw.strip().lower() in ("1", "true", "yes")


#: Эмодзи → custom_emoji_id. Источник — пак «Telegram iOS Icons» (tgiosicons);
#: для эмодзи, которых в паке нет, — системный пак RestrictedEmoji (секции
#: помечены). Семантические замены помечены комментарием «замена:» — fallback-глиф
#: отличается от исходного юникода. Варианты иконок внутри пака выбраны вручную
#: по превью (getStickerSet + getFile, 2026-09-01).
EMOJI_TO_CUSTOM_ID: dict[str, str] = {
    # ── tgiosicons: прямые совпадения ──
    "⚡": "5884428842780594914",
    "⚡️": "5884428842780594914",
    "✅": "5774022692642492953",
    "❌": "5774077015388852135",
    "❓": "6030848053177486888",
    "➕": "5882207227997066107",
    "🎁": "6032644646587338669",
    "🎉": "6041731551845159060",
    "🏠": "6042137469204303531",
    "👆": "5886583490434044162",
    "👋": "6041921818896372382",
    "👤": "6035084557378654059",
    "👥": "6032609071373226027",
    "💡": "5767288287001580715",
    "💬": "6037421444789440735",
    "💰": "5778421276024509124",
    "📂": "6039630677182254664",
    "📊": "5936143551854285132",
    "📎": "6039451237743595514",
    "📞": "6039605143601680423",
    "📺": "6044356915029348425",
    "🔄": "5769248574499983619",
    "🔗": "6028171274939797252",
    "🤖": "6030400221232501136",
    "⚙": "6032742198179532882",
    "⚙️": "6032742198179532882",
    "🆘": "6030848053177486888",  # замена: ❓ (тот же ID, отдельный ключ)
    "🌐": "5776233299424843260",
    "💳": "5778421276024509124",  # замена: 💰 (тот же ID)
    "📅": "5890937706803894250",
    "📝": "5922693616953725714",
    "📋": "5922693616953725714",  # замена: 📝 (тот же ID)
    "📣": "6039422865189638057",
    "📢": "6039422865189638057",  # замена: 📣 (тот же ID)
    "🔓": "6037496202990194718",
    "🖥": "5942734685976138521",
    "🖥️": "5942734685976138521",
    "🗑": "6039522349517115015",
    "🗑️": "6039522349517115015",
    "🛡": "6030445631921721471",
    "🛡️": "6030445631921721471",
    "↩": "5895507195524550741",
    "↩️": "5895507195524550741",
    "ℹ": "6028435952299413210",
    "ℹ️": "6028435952299413210",
    "🚫": "5938215362473496448",
    # ── tgiosicons: смысловые замены (глиф из пака, та же семантика) ──
    "⚠": "6032636795387121097",  # замена: 🛡 с «!»
    "⚠️": "6032636795387121097",
    "🟢": "5774022692642492953",  # замена: ✅ (тот же ID)
    "🟡": "5805331990618053402",  # замена: 🌟 (см. шкалу тарифов ниже)
    # ── шкала тарифов «путешествие» (замена цветных шариков) ──
    "🛫": "5267341200255363810",  # RestrictedEmoji, 1m — взлёт (в tgiosicons нет)
    "✈": "6028346797368283073",  # 14d — самолёт
    "✈️": "6028346797368283073",
    "⭐": "6034923938486684992",  # 3m — звезда
    "⭐️": "6034923938486684992",
    "💎": "5776023601941582822",  # 6m — бриллиант
    "👑": "5805553606635559688",  # 365d — корона
    # ── RestrictedEmoji: в tgiosicons отсутствуют ──
    "⏳": "5451732530048802485",
    "➖": "5229113891081956317",
    "🇷🇺": "5449408995691341691",
    "🌍": "5399898266265475100",
    "🎟": "5377599075237502153",
    "🎟️": "5377599075237502153",
    "🏆": "5409008750893734809",
    "👇": "5470177992950946662",
    "💸": "5472030678633684592",
    "💻": "5431376038628171216",
    "📱": "5407025283456835913",
    "📲": "5406809207947142040",
    "🔐": "5472308992514464048",
    "🔑": "5330115548900501467",
    "😕": "5373272140499918095",
    "🚀": "5445284980978621387",
    "🔥": "5420315771991497307",
    "✔": "5188216731453103384",
    "✔️": "5188216731453103384",
    "🧰": "5449428597922079323",
    "✉": "5472019095106886003",  # замена: 💌
    "✉️": "5472019095106886003",
    "📧": "5406631276042002796",  # замена: 📨
    "📨": "5406631276042002796",
    "💌": "5472019095106886003",
    "🔜": "5451732530048802485",  # замена: ⏳ (тот же ID)
    "💙": "5449759615346548186",
    "💚": "5449380056201697322",
    "💜": "5449468596952507859",
    "🧡": "5449599833973203438",
    "💛": "5449366943666543715",
}

#: Override для кнопки «🎁 Забрать 3 дня бесплатно» (CB_TRIAL): кастомный
#: ID, выбранный вручную поверх дефолтного 🎁 из пака.
TRIAL_BUTTON_CUSTOM_EMOJI_ID = "5203996991054432397"

#: callback_data кнопки триала (storefront_ui.CB_TRIAL) — для override-иконки.
_TRIAL_CALLBACK_DATA = "start_trial"

# Сортировка ключей по убыванию длины: вариант с variation selector (⚙️) должен
# матчиться раньше базового глифа (⚙), иначе от ⚙️ останется хвост «️».
_EMOJI_PATTERN = re.compile("|".join(re.escape(e) for e in sorted(EMOJI_TO_CUSTOM_ID, key=len, reverse=True)))


def apply_custom_emoji(text: str, *, enabled: bool) -> str:
    """Обернуть юникод-эмодзи в tg-emoji теги (HTML parse mode).

    Вызывать ТОЛЬКО на финальном HTML-тексте (после экранирования пользовательских
    данных): вставленные теги не должны проходить через html-escape повторно.
    Идемпотентно: эмодзи внутри уже вставленного tg-emoji тега повторно не
    оборачивается (иначе повторный прогон вкладывает теги друг в друга).
    """

    if not enabled:
        return text

    # Разрезаем текст по уже вставленным тегам: замена идёт только в сегментах
    # между ними, содержимое тега (fallback-глиф) не трогаем.
    segments = re.split(r"(<tg-emoji emoji-id=\"\d+\">.*?</tg-emoji>)", text)
    out: list[str] = []
    for i, segment in enumerate(segments):
        if i % 2 == 1:  # нечётные элементы — это сами теги из capture-группы
            out.append(segment)
            continue

        def _wrap(match: re.Match[str]) -> str:
            emoji = match.group(0)
            custom_id = EMOJI_TO_CUSTOM_ID.get(emoji)
            if custom_id is None:  # непредвиденный глиф — оставляем как есть
                return emoji
            return f'<tg-emoji emoji-id="{custom_id}">{emoji}</tg-emoji>'

        out.append(_EMOJI_PATTERN.sub(_wrap, segment))
    return "".join(out)


def button_icon_custom_emoji_id(button: Mapping[str, Any]) -> str | None:
    """custom_emoji_id для иконки кнопки: override триала → первый эмодзи текста → None."""
    if button.get("callback_data") == _TRIAL_CALLBACK_DATA:
        return TRIAL_BUTTON_CUSTOM_EMOJI_ID
    text = button.get("text") or ""
    match = _EMOJI_PATTERN.search(text)
    if match is None:
        return None
    return EMOJI_TO_CUSTOM_ID.get(match.group(0))


def apply_keyboard_custom_emoji(
    reply_markup: dict[str, Any] | None,
    *,
    enabled: bool,
) -> dict[str, Any] | None:
    """Добавить ``icon_custom_emoji_id`` кнопкам inline-клавиатуры (in-place под флагом).

    InlineKeyboardButton.style уже используется ботом — иконка встаёт рядом тем же
    механизмом нативных полей Bot API. Кнопки без эмодзи в тексте не трогаем.
    """
    if not enabled or not isinstance(reply_markup, dict):
        return reply_markup
    rows = reply_markup.get("inline_keyboard")
    if not isinstance(rows, list):
        return reply_markup
    for row in rows:
        if not isinstance(row, list):
            continue
        for button in row:
            if isinstance(button, dict) and "icon_custom_emoji_id" not in button:
                icon_id = button_icon_custom_emoji_id(button)
                if icon_id is not None:
                    button["icon_custom_emoji_id"] = icon_id
    return reply_markup
