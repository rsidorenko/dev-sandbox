"""Customer-facing support copy: short FAQ and validated contact lines only."""

from __future__ import annotations

from app.bot_transport.storefront_config import StorefrontPublicConfig


def get_support_faq_items() -> list[dict[str, str]]:
    """Static FAQ entries; no env reads, no user input."""
    return [
        {
            "key": "pricing",
            "question": "Где посмотреть стоимость?",
            "answer": (
                "Детали тарифа и сумма к оплате отображаются при оформлении до совершения платежа. "
                "Используйте /plans для краткой информации, затем /buy, когда будете готовы."
            ),
        },
        {
            "key": "access",
            "question": "Как получить доступ после оплаты?",
            "answer": (
                "Активация может занять некоторое время после оплаты. "
                "Используйте /my_subscription для проверки статуса, затем /get_access, когда подписка станет активной."
            ),
        },
        {
            "key": "refund",
            "question": "Как насчёт возврата средств?",
            "answer": (
                "Если вам нужен возврат, обратитесь через контактные данные поддержки. "
                "Каждый запрос рассматривается индивидуально."
            ),
        },
    ]


def build_support_menu_text() -> str:
    """Header, FAQ list, and hint toward safe contact command."""
    items = get_support_faq_items()
    lines: list[str] = ["Помощь и поддержка", ""]
    for i, item in enumerate(items, start=1):
        lines.append(f"{i}. {item['question']}")
        lines.append(f"   {item['answer']}")
        lines.append("")
    lines.append("Используйте /support_contact, чтобы связаться с нами.")
    return "\n".join(lines).rstrip() + "\n"


def build_support_contact_text(cfg: StorefrontPublicConfig) -> str:
    """
    Show only storefront fields already validated in :func:`load_storefront_public_config`.
    Never emit raw, unvalidated URLs.
    """
    if not cfg.support_handle and not cfg.support_url:
        return "Поддержка временно недоступна. Пожалуйста, попробуйте позже."
    lines: list[str] = ["Контакты поддержки", ""]
    if cfg.support_handle:
        lines.append(cfg.support_handle)
    if cfg.support_url:
        lines.append(cfg.support_url)
    return "\n".join(lines)
