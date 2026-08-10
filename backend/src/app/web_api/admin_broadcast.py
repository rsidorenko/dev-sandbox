"""Админ-эндпоинты вкладки «Рассылка»: оценка аудитории, запуск фоновой рассылки,
контроль прогресса и пред-проверка форматирования.

Модель:
  * оператор выбирает одну или несколько категорий получателей (мн. выбор → UNION)
    плюс точечные цели (telegram id / email);
  * сообщение пишется в HTML (parse_mode="HTML"): тулбар фронта оборачивает выделение
    в <code>/<a href> и т.п. — здесь нужен pre-flight _validate_html, чтобы битая
    разметка не привела к массовому 400 на всех получателях;
  * отправка — фоновая BroadcastRunner (последовательная, с троттлингом и обработкой
    429/403/400). Одна активная рассылка за раз (409 broadcast_in_progress).

Канон admin-handler-а — admin_promocodes.py (@_with_admin_auth + @_with_admin_csrf,
safe_json_error, _admin_actor). SQL — как в admin_users.py (литералы now()/интервалов,
единственный параметр $1 = массив явных tg-id).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import uuid
from datetime import UTC, datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import asyncpg
import httpx
from starlette.requests import Request
from starlette.responses import JSONResponse

from app.bot_transport.storefront_ui import (
    CB_BROADCAST_DAYS,
    CB_BROADCAST_PROMO,
    CB_BUY_VPN,
    CB_HELP,
    CB_MAIN_MENU,
    CB_MY_SUB,
    CB_PROMOCODE,
    _cb,
)
from app.domain.promo import normalize_code, validate_code_format
from app.web_api.admin_auth import _admin_actor, _with_admin_auth, _with_admin_csrf
from app.web_api.helpers import safe_json_error

_LOGGER = logging.getLogger(__name__)

# --- Параметры отправки (env-перекрываемые) ---------------------------------
_MAX_MESSAGE_LEN = 4096  # лимит Telegram на текст сообщения
_DEFAULT_MAX_RECIPIENTS = 50_000
_DEFAULT_THROTTLE_SEC = 0.05  # ~20 msg/s — ниже глобального лимита Bot API
_LIST_DEFAULT_LIMIT = 50
_LIST_MAX_LIMIT = 200
_BATCH_FLUSH_EVERY = 20  # флеш счётчиков в БД каждые N отправок
_MAX_BUTTONS = 8  # суммарный лимит кнопок под сообщением (действия + ссылки)

# --- Бонус-кнопка рассылки (бесплатные дни) ---------------------------------
_BONUS_DAYS_MIN = 3  # = длительность триала: бонус никогда не «хуже» триала
_BONUS_DAYS_MAX = 30
_BONUS_TTL_MIN = 1
_BONUS_TTL_MAX = 365
_BONUS_DEFAULT_TTL_DAYS = 30  # срок годности eligible-claim (anti-abuse: старая кнопка протухает)

# Курированные действия бота: callback_data (из storefront_ui.CB_*) → подпись с эмодзи.
# Клик по такой кнопке открывает экран бота и заменяет текст сообщения рассылки
# (edit_message_text по этому сообщению — как у lifecycle-уведомлений).
_ACTION_BUTTONS: dict[str, str] = {
    CB_BUY_VPN: "🔑 Купить VPN",
    CB_MAIN_MENU: "🏠 Главное меню",
    CB_PROMOCODE: "🎟 Ввести промокод",
    CB_MY_SUB: "📋 Моя подписка",
    CB_HELP: "❓ Помощь",
}
_URL_BUTTON_MAX_LEN = 512
_BUTTON_LABEL_MAX_LEN = 64  # лимит Telegram на text кнопки


def _max_recipients() -> int:
    try:
        return max(1, int(os.environ.get("BROADCAST_MAX_RECIPIENTS", str(_DEFAULT_MAX_RECIPIENTS))))
    except ValueError:
        return _DEFAULT_MAX_RECIPIENTS


def _throttle_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("BROADCAST_THROTTLE_SEC", str(_DEFAULT_THROTTLE_SEC))))
    except ValueError:
        return _DEFAULT_THROTTLE_SEC


# --- Категории аудитории -----------------------------------------------------
# Каждый предикат — самодостаточный SELECT telegram_user_id с фильтром > 0
# (web-only с отрицательным telegram_user_id выкидываются — у них нет TG-чата).
# now()/интервалы — литералы, единственный параметр $1 (массив явных tg-id)
# подставляется во внешнем unnest — как в admin_users._LIST_*.

_CATEGORY_PREDICATES: dict[str, str] = {
    "all": ("SELECT i.telegram_user_id FROM user_identities i WHERE i.telegram_user_id > 0"),
    "active": (
        "SELECT i.telegram_user_id FROM user_identities i "
        "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
        "WHERE i.telegram_user_id > 0 AND ("
        "(s.plan_id IS NOT NULL AND s.state_label = 'active' AND s.active_until_utc IS NOT NULL AND s.active_until_utc > now())"
        " OR (s.plan_id IS NULL AND s.trial_expires_at IS NOT NULL AND s.trial_expires_at > now())"
        ")"
    ),
    "expired_0_2d": (
        "SELECT i.telegram_user_id FROM user_identities i "
        "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
        "WHERE i.telegram_user_id > 0 "
        "AND s.plan_id IS NOT NULL AND s.active_until_utc IS NOT NULL "
        "AND s.active_until_utc <= now() AND s.active_until_utc > now() - INTERVAL '3 days'"
    ),
    "expired_3_5d": (
        "SELECT i.telegram_user_id FROM user_identities i "
        "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
        "WHERE i.telegram_user_id > 0 "
        "AND s.plan_id IS NOT NULL AND s.active_until_utc IS NOT NULL "
        "AND s.active_until_utc <= now() - INTERVAL '3 days' AND s.active_until_utc > now() - INTERVAL '6 days'"
    ),
    "expired_6_7d": (
        "SELECT i.telegram_user_id FROM user_identities i "
        "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
        "WHERE i.telegram_user_id > 0 "
        "AND s.plan_id IS NOT NULL AND s.active_until_utc IS NOT NULL "
        "AND s.active_until_utc <= now() - INTERVAL '6 days' AND s.active_until_utc > now() - INTERVAL '8 days'"
    ),
    "expired_8_30d": (
        "SELECT i.telegram_user_id FROM user_identities i "
        "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
        "WHERE i.telegram_user_id > 0 "
        "AND s.plan_id IS NOT NULL AND s.active_until_utc IS NOT NULL "
        "AND s.active_until_utc <= now() - INTERVAL '8 days' AND s.active_until_utc > now() - INTERVAL '31 days'"
    ),
    "expired_30plus": (
        "SELECT i.telegram_user_id FROM user_identities i "
        "JOIN subscription_snapshots s ON s.internal_user_id = i.internal_user_id "
        "WHERE i.telegram_user_id > 0 "
        "AND s.plan_id IS NOT NULL AND s.active_until_utc IS NOT NULL "
        "AND s.active_until_utc <= now() - INTERVAL '31 days'"
    ),
    "dormant": (
        "SELECT i.telegram_user_id FROM user_identities i WHERE i.telegram_user_id > 0 "
        "AND COALESCE(i.trial_used, FALSE) = FALSE "
        "AND NOT EXISTS (SELECT 1 FROM billing_events_ledger b "
        "WHERE b.internal_user_id = i.internal_user_id AND b.status = 'accepted') "
        "AND NOT EXISTS (SELECT 1 FROM subscription_snapshots s "
        "WHERE s.internal_user_id = i.internal_user_id AND s.plan_id IS NOT NULL "
        "AND s.state_label = 'active' AND s.active_until_utc IS NOT NULL AND s.active_until_utc > now())"
    ),
}

_ALLOWED_CATEGORIES = frozenset(_CATEGORY_PREDICATES)


def _build_inner_union(categories: list[str]) -> str:
    """Внутренний UNION ALL выбранных категорий + явные цели (unnest $1)."""
    arms = [_CATEGORY_PREDICATES[c] for c in categories if c in _CATEGORY_PREDICATES]
    arms.append("SELECT unnest($1::bigint[]) AS telegram_user_id")
    return " UNION ALL ".join(arms)


def _count_sql(categories: list[str]) -> str:
    inner = _build_inner_union(categories)
    return f"SELECT COUNT(DISTINCT telegram_user_id) AS n FROM ({inner}) t WHERE telegram_user_id > 0"  # noqa: S608 — литеральные предикаты из allowlist + единственный параметр $1


def _fetch_sql(categories: list[str]) -> str:
    inner = _build_inner_union(categories)
    return f"SELECT DISTINCT telegram_user_id FROM ({inner}) t WHERE telegram_user_id > 0"  # noqa: S608 — литеральные предикаты из allowlist + единственный параметр $1


async def _count_category(pool: asyncpg.Pool, key: str) -> int:
    """Счётчик одной категории (без дедупа с другими) — для per_category_counts."""
    return int(await pool.fetchval(f"SELECT COUNT(*) FROM ({_CATEGORY_PREDICATES[key]}) t") or 0)  # noqa: S608 — key из allowlist, предикат литеральный


async def _resolve_explicit_targets(pool: asyncpg.Pool, raw: list[str]) -> tuple[list[int], list[str]]:
    """Числа → telegram id (>0); прочее трактуется как email и резолвится через
    user_emails (верифицированные). Возвращает (resolved_ids, unresolved_tokens).
    """
    resolved: list[int] = []
    unresolved: list[str] = []
    email_tokens: list[str] = []
    for token in raw:
        t = (token or "").strip()
        if not t:
            continue
        if t.lstrip("+").isdigit():  # telegram id (отрицательные/ноль отбрасываем ниже)
            try:
                value = int(t)
            except ValueError:
                unresolved.append(t)
                continue
            if value > 0:
                resolved.append(value)
            else:
                unresolved.append(t)
            continue
        # иначе — считаем email (точное совпадение, case-insensitive)
        email_tokens.append(t)

    for email in email_tokens:
        tg = await pool.fetchval(
            "SELECT telegram_user_id FROM user_emails "
            "WHERE LOWER(email) = LOWER($1) AND is_verified = TRUE AND telegram_user_id > 0",
            email,
        )
        if tg is not None:
            resolved.append(int(tg))
        else:
            unresolved.append(email)

    return resolved, unresolved


def _validate_categories(raw: Any) -> list[str] | None:
    if not isinstance(raw, list):
        return None
    cats = [c for c in ({str(c) for c in raw} if raw else []) if c]
    bad = [c for c in cats if c not in _ALLOWED_CATEGORIES]
    if bad:
        return None
    return cats


# --- Pre-flight валидация HTML ----------------------------------------------
# Allowlist тегов Telegram HTML + обязательный href для <a>. Ловит битую разметку
# ДО массовой отправки, чтобы один 400 не повторился на всех получателях.

_ALLOWED_HTML_TAGS = frozenset(
    {"b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "code", "pre", "blockquote", "a", "br"}
)
_ALLOWED_HREF_SCHEMES = frozenset({"http", "https", "mailto", "tg"})


class _HtmlAudit(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[str] = []
        self.error: str | None = None

    def _check(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag not in _ALLOWED_HTML_TAGS:
            self.error = f"unsupported_tag:{tag}"
            return
        if tag == "a":
            href: str | None = None
            for k, v in attrs:
                if k == "href" and v:
                    href = v
                    break
            if not href:
                self.error = "link_missing_href"
                return
            scheme = urlparse(href).scheme.lower()
            # Относительные ссылки без схемы — запрещены (нечего кликать в Telegram).
            if scheme not in _ALLOWED_HREF_SCHEMES:
                self.error = f"link_bad_scheme:{scheme or 'none'}"
                return

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.error is not None:
            return
        self._check(tag, attrs)
        if self.error is None and tag not in ("br",):
            self.stack.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.error is not None:
            return
        self._check(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if self.error is not None:
            return
        if tag in self.stack:
            while self.stack and self.stack[-1] != tag:
                self.stack.pop()
            if self.stack:
                self.stack.pop()


def _validate_html(text: str) -> str | None:
    """Возвращает код ошибки или None, если HTML валиден."""
    audit = _HtmlAudit()
    try:
        audit.feed(text)
        audit.close()
    except Exception:
        return "malformed_html"
    # audit.error — неподдерживаемый тег / плохая href (блокируем).
    # Незакрытые разрешённые теги НЕ блокируем — автозакроются в _html_for_telegram.
    return audit.error


_BR_RE = re.compile(r"<br\b[^>]*>", re.IGNORECASE)


def _html_for_telegram(text: str) -> str:
    """Нормализация HTML под Telegram перед отправкой:
    * `<br>` (в т.ч. с атрибутами/самозакрывающийся) → `\\n` — Telegram HTML
      не поддерживает <br> ('Unsupported start tag \"br\"');
    * автозакрытие незакрытых тегов allowlist'а — Telegram строго требует
      баланса ('Can't find end tag'); прощаем оператору забытый </b> и т.п.
    """
    text = _BR_RE.sub("\n", text)
    audit = _HtmlAudit()
    try:
        audit.feed(text)
        audit.close()
    except Exception:
        return text  # пусть Telegram вернёт свою ошибку
    if audit.stack:
        text = text + "".join(f"</{t}>" for t in reversed(audit.stack))
    return text


# --- Кнопки под сообщением ---------------------------------------------------
def _validate_buttons(data: dict) -> tuple[list[dict], JSONResponse | None]:
    """Из тела запроса собирает итоговый список кнопок в формате Telegram.
    actions ({text,callback_data}) идут раньше url-кнопок ({text,url}). Возвращает
    (buttons, None) при успехе или (None, error_response).
    """
    buttons: list[dict] = []

    raw_actions = data.get("action_buttons") or []
    if not isinstance(raw_actions, list):
        return [], safe_json_error(400, "invalid_action_buttons")
    # Дедуп + сохранение порядка, только известные callback_data.
    seen: set[str] = set()
    for key in raw_actions:
        k = str(key)
        if k in _ACTION_BUTTONS and k not in seen:
            seen.add(k)
            buttons.append({"text": _ACTION_BUTTONS[k], "callback_data": k})

    raw_urls = data.get("url_buttons") or []
    if not isinstance(raw_urls, list):
        return [], safe_json_error(400, "invalid_url_buttons")
    for entry in raw_urls:
        if not isinstance(entry, dict):
            return [], safe_json_error(400, "invalid_url_buttons")
        label = str(entry.get("label") or "").strip()
        url = str(entry.get("url") or "").strip()
        if not label or not url:
            return [], safe_json_error(400, "invalid_url_buttons", "label и url обязательны")
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            return [], safe_json_error(400, "invalid_button_url", "допустимы только http/https")
        if len(url) > _URL_BUTTON_MAX_LEN:
            return [], safe_json_error(400, "invalid_button_url", "url слишком длинный")
        buttons.append({"text": label[:_BUTTON_LABEL_MAX_LEN], "url": url})

    if len(buttons) > _MAX_BUTTONS:
        return [], safe_json_error(400, "too_many_buttons", f"максимум {_MAX_BUTTONS} кнопок")
    return buttons, None


def _build_reply_markup(buttons: list[dict] | None) -> dict | None:
    """Каждая кнопка = отдельная строка inline_keyboard (как в notification_texts)."""
    if not buttons:
        return None
    return {"inline_keyboard": [[dict(b)] for b in buttons]}


# --- Бонус-кнопка (бесплатные дни) -------------------------------------------
def _validate_bonus_config(raw: Any) -> tuple[dict | None, JSONResponse | None]:
    """bonus = {type:"days", days:3..30, label, ttl_days?} | {type:"promo", code, label, ttl_days?}.
    Чистая (без DB): формат-валидация. Существование/активность промокода проверяется отдельно
    (_validate_bonus_promo_exists в send/test). Возвращает нормализованный словарь или
    (None, error_response). (None, None) — бонуса нет, это валидно.
    """
    if raw is None or raw == {}:
        return None, None
    if not isinstance(raw, dict):
        return None, safe_json_error(400, "invalid_bonus")
    btype = str(raw.get("type") or "").strip()
    if btype not in ("days", "promo"):
        return None, safe_json_error(400, "invalid_bonus_type", "поддерживается type=days|promo")

    label = str(raw.get("label") or "").strip()
    if not label:
        return None, safe_json_error(400, "invalid_bonus_label", "label обязателен")

    ttl_days = _BONUS_DEFAULT_TTL_DAYS
    raw_ttl = raw.get("ttl_days")
    if raw_ttl is not None:
        try:
            ttl_days = int(raw_ttl)
        except (TypeError, ValueError):
            return None, safe_json_error(400, "invalid_bonus_ttl")
        if not (_BONUS_TTL_MIN <= ttl_days <= _BONUS_TTL_MAX):
            return None, safe_json_error(400, "invalid_bonus_ttl")

    if btype == "days":
        try:
            days = int(raw.get("days"))
        except (TypeError, ValueError):
            return None, safe_json_error(400, "invalid_bonus_days")
        if not (_BONUS_DAYS_MIN <= days <= _BONUS_DAYS_MAX):
            return None, safe_json_error(
                400, "invalid_bonus_days", f"days должен быть {_BONUS_DAYS_MIN}..{_BONUS_DAYS_MAX}"
            )
        return (
            {"type": "days", "days": days, "label": label[:_BUTTON_LABEL_MAX_LEN], "ttl_days": ttl_days},
            None,
        )

    # type == "promo": формат кода (существование/активность — в _validate_bonus_promo_exists).
    code = normalize_code(str(raw.get("code") or ""))
    fmt_err = validate_code_format(code)
    if fmt_err is not None:
        return None, safe_json_error(400, "invalid_bonus_code", fmt_err)
    return (
        {"type": "promo", "code": code, "label": label[:_BUTTON_LABEL_MAX_LEN], "ttl_days": ttl_days},
        None,
    )


async def _validate_bonus_promo_exists(pool: asyncpg.Pool, bonus: dict | None) -> tuple[JSONResponse | None, list[str]]:
    """Для bonus.type="promo": проверить существование/активность/не-истечение/не-исчерпание кода.
    Возвращает (error_response|None, warnings). warnings — non-blocking подсказки админу
    (audience≠all_users / promo.expires_at в пределах TTL кнопки). Best-effort: redemptions_count
    растёт между send и кликом — финальную проверку делает redeem_block_reason при клике и
    resolve_active_promo при оплате. Для bonus=None или type!="promo" — (None, []).
    """
    if bonus is None or bonus.get("type") != "promo":
        return None, []
    code = str(bonus.get("code") or "")
    ttl_days = int(bonus.get("ttl_days", _BONUS_DEFAULT_TTL_DAYS))
    row = await pool.fetchrow(
        "SELECT audience, is_active, expires_at, max_redemptions, redemptions_count FROM promo_codes WHERE code = $1",
        code,
    )
    if row is None or not bool(row["is_active"]):
        return safe_json_error(400, "bonus_promo_not_found", "Промокод не найден или неактивен"), []
    expires_at = row["expires_at"]
    if expires_at is not None and expires_at <= datetime.now(UTC):
        return safe_json_error(400, "bonus_promo_expired", "Срок действия промокода истёк"), []
    max_red = row["max_redemptions"]
    if max_red is not None and int(row["redemptions_count"]) >= int(max_red):
        return safe_json_error(400, "bonus_promo_exhausted", "Лимит погашений промокода исчерпан"), []

    warnings: list[str] = []
    if str(row["audience"]) != "all_users":
        warnings.append(
            f"Аудитория промокода — {row['audience']}: часть получателей (не подходящих) увидит «промокод недоступен»."
        )
    if expires_at is not None:
        days_left = (expires_at - datetime.now(UTC)).days
        if 0 <= days_left <= ttl_days:
            warnings.append(
                f"Срок промокода истекает через ~{days_left} дн., а кнопка активна {ttl_days} дн.: "
                f"часть получателей нажмёт уже после истечения."
            )
    return None, warnings


def _decode_bonus(raw: Any) -> dict | None:
    """JSONB bonus из строки broadcast_jobs → dict или None. Поддерживает type="days" и "promo".
    Используется в Runner.run(), а также в /status и /list API (видимость промокода на рассылке).
    asyncpg без jsonb-кодека отдаёт str (нужен json.loads). См. _decode_buttons.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        if not raw.strip():
            return None
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return None
    if not isinstance(raw, dict):
        return None
    btype = raw.get("type")
    label = raw.get("label")
    if not isinstance(label, str):
        return None
    # ttl_days защищаем от битого JSONB: иначе int("abc") роняет Runner.run() и /status API.
    # Запись всегда валидна через _validate_bonus_config — это defense-in-depth.
    try:
        ttl_days = int(raw.get("ttl_days", _BONUS_DEFAULT_TTL_DAYS))
    except (TypeError, ValueError):
        ttl_days = _BONUS_DEFAULT_TTL_DAYS
    if btype == "days":
        days = raw.get("days")
        if not isinstance(days, int):
            return None
        return {"type": "days", "days": days, "label": label, "ttl_days": ttl_days}
    if btype == "promo":
        code = raw.get("code")
        if not isinstance(code, str):
            return None
        return {"type": "promo", "code": code, "label": label, "ttl_days": ttl_days}
    return None


# --- Reconcile orphan-задач --------------------------------------------------
# Без правки lifespan: один раз на процесс перевести зависшие running/queued
# (остались от упавшего процесса) в interrupted.

_RECONCILED_POOLS: set[int] = set()
_RECONCILE_LOCK = asyncio.Lock()


async def _maybe_reconcile_orphan_jobs(pool: asyncpg.Pool) -> None:
    if id(pool) in _RECONCILED_POOLS:
        return
    async with _RECONCILE_LOCK:
        if id(pool) in _RECONCILED_POOLS:
            return
        try:
            await pool.execute(
                "UPDATE broadcast_jobs SET status='interrupted', finished_at=NOW(), "
                "last_error='process_restart' WHERE status IN ('running','queued')"
            )
        except Exception:
            _LOGGER.exception("broadcast orphan reconcile failed")
        _RECONCILED_POOLS.add(id(pool))


# --- Разбор ответа Telegram --------------------------------------------------
def _response_json(response: httpx.Response) -> dict | None:
    try:
        data = response.json()
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _extract_retry_after(response: httpx.Response) -> int:
    params = (_response_json(response) or {}).get("parameters")
    if isinstance(params, dict):
        ra = params.get("retry_after")
        if isinstance(ra, int | float) and ra > 0:
            return int(ra)
    return 1


def _describe_response(response: httpx.Response) -> str:
    return str((_response_json(response) or {}).get("description") or "")


def _classify_bad_request(description: str) -> str:
    """400 делится на два класса:
    * content  — битая разметка/длина («can't parse entities», «too long») → повторится
      на всех получателях, поэтому аварийно стопим всю рассылку;
    * recipient — «chat not found», «PEER_ID_INVALID» и т.п. → проблема одного чата,
      помечаем failed и идём дальше.
    """
    d = (description or "").lower()
    if any(k in d for k in ("entit", "parse", "too long", "format", "markdown")):
        return "bad_request_content"
    return "bad_request_recipient"


# --- Фоновая задача рассылки -------------------------------------------------
class BroadcastRunner:
    """Последовательная отправка по списку получателей с троттлингом и обработкой
    ответов Telegram. Состояние — в broadcast_jobs (счётчики, статус).

    client — HttpxTelegramRawPollingClient (или фейк в тестах). Конструктор НЕ ходит
    в БД; конфиг задачи (текст/категории/цели) читается из строки в run().
    """

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        client: Any,
        job_id: str,
        sleep: Any = asyncio.sleep,
    ) -> None:
        self._pool = pool
        self._client = client
        self._job_id = job_id
        self._sleep = sleep
        self._throttle = _throttle_sec()

    async def run(self) -> None:
        # Перевод queued → running (CAS: только если ещё queued; иначе — отмена/уже идёт).
        flipped = await self._pool.execute(
            "UPDATE broadcast_jobs SET status='running', started_at=NOW() WHERE job_id=$1 AND status='queued'",
            self._job_id,
        )
        if flipped.endswith(" 0"):
            _LOGGER.info("broadcast job %s preempted before start", self._job_id[:8])
            return

        job = await self._pool.fetchrow(
            "SELECT categories, explicit_telegram_ids, message_text, parse_mode, "
            "disable_web_page_preview, buttons, bonus FROM broadcast_jobs WHERE job_id=$1",
            self._job_id,
        )
        if job is None:
            return

        categories = list(job["categories"] or [])
        explicit_ids = [int(x) for x in (job["explicit_telegram_ids"] or []) if int(x) > 0]
        text: str = job["message_text"]
        parse_mode: str = job["parse_mode"] or "HTML"
        disable_preview: bool = bool(job["disable_web_page_preview"])
        bonus = _decode_bonus(job.get("bonus"))
        buttons = _decode_buttons(job["buttons"])
        if bonus is not None:
            # Бонус-кнопка добавляется последней строкой; callback_data несёт только job_id —
            # параметры бонуса (дни/код) лежат в claims-таблице, а не в callback (приватность
            # + укладывается в 64-байт лимит Telegram: "bc_days:"/"bc_promo:" + UUID ≤ 45 байт).
            cb_prefix = CB_BROADCAST_PROMO if bonus["type"] == "promo" else CB_BROADCAST_DAYS
            buttons.append({"text": bonus["label"], "callback_data": _cb(cb_prefix, self._job_id)})
        reply_markup = _build_reply_markup(buttons)

        sent = blocked = failed = 0
        try:
            recipients = await self._fetch_recipients(categories, explicit_ids)
            await self._pool.execute(
                "UPDATE broadcast_jobs SET total_recipients=$2 WHERE job_id=$1",
                self._job_id,
                len(recipients),
            )
            if not recipients:
                await self._finish("completed", sent, blocked, failed, None)
                return

            # Бонус: заполняем claims СТРОГО для реальных получателей и ДО цикла отправки
            # (инвариант: получатель не может нажать раньше, чем claim записан). Этим
            # обеспечивается «только получатели» + срок годности. Идемпотентность дней —
            # atomic UPDATE eligible→claimed в обработчике; промокод — apply идемпотентен
            # через promo_applied PK=internal_user_id (см. _handle_broadcast_promo_claim).
            if bonus is not None:
                tg_ids = [int(r["telegram_user_id"]) for r in recipients]
                if bonus["type"] == "promo":
                    await self._seed_promo_claims(bonus, tg_ids)
                else:
                    await self._seed_bonus_claims(bonus, tg_ids)

            since_flush = 0
            for row in recipients:
                chat_id = int(row["telegram_user_id"])
                status = await self._send_one(chat_id, text, parse_mode, disable_preview, reply_markup)
                if status == "ok":
                    sent += 1
                elif status == "blocked":
                    blocked += 1
                elif status == "bad_request_content":
                    # Битая разметка/длина — повторится на всех получателях. Аварийный стоп.
                    failed += 1
                    await self._finish("failed", sent, blocked, failed, "bad_request: abort to avoid mass 400")
                    return
                else:  # bad_request_recipient / rate_limited / transient — проблема одного чата
                    failed += 1
                    if status == "rate_limited":
                        await self._sleep(5.0)

                since_flush += 1
                if since_flush >= _BATCH_FLUSH_EVERY:
                    await self._flush(sent, blocked, failed)
                    since_flush = 0

                if self._throttle > 0:
                    await self._sleep(self._throttle)

            await self._flush(sent, blocked, failed)
            await self._finish("completed", sent, blocked, failed, None)

        except asyncio.CancelledError:
            await self._finish("interrupted", sent, blocked, failed, "cancelled")
            raise
        except Exception as exc:
            _LOGGER.exception("broadcast job %s crashed", self._job_id[:8])
            await self._finish("failed", sent, blocked, failed, str(exc)[:300])

    async def _fetch_recipients(self, categories: list[str], explicit_ids: list[int]) -> list[asyncpg.Record]:
        sql = _fetch_sql(categories)
        return await self._pool.fetch(sql, explicit_ids)

    async def _seed_bonus_claims(self, bonus: dict, tg_ids: list[int]) -> None:
        """Bulk-INSERT eligible-claims для реальных получателей (одна транзакция, ON CONFLICT
        DO NOTHING — страховка от теоретического re-run того же job_id: уже-claimed строки не
        повреждаются). expires_at = NOW()+ttl_days (anti-abuse: старая кнопка протухает).
        """
        if not tg_ids:
            return
        days = int(bonus["days"])
        ttl_days = int(bonus.get("ttl_days", _BONUS_DEFAULT_TTL_DAYS))
        sql = (
            "INSERT INTO broadcast_bonus_claims "
            "(job_id, telegram_user_id, status, days, expires_at) "
            "VALUES ($1, $2, 'eligible', $3, NOW() + make_interval(days => $4)) "
            "ON CONFLICT (job_id, telegram_user_id) DO NOTHING"
        )
        rows = [(self._job_id, tg, days, ttl_days) for tg in tg_ids]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(sql, rows)

    async def _seed_promo_claims(self, bonus: dict, tg_ids: list[int]) -> None:
        """Bulk-INSERT реестра получателей промокода (одна транзакция, ON CONFLICT DO NOTHING —
        страховка от re-run того же job_id). Без status-машины: apply промокода идемпотентен сам
        по себе (promo_applied PK=internal_user_id). expires_at = NOW()+ttl_days (срок годности кнопки).
        """
        if not tg_ids:
            return
        code = str(bonus["code"])
        ttl_days = int(bonus.get("ttl_days", _BONUS_DEFAULT_TTL_DAYS))
        sql = (
            "INSERT INTO broadcast_promo_claims "
            "(job_id, telegram_user_id, code, expires_at) "
            "VALUES ($1, $2, $3, NOW() + make_interval(days => $4)) "
            "ON CONFLICT (job_id, telegram_user_id) DO NOTHING"
        )
        rows = [(self._job_id, tg, code, ttl_days) for tg in tg_ids]
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.executemany(sql, rows)

    async def _send_one(
        self,
        chat_id: int,
        text: str,
        parse_mode: str,
        disable_preview: bool,
        reply_markup: dict | None,
    ) -> str:
        """Возвращает 'ok' | 'blocked' | 'bad_request_content' | 'bad_request_recipient'
        | 'rate_limited' | 'transient'. 429 — ретрай с retry_after (до 2 повторных попыток)."""
        attempts = 0
        while True:
            attempts += 1
            try:
                await self._client.send_text_message(
                    chat_id=chat_id,
                    text=text,
                    correlation_id=f"broadcast-{self._job_id[:8]}",
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_preview,
                    reply_markup=reply_markup,
                )
                return "ok"
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code
                if code == 403:
                    return "blocked"
                if code == 400:
                    return _classify_bad_request(_describe_response(exc.response))
                if code == 429 and attempts <= 2:
                    retry_after = _extract_retry_after(exc.response)
                    await self._sleep(min(max(retry_after, 1), 30) + 0.2)
                    continue
                if code == 429:
                    return "rate_limited"
                if attempts <= 2:  # прочие 5xx/неожиданные — короткий ретрай
                    await self._sleep(0.5 * attempts)
                    continue
                return "transient"
            except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteError, httpx.PoolTimeout):
                if attempts <= 3:
                    await self._sleep(0.5 * attempts)
                    continue
                return "transient"
            except Exception:
                if attempts <= 2:
                    await self._sleep(0.5 * attempts)
                    continue
                return "transient"

    async def _flush(self, sent: int, blocked: int, failed: int) -> None:
        await self._pool.execute(
            "UPDATE broadcast_jobs SET sent_count=$2, blocked_count=$3, failed_count=$4 WHERE job_id=$1",
            self._job_id,
            sent,
            blocked,
            failed,
        )

    async def _finish(self, status: str, sent: int, blocked: int, failed: int, last_error: str | None) -> None:
        await self._pool.execute(
            "UPDATE broadcast_jobs SET status=$2, sent_count=$3, blocked_count=$4, "
            "failed_count=$5, finished_at=NOW(), last_error=$6 WHERE job_id=$1",
            self._job_id,
            status,
            sent,
            blocked,
            failed,
            last_error,
        )


