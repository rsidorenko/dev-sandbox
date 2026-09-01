"""Кастомные эмодзи Telegram (tg-emoji) для текстов и кнопок бота.

Слой подстановки: юникод-эмодзи в HTML-текстах оборачиваются в
``<tg-emoji emoji-id="...">эмодзи</tg-emoji>`` (Bot API 9.4+), кнопкам добавляется
``icon_custom_emoji_id`` по первому эмодзи текста.

Требование Bot API: у владельца бота должен быть Telegram Premium (либо collectible
username на Fragment) — иначе Telegram отклонит entities. Фича закрыта флагом
``TELEGRAM_CUSTOM_EMOJI_ENABLE`` (по умолчанию off) для безопасного выката/отката.

ID взяты из официального системного пака Telegram RestrictedEmoji
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


#: Эмодзи → custom_emoji_id пака RestrictedEmoji. Семантические замены помечены
#: комментарием «замена:» — fallback-глиф отличается от исходного юникода.
EMOJI_TO_CUSTOM_ID: dict[str, str] = {
    # ── прямые совпадения ──
    "⏳": "5451732530048802485",
    "⚡": "5431449001532594346",
    "⚡️": "5431449001532594346",
    "✅": "5427009714745517609",
    "❌": "5465665476971471368",
    "❓": "5467666648263564704",
    "➕": "5226945370684140473",
    "➖": "5229113891081956317",
    "🇷🇺": "5449408995691341691",
    "🌍": "5399898266265475100",
    "🎁": "5199749070830197566",
    "🎉": "5436040291507247633",
    "🎟": "5377599075237502153",
    "🎟️": "5377599075237502153",
    "🏆": "5409008750893734809",
    "🏠": "5465226866321268133",
    "👆": "5469718869536940860",
    "👇": "5470177992950946662",
    "👋": "5472055112702629499",
    "👤": "5373012449597335010",
    "👥": "5372926953978341366",
    "💡": "5472146462362048818",
    "💬": "5465300082628763143",
    "💰": "5375296873982604963",
    "💸": "5472030678633684592",
    "💻": "5431376038628171216",
    "📂": "5431721976769027887",
    "📊": "5431577498364158238",
    "📎": "5377844313575150051",
    "📞": "5467539229468793355",
    "📱": "5407025283456835913",
    "📲": "5406809207947142040",
    "📺": "5373330964372004748",
    "🔄": "5264727218734524899",
    "🔐": "5472308992514464048",
    "🔑": "5330115548900501467",
    "🔗": "5375129357373165375",
    "😕": "5373272140499918095",
    "🚀": "5445284980978621387",
    "🤖": "5372981976804366741",
    # ── смысловые замены (глиф из пака, та же семантика) ──
    "⚙": "5449428597922079323",  # замена: 🧰 toolbox
    "⚙️": "5449428597922079323",
    "⚠": "5467596412663372909",  # замена: ⁉️
    "⚠️": "5467596412663372909",
    "✉": "5472019095106886003",  # замена: 💌
    "✉️": "5472019095106886003",
    "🆘": "5467666648263564704",  # замена: ❓ (тот же ID, отдельный ключ)
    "🌐": "5399898266265475100",  # замена: 🌍 глобус
    "💳": "5375296873982604963",  # замена: 💰
    "📅": "5431897022456145283",  # замена: 📆
    "📋": "5334882760735598374",  # замена: 📝
    "📢": "5469903029144657419",  # замена: 📣
    "📧": "5406631276042002796",  # замена: 📨
    "🔓": "5472308992514464048",  # замена: 🔐 (тот же ID)
    "🔜": "5451732530048802485",  # замена: ⏳ (тот же ID)
    "🖥": "5431376038628171216",  # замена: 💻 (тот же ID)
    "🖥️": "5431376038628171216",
    "🗑": "5465665476971471368",  # замена: ❌ (тот же ID)
    "🗑️": "5465665476971471368",
    "🛡": "5408935401442267103",  # замена: ⚔️
    "🛡️": "5408935401442267103",
    "🟡": "5458799228719472718",  # замена: 🌟 (см. шкалу тарифов ниже)
    "🟢": "5427009714745517609",  # замена: ✅ (тот же ID)
    # ── шкала тарифов «путешествие» (замена цветных шариков) ──
    "🛫": "5267341200255363810",  # 1m — взлёт
    "✈": "5361600266225326825",  # 14d — самолёт
    "✈️": "5361600266225326825",
    "⭐": "5435957248314579621",  # 3m — звезда
    "⭐️": "5435957248314579621",
    "💎": "5471952986970267163",  # 6m — бриллиант
    "👑": "5467406098367521267",  # 365d — корона
    "🔥": "5420315771991497307",
    "✔": "5188216731453103384",
    "✔️": "5188216731453103384",
    "🧰": "5449428597922079323",
    "📝": "5334882760735598374",
    "📣": "5469903029144657419",
    "📨": "5406631276042002796",
    "💌": "5472019095106886003",
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
