"""Версия оферты и явное подтверждение до создания заказа."""

from starlette.responses import JSONResponse

OFFER_VERSION = "2026-09-30"


def validate_offer(data: dict) -> JSONResponse | None:
    if data.get("offer_accepted") is not True or data.get("offer_version") != OFFER_VERSION:
        return JSONResponse(
            {"ok": False, "error": "offer_acceptance_required", "offer_version": OFFER_VERSION}, status_code=400
        )
    return None


def validate_price(data: dict, amount_kopecks: int) -> JSONResponse | None:
    if type(data.get("expected_amount_kopecks")) is not int or data["expected_amount_kopecks"] != amount_kopecks:
        return JSONResponse({"ok": False, "error": "price_changed", "amount_kopecks": amount_kopecks}, status_code=409)
    return None