# --- Сериализация строки задачи ---------------------------------------------
def _decode_buttons(raw: Any) -> list[dict]:
    """JSONB buttons → list[dict]. asyncpg без jsonb-кодека отдаёт str (нужен json.loads);
    list — если кодек зарегистрирован; None/пусто/битое → []. См. promo_contracts._coerce_discounts_by_plan."""
    if raw is None:
        return []
    if isinstance(raw, str):
        if not raw.strip():
            return []
        try:
            raw = json.loads(raw)
        except (ValueError, TypeError):
            return []
    if not isinstance(raw, list):
        return []
    return [b for b in raw if isinstance(b, dict)]


def _buttons_view(raw: Any) -> list[dict]:
    """Кнопки задачи для ответа API: [{text, callback_data? url?}, ...]."""
    return [dict(b) for b in _decode_buttons(raw) if isinstance(b.get("text"), str)]


def _job_to_dict(row: asyncpg.Record) -> dict:
    return {
        "job_id": str(row["job_id"]),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "created_by": row["created_by"],
        "status": row["status"],
        "categories": list(row["categories"] or []),
        "explicit_telegram_ids_count": len(row["explicit_telegram_ids"] or []),
        "message_text": row["message_text"],
        "parse_mode": row["parse_mode"],
        "buttons": _buttons_view(row.get("buttons")),
        "bonus": _decode_bonus(row.get("bonus")),
        "total_recipients": row["total_recipients"],
        "sent_count": row["sent_count"],
        "failed_count": row["failed_count"],
        "blocked_count": row["blocked_count"],
        "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
        "finished_at": row["finished_at"].isoformat() if row.get("finished_at") else None,
        "last_error": row["last_error"],
    }


