"""Атрибуция и дифф per-user трафик-счётчиков на bare_xray хостах (чистые функции).

QueryStats отдаёт счётчики per-PROCESS, без тега инбаунда: на хосте с двумя инбаундами
(tcp :443 + xhttp :8443) опрос приходит как ``{email: {"up": n, "down": n}}`` один раз.
Здесь решаются две задачи:

1. **Атрибуция** — какому строке vpn_servers принадлежит email: префикс ``x-``
   указывает на xhttp-строку, его отсутствие — на tcp (см. ``_email_from_internal``).
2. **Дифф** — счётчики живут в памяти процесса xray и после рестарта обнуляются,
   поэтому рост относительно сохранённого состояния — либо дельта (>= 0, накапливаем),
   либо clamp-to-new (рестарт случился: текущее значение и есть трафик с момента
   рестарта — отрицательную разницу не копим и не теряем).

Email-формат: ``user-<internal_id[:16]>`` (tcp) / ``x-user-<internal_id[:16]>`` (xhttp).
Резолв только вперёд (id → email): обратный парсинг ненадёжен из-за усечения до 16 символов.
"""

from __future__ import annotations

import calendar
import os
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from app.issuance.xui_client import XuiServerConfig

_MSK_OFFSET = timedelta(hours=3)  # Москва: UTC+3 без перехода на летнее время


def traffic_stats_enabled_from_env(env_getter=os.environ.get) -> bool:
    """Kill-switch коллектора трафика: включён ТОЛЬКО явным truthy значением (default OFF).

    Живёт здесь (а не в шедулере), чтобы persistence-wiring мог гейтить появление
    репозитория в композиции без зависимости от runtime-слоя.
    """
    raw = (env_getter("TRAFFIC_STATS_ENABLE") or "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class TrafficSample:
    """Одно показание счётчика пользователя на server_id, снятое тиком коллектора.

    Несёт ОДНОВРЕМЕННО абсолют (последнее показание per-PROCESS счётчика — его
    пишем в state для следующего диффа) и дельту (прирост с прошлого тика — её
    аккумулируем в daily). Разделять их нельзя: state с дельтой сломал бы
    следующий дифф (текущий абсолют «меньше» сохранённой дельты → вечный
    clamp-to-new и двойной подсчёт).
    """

    server_id: int
    email: str
    uplink: int
    downlink: int
    delta_up: int
    delta_down: int


def user_emails_for_internal(internal_user_id: str) -> tuple[str, str]:
    """Оба возможных email пользователя (tcp и xhttp) — прямой резолв id → email."""
    stub = internal_user_id[:16]
    return f"user-{stub}", f"x-user-{stub}"


def resolve_server_id(configs: Sequence[XuiServerConfig], email: str) -> int | None:
    """server_id строки vpn_servers, которой принадлежит email (по префиксу транспорта).

    ``x-`` префикс → xhttp-инбаунд хоста, иначе — tcp. None, если подходящей строки
    нет (email не из этого хоста / конфиг сменился).
    """
    wanted_transport = "xhttp" if email.startswith("x-user-") else "tcp"
    if "-g" in email and "-t" in email:
        wanted_transport = email.rsplit("-t", 1)[1]
    for cfg in configs:
        if cfg.transport_type == wanted_transport:
            return cfg.server_id
    return None


def diff_deltas(
    current: Mapping[str, Mapping[str, int]],
    state: Mapping[str, Mapping[str, int]],
) -> dict[str, dict[str, int]]:
    """Дельты роста счётчиков относительно сохранённого состояния.

    - email есть в current, нет в state → дельта = полное текущее значение
      (первый тик: новый пользователь или рестарт xray, счётчики родились заново);
    - current < state по какой-то из сторон → рестарт: берём текущее значение
      (clamp-to-new), а не отрицательную разницу;
    - email есть в state, но исчез из current → skip (пользователь удалён,
      провижининг убрал его — счётчик перестал существовать).
    """
    deltas: dict[str, dict[str, int]] = {}
    for email, cur in current.items():
        cur_up = int(cur.get("up", 0))
        cur_down = int(cur.get("down", 0))
        prev = state.get(email)
        if prev is None:
            deltas[email] = {"up": cur_up, "down": cur_down}
            continue
        prev_up = int(prev.get("up", 0))
        prev_down = int(prev.get("down", 0))
        deltas[email] = {
            "up": cur_up - prev_up if cur_up >= prev_up else cur_up,
            "down": cur_down - prev_down if cur_down >= prev_down else cur_down,
        }
    return deltas


def msk_now(now: datetime | None = None) -> datetime:
    """Текущее московское время (продукт RU-facing, границы суток/месяца — MSK)."""
    return (now or datetime.now(UTC)) + _MSK_OFFSET


def msk_stat_date(now: datetime | None = None) -> datetime.date:
    """Календарная дата в Москве на момент сбора — ключ stat_date дневной таблицы."""
    return msk_now(now).date()


def msk_month_bounds(now: datetime | None = None) -> tuple[datetime.date, datetime.date]:
    """Первая и последняя дата текущего московского месяца (для месячного тотала)."""
    moment = msk_now(now)
    first = moment.date().replace(day=1)
    last = first.replace(day=calendar.monthrange(first.year, first.month)[1])
    return first, last


def build_batch(
    host_results: Iterable[
        tuple[Sequence[XuiServerConfig], Mapping[str, Mapping[str, int]], Mapping[str, Mapping[str, int]]]
    ],
) -> list[TrafficSample]:
    """Собрать пачку TrafficSample из результатов хостов: (конфиги, current, state).

    Дифф считает diff_deltas (дельты), абсолют берётся из current как есть;
    email атрибутируется к server_id по префиксу транспорта. Неатрибутированные
    email пропускаются молча: их строки vpn_servers недостижимы текущим конфигом
    (смена тегов/транспортов), показание не привязать к серверу.
    """
    batch: list[TrafficSample] = []
    for configs, current, state in host_results:
        deltas = diff_deltas(current, state)
        for email, delta in deltas.items():
            server_id = resolve_server_id(configs, email)
            if server_id is None:
                continue
            cur = current[email]
            batch.append(
                TrafficSample(
                    server_id=server_id,
                    email=email,
                    uplink=int(cur.get("up", 0)),
                    downlink=int(cur.get("down", 0)),
                    delta_up=int(delta.get("up", 0)),
                    delta_down=int(delta.get("down", 0)),
                )
            )
    return batch


__all__ = [
    "TrafficSample",
    "build_batch",
    "diff_deltas",
    "msk_month_bounds",
    "msk_now",
    "msk_stat_date",
    "resolve_server_id",
    "traffic_stats_enabled_from_env",
    "user_emails_for_internal",
]
