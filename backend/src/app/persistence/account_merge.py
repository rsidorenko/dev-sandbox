"""Привязка ссылки-подписки к Telegram-аккаунту (merge web-аккаунта по токену).

Когда Telegram-юзер вставляет в бота ссылку-подписку (``subscription_token``)
от web-only аккаунта (отрицательный ``telegram_user_id``), все данные
web-аккаунта переносятся на реальную TG-identity в ОДНОЙ транзакции, токен
перепривязывается на TG-строку, а web-строка identity удаляется. Продукт
(2026-09-20): старая TG-ссылка «отвязывается и удаляется» — у юзера остаётся
ровно одна ссылка, та самая, что он привязал.

Переносимые данные и правила конфликтов — как в историческом email-merge
(``4c048f6^``): реальная TG-identity **выигрывает** каждый конфликт —
подписка (две активные → TG, подписка web адоптируется только если у TG нет
своей активной), issuance/billing едут вместе с адоптированной подпиской,
эксперименты — ранний вариант, реферальный код — TG-wins, баланс — сумма.
Слияние не бросает исключений по данным.

Порядок перепривязки токена важен: ``idx_user_identities_sub_token`` —
partial UNIQUE, поэтому сначала обнуляем токен web-строки, затем назначаем
TG-строке, затем удаляем web-identity (вместе с её ``user_emails`` — FK без
CASCADE — и недостижимыми web-уведомлениями).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime

import asyncpg

_LOGGER = logging.getLogger(__name__)


class BindLinkError(Exception):
    """Отказ привязки ссылки. ``code``:

    * ``token_not_found`` — токен не существует;
    * ``already_linked`` — токен принадлежит Telegram-аккаунту (в т.ч. собственный);
    * ``telegram_identity_missing`` — у TG-юзера нет identity (в боте практически невозможно).
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class LinkAccountPreview:
    """Превью web-аккаунта по токену — для экрана подтверждения в боте."""

    internal_user_id: str
    telegram_user_id: int  # < 0 для web-only
    has_active_subscription: bool
    plan_id: str | None
    active_until_utc: datetime | None
    device_count: int | None
    balance_kopecks: int


@dataclass(frozen=True)
class BindResult:
    """Итог слияния — для итогового сообщения бота."""

    adopted_subscription: bool
    discarded_web_subscription: bool
    balance_kopecks_added: int