# --- Handlers ---------------------------------------------------------------


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_broadcast_estimate(request: Request) -> JSONResponse:
    """Dry-run: размер аудитории (дедуп) + per-category counts + разрешённые/неразрешённые явные цели."""
    pool: asyncpg.Pool = request.app.state.pool
    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict):
        return safe_json_error(400, "invalid_request", "Expected JSON object")

    categories = _validate_categories(data.get("categories"))
    if categories is None:
        return safe_json_error(400, "invalid_categories", f"allowed: {sorted(_ALLOWED_CATEGORIES)}")

    raw_targets = data.get("explicit_targets") or []
    if not isinstance(raw_targets, list):
        return safe_json_error(400, "invalid_explicit_targets")
    resolved_ids, unresolved = await _resolve_explicit_targets(pool, [str(t) for t in raw_targets])

    try:
        total = int(await pool.fetchval(_count_sql(categories), resolved_ids) or 0)
        per_category: dict[str, int] = {}
        for key in categories:
            per_category[key] = await _count_category(pool, key)
    except Exception:
        _LOGGER.exception("broadcast estimate sql failed")
        return safe_json_error(500, "internal_error")

    return JSONResponse(
        {
            "ok": True,
            "total": total,
            "per_category_counts": per_category,
            "explicit_resolved": resolved_ids,
            "explicit_unresolved": unresolved,
        }
    )


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_broadcast_send(request: Request) -> JSONResponse:
    """Создать задачу рассылки (status='queued') и запустить фоновую BroadcastRunner."""
    pool: asyncpg.Pool = request.app.state.pool
    await _maybe_reconcile_orphan_jobs(pool)

    client = getattr(request.app.state, "telegram_raw_client", None)
    if client is None:
        return safe_json_error(503, "telegram_not_configured", "BOT_TOKEN is not set")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict):
        return safe_json_error(400, "invalid_request", "Expected JSON object")

    categories = _validate_categories(data.get("categories"))
    if categories is None:
        return safe_json_error(400, "invalid_categories", f"allowed: {sorted(_ALLOWED_CATEGORIES)}")

    text = str(data.get("message_text") or "")
    if not text.strip():
        return safe_json_error(400, "empty_message")
    if len(text) > _MAX_MESSAGE_LEN:
        return safe_json_error(400, "message_too_long", f"max {_MAX_MESSAGE_LEN} chars")
    if (html_err := _validate_html(text)) is not None:
        return safe_json_error(400, "invalid_html", html_err)
    text = _html_for_telegram(text)  # <br> → \n (Telegram HTML не поддерживает <br>)

    # Фронт присылает кнопки вложенно: {buttons:{action_buttons,url_buttons}}.
    buttons, buttons_err = _validate_buttons(data.get("buttons") or {})
    if buttons_err is not None:
        return buttons_err

    bonus, bonus_err = _validate_bonus_config(data.get("bonus"))
    if bonus_err is not None:
        return bonus_err

    # Для promo: проверить существование/активность/не-истечение/не-исчерпание кода (best-effort).
    bonus_promo_err, bonus_warnings = await _validate_bonus_promo_exists(pool, bonus)
    if bonus_promo_err is not None:
        return bonus_promo_err

    disable_preview = bool(data.get("disable_web_page_preview", False))

    raw_targets = data.get("explicit_targets") or []
    if not isinstance(raw_targets, list):
        return safe_json_error(400, "invalid_explicit_targets")
    resolved_ids, unresolved = await _resolve_explicit_targets(pool, [str(t) for t in raw_targets])

    try:
        total = int(await pool.fetchval(_count_sql(categories), resolved_ids) or 0)
    except Exception:
        _LOGGER.exception("broadcast send estimate sql failed")
        return safe_json_error(500, "internal_error")

    if total == 0:
        return safe_json_error(400, "no_recipients", "Audience is empty")
    if total > _max_recipients():
        return safe_json_error(413, "too_many_recipients", f"limit {_max_recipients()} (set BROADCAST_MAX_RECIPIENTS)")

    # Одна активная рассылка за раз — предсказуемая flood-поверхность.
    active = await pool.fetchval("SELECT job_id FROM broadcast_jobs WHERE status IN ('running','queued') LIMIT 1")
    if active is not None:
        return safe_json_error(409, "broadcast_in_progress", str(active))

    job_id = uuid.uuid4()
    try:
        await pool.execute(
            "INSERT INTO broadcast_jobs "
            "(job_id, created_by, status, categories, explicit_telegram_ids, message_text, "
            " parse_mode, disable_web_page_preview, total_recipients, buttons, bonus) "
            "VALUES ($1,$2,'queued',$3,$4,$5,'HTML',$6,$7,$8::jsonb,$9::jsonb)",
            job_id,
            _admin_actor(request),
            categories,
            resolved_ids,
            text,
            disable_preview,
            total,
            json.dumps(buttons) if buttons else "[]",
            json.dumps(bonus) if bonus else None,
        )
    except Exception:
        _LOGGER.exception("broadcast insert failed")
        return safe_json_error(500, "internal_error")

    runner = BroadcastRunner(pool=pool, client=client, job_id=str(job_id))
    task = asyncio.create_task(runner.run())
    tasks: dict = getattr(request.app.state, "broadcast_tasks", {})
    tasks[str(job_id)] = task

    def _drop(_t: asyncio.Task) -> None:
        tasks.pop(str(job_id), None)

    task.add_done_callback(_drop)

    return JSONResponse(
        {
            "ok": True,
            "job_id": str(job_id),
            "total_recipients": total,
            "explicit_unresolved": unresolved,
            "bonus_warnings": bonus_warnings,
        },
        status_code=201,
    )


