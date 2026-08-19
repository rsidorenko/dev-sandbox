"""Фаза 4 (бот): отображение закреплённого промокода — бейдж меню/статуса, скидка и
кнопка промокода в сводке заказа, возврат к сводке после ввода.

In-memory composition (pool=None). Переиспользует ту же валидацию/математику Фазы 3
(resolve_active_promo/apply_promo_discount) — здесь проверяется только UI-обвязка:
что закреплённый промокод показывается, скидка отображается, а ввод из оплаты возвращает
к сводке со скидкой. Сами инварианты скидки/сверки покрыты test_promo_redemption.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.application.bootstrap import build_slice1_composition
from app.application.promo_service import resolve_active_promo
from app.bot_transport.runtime_facade import (
    _active_applied_promo,
    _handle_promo_code_input,
    _promo_menu_badge,
    _render_purchase_summary,
)
from app.bot_transport.storefront_ui import CB_CONFIRM_PAY, CB_PROMO_AT
from app.domain.promo import (
    PROMO_APPLIED_TTL_HOURS,
    PromoAudience,
    PromoCode,
    PromoDiscountKind,
)
from app.issuance.vless_provider import StubVlessProvider
from app.shared.test_helpers import run_async as _run


def _promo(
    *,
    code: str = "WELCOME",
    audience: PromoAudience = PromoAudience.NEW_USERS,
    value: int = 50,
    kind: PromoDiscountKind = PromoDiscountKind.PERCENT,
    discounts_by_plan: dict[str, int] | None = None,
    is_active: bool = True,
    expires_at: datetime | None = None,
) -> PromoCode:
    now = datetime.now(UTC)
    return PromoCode(
        code=code,
        audience=audience,
        discount_kind=kind,
        discount_value=value,
        max_redemptions=None,
        redemptions_count=0,
        expires_at=expires_at,
        is_active=is_active,
        note=None,
        created_at=now,
        updated_at=now,
        created_by="test",
        discounts_by_plan=discounts_by_plan,
    )


def _composition(user_id: int = 9101):
    c = build_slice1_composition(bot_username="testbot", vless_provider=StubVlessProvider())
    _run(c.identity.create_if_absent(user_id))
    id_rec = _run(c.identity.find_by_telegram_user_id(user_id))
    return c, user_id, id_rec.internal_user_id


def _apply(c, uid: str, promo: PromoCode, *, expires_at: datetime | None = None) -> None:
    _run(c.promo_code_repo.create(promo))
    exp = expires_at or (datetime.now(UTC) + timedelta(hours=PROMO_APPLIED_TTL_HOURS))
    _run(c.promo_applied_repo.upsert(uid, promo.code, exp))


def _keyboard_rows(kb: dict) -> list[list[dict]]:
    return kb["inline_keyboard"]


# ── _active_applied_promo / бейдж ──────────────────────────────────────────


def test_active_applied_promo_returns_promo_when_applied():
    c, uid, iid = _composition()
    _apply(c, iid, _promo(value=50))
    active = _run(_active_applied_promo(c, uid))
    assert active is not None
    promo, expires_at = active
    assert promo.code == "WELCOME"
    assert expires_at > datetime.now(UTC)


def test_active_applied_promo_none_when_no_promo():
    c, uid, _iid = _composition()
    assert _run(_active_applied_promo(c, uid)) is None


def test_active_applied_promo_none_when_record_expired():
    c, uid, iid = _composition()
    _apply(c, iid, _promo(), expires_at=datetime(2000, 1, 1, tzinfo=UTC))
    assert _run(_active_applied_promo(c, uid)) is None


def test_active_applied_promo_none_when_audience_mismatch():
    # EXISTING_USERS промокод + новый юзер → None.
    c, uid, iid = _composition()
    _apply(c, iid, _promo(audience=PromoAudience.EXISTING_USERS))
    assert _run(_active_applied_promo(c, uid)) is None


def test_promo_menu_badge_contains_code_and_label():
    c, uid, iid = _composition()
    _apply(c, iid, _promo(value=50))
    badge = _run(_promo_menu_badge(c, uid))
    assert "WELCOME" in badge
    assert "−50%" in badge
    assert "ещё" in badge


def test_promo_menu_badge_empty_without_promo():
    c, uid, _iid = _composition()
    assert _run(_promo_menu_badge(c, uid)) == ""


# ── _render_purchase_summary: скидка + кнопка промокода ─────────────────────


def test_render_summary_with_promo_shows_discount_and_promo_button():
    c, uid, iid = _composition()
    _apply(c, iid, _promo(value=50))  # 50% от 249 = 125
    text, keyboard = _run(_render_purchase_summary(c, uid, "1m", 5))
    assert "125 ₽" in text and "вместо 249 ₽" in text
    rows = _keyboard_rows(keyboard)
    # Кнопка промокода несёт код и callback возврата к сводке.
    promo_rows = [r[0] for r in rows if "WELCOME" in r[0]["text"]]
    assert promo_rows and promo_rows[0]["callback_data"] == f"{CB_PROMO_AT}1m:5"


def test_render_summary_without_promo_has_enter_button():
    c, uid, _iid = _composition()
    text, keyboard = _run(_render_purchase_summary(c, uid, "1m", 5))
    assert "вместо" not in text
    assert "249 ₽" in text
    rows = _keyboard_rows(keyboard)
    enter_rows = [r[0] for r in rows if "Ввести промокод" in r[0]["text"]]
    assert enter_rows and enter_rows[0]["callback_data"] == f"{CB_PROMO_AT}1m:5"


# ── ввод промокода из оплаты → возврат к сводке со скидкой ──────────────────


def test_handle_promo_input_from_order_returns_to_discounted_summary():
    c, uid, iid = _composition()
    _run(c.promo_code_repo.create(_promo(value=50)))
    target = f"{CB_CONFIRM_PAY}1m:5"
    text, _keyboard = _run(_handle_promo_code_input(c, uid, "welcome", target))
    # Успех: промокод закреплён в БД.
    assert _run(c.promo_applied_repo.get(iid)) is not None
    # И вернулись к сводке со скидкой + подтверждайкой.
    assert "Промокод WELCOME применён" in text
    assert "125 ₽" in text and "вместо 249 ₽" in text
    # resolve_active_promo (тот же путь, что при оплате) теперь даёт скидку.
    active = _run(
        resolve_active_promo(
            pool=None,
            promo_applied_repo=c.promo_applied_repo,
            promo_code_repo=c.promo_code_repo,
            promo_redemption_repo=c.promo_redemption_repo,
            snapshots=c.snapshots,
            internal_user_id=iid,
            base_rubles=249,
        )
    )
    assert active is not None and active.price.discounted_rubles == 125


def test_handle_promo_input_from_menu_returns_to_menu():
    c, uid, _iid = _composition()
    _run(c.promo_code_repo.create(_promo(value=50)))
    text, _keyboard = _run(_handle_promo_code_input(c, uid, "welcome", "main_menu"))
    # Возврат в меню: текст успеха (не сводка заказа).
    assert "Промокод WELCOME применён" in text
    assert "вместо" not in text


def test_promo_text_input_summary_has_html_parse_mode():
    """Регрессия: возврат к HTML-сводке заказа после ввода промокода должен уходить с
    parse_mode=HTML, иначе <b>…</b> отрисовывается literal-текстом (Фаза 4)."""
    from app.bot_transport.runtime_facade import (
        _awaiting_promo_code,
        handle_slice1_telegram_update_to_rendered_message,
    )

    c, uid, _iid = _composition()
    _run(c.promo_code_repo.create(_promo(value=50)))
    _awaiting_promo_code[uid] = f"{CB_CONFIRM_PAY}1m:5"
    try:
        update = {
            "update_id": 701,
            "message": {
                "message_id": 1,
                "from": {"id": uid, "is_bot": False, "first_name": "U"},
                "chat": {"id": uid, "type": "private"},
                "text": "welcome",
            },
        }
        pkg = _run(handle_slice1_telegram_update_to_rendered_message(update, c, correlation_id="cid"))
        assert pkg.parse_mode == "HTML"
        assert "<b>" in pkg.message_text
        assert "125 ₽" in pkg.message_text  # 50% от 249
    finally:
        _awaiting_promo_code.pop(uid, None)


# ── buy_vpn_keyboard: скидочные цены по применённому промокоду ──────────────

from app.application.purchase_handler import get_available_plans  # noqa: E402
from app.bot_transport.storefront_ui import (  # noqa: E402
    buy_vpn_keyboard,
    promo_offer_preview,
    text_buy_vpn_intro,
)


def _button_text(kb: dict, plan_id: str) -> str:
    for row in kb["inline_keyboard"]:
        for btn in row:
            if btn["callback_data"] == f"plan:{plan_id}":
                return btn["text"]
    raise AssertionError(f"no button for {plan_id}")


def test_buy_vpn_keyboard_no_promo_plain_prices():
    kb = buy_vpn_keyboard(get_available_plans())
    assert "вместо" not in _button_text(kb, "1m")
    assert _button_text(kb, "1m").endswith("249 ₽")


def test_buy_vpn_keyboard_flat_promo_shows_discount():
    promo = _promo(kind=PromoDiscountKind.PERCENT, value=15)  # −15%
    kb = buy_vpn_keyboard(get_available_plans(), promo=promo)
    txt = _button_text(kb, "1m")
    assert "вместо" in txt  # 249 → 212
    assert "212 ₽ вместо 249" in txt


def test_buy_vpn_keyboard_per_tariff_only_configured_plans():
    promo = _promo(kind=PromoDiscountKind.PERCENT, discounts_by_plan={"1m": 50})
    kb = buy_vpn_keyboard(get_available_plans(), promo=promo)
    assert "вместо" in _button_text(kb, "1m")  # 1m в map → скидка
    assert "вместо" not in _button_text(kb, "3m")  # 3m не в map → полная
    assert "вместо" not in _button_text(kb, "365d")


def test_buy_vpn_intro_mentions_promo_when_applied():
    intro = text_buy_vpn_intro(promo=_promo(code="180TEARZ"))
    assert "180TEARZ" in intro
    assert "со скидкой" in intro
    assert "Промокод" not in text_buy_vpn_intro()  # без промокода — нет строки


def test_promo_offer_preview_per_tariff_has_no_zero():
    # Регресс: per-tariff ранее показывал «Скидка: 0 ₽ от стоимости.»
    txt = promo_offer_preview(_promo(discounts_by_plan={"1m": 50}))
    assert "0 ₽" not in txt
    assert "0%" not in txt
    assert "тариф" in txt.lower()


def test_device_select_shows_promo_discount():
    # Регресс: после выбора тарифа страница устройств показывала полную цену без промокода.
    from app.bot_transport.storefront_ui import text_device_select

    promo = _promo(kind=PromoDiscountKind.PERCENT, value=15)  # −15% от 249 → 212
    txt = text_device_select("1m", 249, 30, 5, promo=promo)
    assert "212 ₽" in txt
    assert "вместо 249" in txt
    # без промокода — обычная цена, без «вместо»
    plain = text_device_select("1m", 249, 30, 5)
    assert "вместо" not in plain
    assert "💳 Итого: 249 ₽" in plain


def test_device_select_per_tariff_only_configured_plan():
    from app.bot_transport.storefront_ui import text_device_select

    promo = _promo(kind=PromoDiscountKind.PERCENT, discounts_by_plan={"1m": 50})
    # 1m в map → скидка; custom → без скидки (per-tariff исключает «Свой»).
    assert "вместо" in text_device_select("1m", 249, 30, 5, promo=promo)
    assert "вместо" not in text_device_select("custom:20", 300, 20, 5, promo=promo)
