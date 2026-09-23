"""Guard: триал выдаётся только в Telegram-боте — web-эндпоинта активации нет.

Инвариант приватности/продукта: сайт не активирует пробный период (см. план
«триал только в TG-боте»). Если роут вернётся — тест упадёт и заставит
перечитать решение.
"""

from __future__ import annotations

from app.web_api.app import build_web_api_app


class _FakePool:
    """Пул не нужен: маршруты строятся до любого обращения к БД."""


def test_web_trial_activate_route_absent() -> None:
    app = build_web_api_app(pool=_FakePool())  # type: ignore[arg-type]
    paths = [getattr(route, "path", "") for route in app.routes]
    assert "/api/v1/user/trial/activate" not in paths
    assert not any("trial" in path and "activate" in path for path in paths), paths