@_with_admin_auth
async def handle_admin_broadcast_status(request: Request) -> JSONResponse:
    """Прогресс задачи рассылки."""
    pool: asyncpg.Pool = request.app.state.pool
    await _maybe_reconcile_orphan_jobs(pool)
    job_id = request.path_params["job_id"]
    row = await pool.fetchrow(
        "SELECT job_id, created_at, created_by, status, categories, explicit_telegram_ids, "
        "message_text, parse_mode, total_recipients, sent_count, failed_count, blocked_count, "
        "started_at, finished_at, last_error, buttons, bonus "
        "FROM broadcast_jobs WHERE job_id=$1",
        job_id,
    )
    if row is None:
        return safe_json_error(404, "job_not_found")
    return JSONResponse({"ok": True, "job": _job_to_dict(row)})


@_with_admin_auth
async def handle_admin_broadcast_list(request: Request) -> JSONResponse:
    """Последние задачи рассылки (новые сверху)."""
    pool: asyncpg.Pool = request.app.state.pool
    try:
        limit = max(1, min(_LIST_MAX_LIMIT, int(request.query_params.get("limit", _LIST_DEFAULT_LIMIT))))
    except ValueError:
        limit = _LIST_DEFAULT_LIMIT
    try:
        offset = max(0, int(request.query_params.get("offset", "0")))
    except ValueError:
        offset = 0

    rows = await pool.fetch(
        "SELECT job_id, created_at, created_by, status, categories, explicit_telegram_ids, "
        "message_text, parse_mode, total_recipients, sent_count, failed_count, blocked_count, "
        "started_at, finished_at, last_error, buttons, bonus "
        "FROM broadcast_jobs ORDER BY created_at DESC LIMIT $1 OFFSET $2",
        limit,
        offset,
    )
    return JSONResponse({"ok": True, "jobs": [_job_to_dict(r) for r in rows], "limit": limit, "offset": offset})