async def find_link_account(
    pool: asyncpg.Pool,
    subscription_token: str,
) -> LinkAccountPreview | None:
    """Найти аккаунт по токену ссылки (без ограничений по знаку tg-id).

    ``None`` — токен не существует. Знак ``telegram_user_id`` вызывающий код
    проверяет сам: tg<0 — web-аккаунт можно привязать, tg>0 — уже занят.
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT i.telegram_user_id, i.internal_user_id,"
            " s.state_label, s.plan_id, s.active_until_utc, s.device_count,"
            " COALESCE(b.balance_kopecks, 0) AS balance_kopecks"
            " FROM user_identities i"
            " LEFT JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id"
            " LEFT JOIN referral_balances b ON b.internal_user_id = i.internal_user_id"
            " WHERE i.subscription_token = $1",
            subscription_token,
        )
        if row is None:
            return None
        return LinkAccountPreview(
            internal_user_id=row["internal_user_id"],
            telegram_user_id=int(row["telegram_user_id"]),
            has_active_subscription=row["state_label"] == "active",
            plan_id=row["plan_id"],
            active_until_utc=row["active_until_utc"],
            device_count=row["device_count"],
            balance_kopecks=int(row["balance_kopecks"] or 0),
        )


async def bind_subscription_link(
    pool: asyncpg.Pool,
    telegram_user_id: int,
    subscription_token: str,
) -> BindResult:
    """Слить web-аккаунт (владелец токена, tg<0) в Telegram-identity.

    Бросает :class:`BindLinkError` при отказе; в случае успеха данные web-аккаунта
    перенесены, токен принадлежит TG-строке, web-identity удалена.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            web_row = await conn.fetchrow(
                "SELECT telegram_user_id, internal_user_id FROM user_identities WHERE subscription_token = $1",
                subscription_token,
            )
            if web_row is None:
                raise BindLinkError("token_not_found")
            web_tg = int(web_row["telegram_user_id"])
            if web_tg > 0:
                # Токен уже живёт на Telegram-аккаунте — привязывать нечего
                # (сюда попадает и попытка привязать собственную ссылку).
                raise BindLinkError("already_linked")
            old_id = web_row["internal_user_id"]

            tg_row = await conn.fetchrow(
                "SELECT internal_user_id FROM user_identities WHERE telegram_user_id = $1",
                telegram_user_id,
            )
            if tg_row is None:
                raise BindLinkError("telegram_identity_missing")
            new_id = tg_row["internal_user_id"]
            if new_id == old_id:
                raise BindLinkError("already_linked")

            web_balance = (
                await conn.fetchval(
                    "SELECT balance_kopecks FROM referral_balances WHERE internal_user_id = $1",
                    old_id,
                )
            ) or 0

            # Внутренний аккаунт не становится коммерческим после объединения.
            await conn.execute(
                "WITH changed AS ("
                " UPDATE user_identities SET is_internal = TRUE"
                " WHERE internal_user_id IN ($1, $2) AND NOT is_internal"
                " AND EXISTS (SELECT 1 FROM user_identities WHERE internal_user_id IN ($1, $2) AND is_internal)"
                " RETURNING internal_user_id)"
                " INSERT INTO analytics_internal_audit (internal_user_id, actor, reason, old_value, new_value)"
                " SELECT internal_user_id, 'account_merge', 'Объединение с внутренним аккаунтом', FALSE, TRUE FROM changed",
                old_id,
                new_id,
            )

            # ------------------------------------------------------------------
            # Перенос данных web-аккаунта на реальную identity. Инвариант: TG
            # выигрывает каждый конфликт; все инструкции идемпотентны.
            # ------------------------------------------------------------------

            # --- Подписка: активная приоритетна; ничья → TG выигрывает ---
            web_state = await conn.fetchval(
                "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                old_id,
            )
            tg_state = await conn.fetchval(
                "SELECT state_label FROM subscription_snapshots WHERE internal_user_id = $1",
                new_id,
            )
            adopt_web = False  # True => подписка/issuance/billing web становятся TG
            discarded = False
            if web_state == "active" and tg_state != "active":
                # У web единственная ЖИВАЯ подписка → она становится подпиской TG.
                if tg_state is not None:
                    await conn.execute(
                        "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
                        new_id,
                    )
                await conn.execute(
                    "UPDATE subscription_snapshots SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id,
                    old_id,
                )
                adopt_web = True
            elif web_state is not None and tg_state is not None:
                # Подписка есть у обоих (TG активна, либо не активна ни одна) →
                # TG выигрывает; web-подписка отбрасывается.
                _LOGGER.warning(
                    "account_merge_conflict: both web=%s and tg=%s have subscriptions; keeping telegram",
                    old_id,
                    new_id,
                )
                await conn.execute(
                    "DELETE FROM subscription_snapshots WHERE internal_user_id = $1",
                    old_id,
                )
                discarded = True
            elif web_state is not None:
                # Подписка только у web-аккаунта → адоптация.
                await conn.execute(
                    "UPDATE subscription_snapshots SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id,
                    old_id,
                )
                adopt_web = True

            # --- Issuance + billing: переезжают ТОЛЬКО вместе с адоптированной
            # web-подпиской; иначе у TG есть своя (реально оплаченная), а строки
            # web остаются висеть на old_id (недостижимы после удаления identity).
            if adopt_web:
                # issuance_state PK = (internal_user_id, issue_idempotency_key):
                # выбрасываем web-строки с ключом, уже занятым у TG, остальное переносим.
                await conn.execute(
                    "DELETE FROM issuance_state WHERE internal_user_id = $1"
                    " AND issue_idempotency_key IN"
                    " (SELECT issue_idempotency_key FROM issuance_state WHERE internal_user_id = $2)",
                    old_id,
                    new_id,
                )
                await conn.execute(
                    "UPDATE issuance_state SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id,
                    old_id,
                )
                # Биллинг-таблицы ключуются по id события/факта → перенос безопасен.
                await conn.execute(
                    "UPDATE billing_events_ledger SET internal_user_id = $1"
                    " WHERE internal_user_id = $2 AND internal_user_id IS NOT NULL",
                    new_id,
                    old_id,
                )
                await conn.execute(
                    "UPDATE billing_subscription_apply_audit_events SET internal_user_id = $1"
                    " WHERE internal_user_id = $2 AND internal_user_id IS NOT NULL",
                    new_id,
                    old_id,
                )
                await conn.execute(
                    "UPDATE billing_subscription_apply_records SET internal_user_id = $1 WHERE internal_user_id = $2",
                    new_id,
                    old_id,
                )

            # Эксперимент следует за человеком; при конфликте — самый ранний
            # вариант (первая реальная экспозиция определяет анализ).
            await conn.execute(
                "INSERT INTO experiment_assignments (internal_user_id, experiment_key, variant, assigned_at) "
                "SELECT $1, experiment_key, variant, assigned_at FROM experiment_assignments "
                "WHERE internal_user_id = $2 "
                "ON CONFLICT (internal_user_id, experiment_key) DO UPDATE SET "
                "variant = CASE WHEN EXCLUDED.assigned_at < experiment_assignments.assigned_at "
                "THEN EXCLUDED.variant ELSE experiment_assignments.variant END, "
                "assigned_at = LEAST(EXCLUDED.assigned_at, experiment_assignments.assigned_at)",
                new_id,
                old_id,
            )
            await conn.execute(
                "DELETE FROM experiment_assignments WHERE internal_user_id = $1",
                old_id,
            )

            # --- Рефералка: переносим всегда (идемпотентно, TG выигрывает) ---
            # referral_codes PK = internal_user_id → код web выбрасывается, если у TG есть свой.
            await conn.execute(
                "DELETE FROM referral_codes WHERE internal_user_id = $1"
                " AND EXISTS (SELECT 1 FROM referral_codes WHERE internal_user_id = $2)",
                old_id,
                new_id,
            )
            await conn.execute(
                "UPDATE referral_codes SET internal_user_id = $1 WHERE internal_user_id = $2",
                new_id,
                old_id,
            )
            # referral_balances PK = internal_user_id: баланс web СУММИРУЕТСЯ в TG
            # (заработанная до привязки комиссия не должна теряться).
            await conn.execute(
                "INSERT INTO referral_balances (internal_user_id, balance_kopecks)"
                " SELECT $1, balance_kopecks FROM referral_balances WHERE internal_user_id = $2"
                " ON CONFLICT (internal_user_id) DO UPDATE"
                " SET balance_kopecks = referral_balances.balance_kopecks + EXCLUDED.balance_kopecks,"
                " updated_at = now()",
                new_id,
                old_id,
            )
            await conn.execute(
                "DELETE FROM referral_balances WHERE internal_user_id = $1",
                old_id,
            )
            # referral_transactions UNIQUE(internal_user_id, description) →
            # выбрасываем web-строки с описанием, уже занятым у TG, остальное переносим.
            await conn.execute(
                "DELETE FROM referral_transactions WHERE internal_user_id = $1"
                " AND description IN"
                " (SELECT description FROM referral_transactions WHERE internal_user_id = $2)",
                old_id,
                new_id,
            )
            await conn.execute(
                "UPDATE referral_transactions SET internal_user_id = $1 WHERE internal_user_id = $2",
                new_id,
                old_id,
            )
            # referral_relationships UNIQUE(referred_user_id, level) → на стороне
            # «referred» выбрасываем web-строки на уровнях, уже занятых у TG.
            await conn.execute(
                "DELETE FROM referral_relationships WHERE referred_user_id = $1"
                " AND level IN"
                " (SELECT level FROM referral_relationships WHERE referred_user_id = $2)",
                old_id,
                new_id,
            )
            await conn.execute(
                "UPDATE referral_relationships SET referred_user_id = $1 WHERE referred_user_id = $2",
                new_id,
                old_id,
            )
            # Стороны «referrer»/«referrer_of_referrer» без unique-ограничений → перенос.
            await conn.execute(
                "UPDATE referral_relationships SET referrer_user_id = $1 WHERE referrer_user_id = $2",
                new_id,
                old_id,
            )
            await conn.execute(
                "UPDATE referral_relationships SET referrer_of_referrer_user_id = $1"
                " WHERE referrer_of_referrer_user_id = $2",
                new_id,
                old_id,
            )
            # Self-referral guard: переразметка выше может замкнуть петлю —
            # строки referrer_user_id = referred_user_id бизнес-смысла не имеют.
            await conn.execute("DELETE FROM referral_relationships WHERE referrer_user_id = referred_user_id")

            # --- Промокоды: закрепление (PK=internal_user_id) адоптируется, если
            # у TG своего нет; история погашений переносится (UNIQUE(code,
            # internal_user_id) → TG-wins дедуп), чтобы once-per-user код нельзя
            # было погасить повторно после привязки.
            await conn.execute(
                "INSERT INTO promo_applied (internal_user_id, code, applied_at, expires_at)"
                " SELECT $1, code, applied_at, expires_at FROM promo_applied WHERE internal_user_id = $2"
                " ON CONFLICT (internal_user_id) DO NOTHING",
                new_id,
                old_id,
            )
            await conn.execute("DELETE FROM promo_applied WHERE internal_user_id = $1", old_id)
            await conn.execute(
                "DELETE FROM promo_redemptions WHERE internal_user_id = $1"
                " AND code IN (SELECT code FROM promo_redemptions WHERE internal_user_id = $2)",
                old_id,
                new_id,
            )
            await conn.execute(
                "UPDATE promo_redemptions SET internal_user_id = $1 WHERE internal_user_id = $2",
                new_id,
                old_id,
            )

            # --- Web-колокольчик: после удаления web-identity уведомления
            # приходят только в бот — висячие строки web_notifications чистим.
            await conn.execute("DELETE FROM web_notifications WHERE internal_user_id = $1", old_id)

            # --- Email web-аккаунта: user_emails держит FK на user_identities
            # (без CASCADE), а почта по продукту — только канал доставки первой
            # ссылки; привязка к TG её не наследует.
            await conn.execute("DELETE FROM user_emails WHERE telegram_user_id = $1", web_tg)

            # --- Перепривязка токена (unique-index-безопасный порядок) и
            # удаление web-строки. FOR UPDATE обеих строк — защита от гонки
            # двойного клика «Подтвердить» в боте.
            await conn.execute(
                "SELECT 1 FROM user_identities WHERE internal_user_id = ANY($1::text[]) FOR UPDATE",
                [old_id, new_id],
            )
            await conn.execute(
                "UPDATE user_identities SET subscription_token = NULL WHERE internal_user_id = $1",
                old_id,
            )
            await conn.execute(
                "UPDATE user_identities SET subscription_token = $1 WHERE internal_user_id = $2",
                subscription_token,
                new_id,
            )
            await conn.execute("DELETE FROM user_identities WHERE internal_user_id = $1", old_id)

            _LOGGER.info(
                "account_merge: merged web=%s into tg=%s by subscription link",
                old_id,
                new_id,
            )
            return BindResult(
                adopted_subscription=adopt_web,
                discarded_web_subscription=discarded,
                balance_kopecks_added=int(web_balance),
            )