@_with_admin_auth
@_with_admin_csrf
async def handle_admin_broadcast_test(request: Request) -> JSONResponse:
    """Пред-флайт: отправить сообщение одному chat_id (себе), чтобы увидеть реальный рендер HTML."""
    client = getattr(request.app.state, "telegram_raw_client", None)
    if client is None:
        return safe_json_error(503, "telegram_not_configured", "BOT_TOKEN is not set")

    try:
        data = await request.json()
    except Exception:
        return safe_json_error(400, "invalid_request", "Expected JSON body")
    if not isinstance(data, dict):
        return safe_json_error(400, "invalid_request", "Expected JSON object")

    raw_chat = data.get("chat_id")
    try:
        chat_id = int(raw_chat)
    except (TypeError, ValueError):
        return safe_json_error(400, "invalid_chat_id")
    if chat_id <= 0:
        return safe_json_error(400, "invalid_chat_id")

    text = str(data.get("message_text") or "")
    if not text.strip():
        return safe_json_error(400, "empty_message")
    if len(text) > _MAX_MESSAGE_LEN:
        return safe_json_error(400, "message_too_long", f"max {_MAX_MESSAGE_LEN} chars")
    if (html_err := _validate_html(text)) is not None:
        return safe_json_error(400, "invalid_html", html_err)
    text = _html_for_telegram(text)  # <br> → \n (Telegram HTML не поддерживает <br>)

    # Фронт присылает кнопки вложенно: {buttons:{action_buttons,url_buttons}}.
    buttons, buttons_err = _validate_buttons(data.get("buttons") or {})
    if buttons_err is not None:
        return buttons_err
    bonus, bonus_err = _validate_bonus_config(data.get("bonus"))
    if bonus_err is not None:
        return bonus_err
    # Для promo: проверить существование/активность кода (best-effort) — как в send.
    pool: asyncpg.Pool = request.app.state.pool
    bonus_promo_err, bonus_warnings = await _validate_bonus_promo_exists(pool, bonus)
    if bonus_promo_err is not None:
        return bonus_promo_err
    if bonus is not None:
        # Предпросмотр бонус-кнопки. job_id="preview" — нажатие в тесте не найдёт claim и
        # безопасно завершится отказом (это только визуальный предпросмотр для оператора).
        cb_prefix = CB_BROADCAST_PROMO if bonus["type"] == "promo" else CB_BROADCAST_DAYS
        buttons.append({"text": bonus["label"], "callback_data": _cb(cb_prefix, "preview")})
    reply_markup = _build_reply_markup(buttons)

    disable_preview = bool(data.get("disable_web_page_preview", False))
    try:
        await client.send_text_message(
            chat_id=chat_id,
            text=text,
            correlation_id=f"broadcast-test-{chat_id}",
            parse_mode="HTML",
            disable_web_page_preview=disable_preview,
            reply_markup=reply_markup,
        )
    except httpx.HTTPStatusError as exc:
        code = exc.response.status_code
        if code == 403:
            return safe_json_error(403, "blocked_by_user", "chat not reachable")
        detail = _describe_response(exc.response)[:200]
        return safe_json_error(400 if code < 500 else 502, "telegram_rejected", detail)
    except Exception:
        _LOGGER.exception("broadcast test send failed chat_id=%s", str(chat_id)[:8])
        return safe_json_error(502, "send_failed")

    return JSONResponse({"ok": True, "bonus_warnings": bonus_warnings})
