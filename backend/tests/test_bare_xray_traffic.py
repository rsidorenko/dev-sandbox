"""Тесты учёта трафика bare_xray: атрибуция email→server_id, дифф счётчиков
(clamp-to-new после рестарта xray), группировка хостов (анти-двойной-подсчёт),
персист-батч (дельты в daily, абсолюты в state) и тик шедулера на фейках.

Сеть/SSH/grpcurl не нужны: чистые функции + фейковые пул/репозиторий/провайдер.
"""

from __future__ import annotations

import contextlib
import json
import types
from datetime import UTC, date, datetime
from unittest.mock import AsyncMock

from app.issuance import bare_xray_traffic as bxt
from app.issuance.bare_xray_traffic import (
    build_batch,
    diff_deltas,
    msk_month_bounds,
    msk_stat_date,
    resolve_server_id,
    user_emails_for_internal,
)
from app.issuance.xui_client import XuiServerConfig
from app.issuance.xui_vless_provider import XuiVlessProvider
from app.runtime import traffic_stats_scheduler as tss
from app.shared.test_helpers import run_async as _run


def _cfg(server_id: int, *, transport: str, host: str = "10.0.0.1") -> XuiServerConfig:
    return XuiServerConfig(
        server_id=server_id,
        label=f"srv-{server_id}",
        country_code="DE",
        country_flag="🇩🇪",
        server_host=host,
        server_port=443,
        ws_path="/ws",
        tls_sni=None,
        panel_url="",
        panel_username="",
        panel_password="",
        inbound_id=1,
        transport_type=transport,
        xray_inbound_tag=f"tag-{server_id}",
    )


# ---------------------------------------------------------------------------
# Атрибуция email → server_id (префикс x- = xhttp-строка)
# ---------------------------------------------------------------------------


def test_resolve_server_id_by_email_prefix() -> None:
    configs = [_cfg(2, transport="tcp"), _cfg(3, transport="xhttp")]
    assert resolve_server_id(configs, "user-abc123") == 2
    assert resolve_server_id(configs, "x-user-abc123") == 3


def test_resolve_server_id_no_match_returns_none() -> None:
    configs = [_cfg(2, transport="xhttp")]
    assert resolve_server_id(configs, "user-abc123") is None


def test_user_emails_for_internal_forward_resolution() -> None:
    # Прямой резолв: id → оба email; усечение до 16 символов.
    long_id = "a" * 32
    tcp, xhttp = user_emails_for_internal(long_id)
    assert tcp == f"user-{'a' * 16}"
    assert xhttp == f"x-user-{'a' * 16}"


# ---------------------------------------------------------------------------
# diff_deltas: первый тик, рост, рестарт (clamp-to-new), исчезнувший email
# ---------------------------------------------------------------------------


def test_diff_deltas_first_tick_full_value() -> None:
    current = {"user-1": {"up": 100, "down": 500}}
    assert diff_deltas(current, {}) == {"user-1": {"up": 100, "down": 500}}


def test_diff_deltas_growth() -> None:
    current = {"user-1": {"up": 150, "down": 700}}
    state = {"user-1": {"up": 100, "down": 500}}
    assert diff_deltas(current, state) == {"user-1": {"up": 50, "down": 200}}


def test_diff_deltas_restart_clamps_to_new() -> None:
    # Рестарт xray: счётчик родился заново и МЕНЬШЕ сохранённого. Дельта = текущее
    # значение (трафик с момента рестарта), а не отрицательная разница.
    current = {"user-1": {"up": 5, "down": 0}}
    state = {"user-1": {"up": 100, "down": 500}}
    assert diff_deltas(current, state) == {"user-1": {"up": 5, "down": 0}}


def test_diff_deltas_one_side_restarted() -> None:
    # Стороны независимы: up рестартнул (clamp), down продолжает расти (дельта).
    current = {"user-1": {"up": 3, "down": 900}}
    state = {"user-1": {"up": 100, "down": 500}}
    assert diff_deltas(current, state) == {"user-1": {"up": 3, "down": 400}}


def test_diff_deltas_missing_in_current_skipped() -> None:
    # Пользователь удалён → счётчик исчез → не отдаём нулевую/отрицательную запись.
    current = {"user-2": {"up": 10, "down": 10}}
    state = {"user-1": {"up": 100, "down": 500}, "user-2": {"up": 5, "down": 5}}
    assert diff_deltas(current, state) == {"user-2": {"up": 5, "down": 5}}


# ---------------------------------------------------------------------------
# build_batch: сэмпл несёт абсолют И дельту одновременно
# ---------------------------------------------------------------------------


def test_build_batch_carries_absolute_and_delta() -> None:
    configs = [_cfg(2, transport="tcp"), _cfg(3, transport="xhttp")]
    current = {
        "user-1": {"up": 150, "down": 700},
        "x-user-1": {"up": 30, "down": 40},
    }
    state = {
        "user-1": {"up": 100, "down": 500},
        "x-user-1": {"up": 10, "down": 20},
    }
    batch = build_batch([(configs, current, state)])
    by_server = {row.server_id: row for row in batch}
    assert by_server[2].delta_up == 50 and by_server[2].delta_down == 200
    assert by_server[2].uplink == 150 and by_server[2].downlink == 700
    assert by_server[3].delta_up == 20 and by_server[3].delta_down == 20


def test_build_batch_unattributed_email_skipped() -> None:
    configs = [_cfg(3, transport="xhttp")]  # tcp-строки нет
    batch = build_batch([(configs, {"user-1": {"up": 1, "down": 1}}, {})])
    assert batch == []


# ---------------------------------------------------------------------------
# MSK-границы (продукт RU-facing)
# ---------------------------------------------------------------------------


def test_msk_date_and_month_bounds() -> None:
    # 2026-08-31 22:30 UTC = 2026-09-01 01:30 MSK — сутки/месяц уже сентябрьские.
    now = datetime(2026, 8, 31, 22, 30, tzinfo=UTC)
    assert msk_stat_date(now).isoformat() == "2026-09-01"
    first, last = msk_month_bounds(now)
    assert (first.isoformat(), last.isoformat()) == ("2026-09-01", "2026-09-30")


def test_msk_month_bounds_leap_february() -> None:
    now = datetime(2028, 2, 15, 12, 0, tzinfo=UTC)
    _, last = msk_month_bounds(now)
    assert last.isoformat() == "2028-02-29"


# ---------------------------------------------------------------------------
# bare_xray_stats_hosts: dual-inbound хост → ОДНА группа (анти-двойной-подсчёт)
# ---------------------------------------------------------------------------


class _StatsFakeClient:
    def __init__(self, server_id: int, host: str, transport: str, api_port: int = 10085):
        self.server_id = server_id
        self.api_port = api_port
        self.server_config = types.SimpleNamespace(server_id=server_id, server_host=host, transport_type=transport)


def _provider_with_clients(clients):
    provider = XuiVlessProvider(pool=object())
    provider._key_state = AsyncMock(return_value={})

    async def fake_clients():
        return tuple(clients)

    provider._get_bare_xray_clients = fake_clients  # type: ignore[method-assign]
    return provider


def test_stats_hosts_dedupes_dual_inbound_host() -> None:
    de_tcp = _StatsFakeClient(2, "158.160.188.35", "tcp")
    de_xhttp = _StatsFakeClient(3, "158.160.188.35", "xhttp")
    fi_tcp = _StatsFakeClient(4, "85.208.85.117", "tcp")
    provider = _provider_with_clients([de_tcp, de_xhttp, fi_tcp])

    hosts = _run(provider.bare_xray_stats_hosts())

    assert len(hosts) == 2  # не 3: DE-хост один, несмотря на два инбаунда
    by_host = {client.server_config.server_host: (client, configs) for client, configs in hosts}
    de_client, de_configs = by_host["158.160.188.35"]
    assert de_client in (de_tcp, de_xhttp)
    assert {c.server_id for c in de_configs} == {2, 3}
    assert {c.server_id for c in by_host["85.208.85.117"][1]} == {4}


def test_stats_hosts_separates_different_api_ports() -> None:
    a = _StatsFakeClient(2, "10.0.0.1", "tcp", api_port=10085)
    b = _StatsFakeClient(3, "10.0.0.1", "tcp", api_port=10086)
    hosts = _run(_provider_with_clients([a, b]).bare_xray_stats_hosts())
    assert len(hosts) == 2


# ---------------------------------------------------------------------------
# PostgresTrafficStatsRepository: батч и чтения на фейковом пуле
# ---------------------------------------------------------------------------


class _FakeConn:
    def __init__(self, *, fetchval_result=None, fetchrow_result=None, fetch_rows=None) -> None:
        self.calls: list[tuple[str, list]] = []
        self._fetchval_result = fetchval_result
        self._fetchrow_result = fetchrow_result
        self._fetch_rows = fetch_rows or []

    async def fetchval(self, sql, *args):
        self.calls.append((sql, list(args)))
        return self._fetchval_result

    async def fetchrow(self, sql, *args):
        self.calls.append((sql, list(args)))
        return self._fetchrow_result

    async def fetch(self, sql, *args):
        self.calls.append((sql, list(args)))
        return self._fetch_rows

    async def execute(self, sql, *args):
        self.calls.append((sql, list(args)))
        return "OK"

    async def executemany(self, sql, args_list):
        self.calls.append((sql, [list(a) for a in args_list]))

    def transaction(self):
        @contextlib.asynccontextmanager
        async def _tx():
            yield

        return _tx()


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn
        self.acquired = 0

    def acquire(self):
        @contextlib.asynccontextmanager
        async def _acq():
            self.acquired += 1
            yield self._conn

        return _acq()


def _sample(server_id=2, email="user-1", up=150, down=700, dup=50, ddown=200):
    return bxt.TrafficSample(server_id=server_id, email=email, uplink=up, downlink=down, delta_up=dup, delta_down=ddown)


def test_apply_batch_daily_gets_deltas_state_gets_absolutes(monkeypatch) -> None:
    from app.persistence import postgres_traffic_stats as pts

    monkeypatch.setattr(pts, "msk_stat_date", lambda now=None: date(2026, 9, 7))
    conn = _FakeConn()
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))

    started = _run(
        repo.apply_batch([_sample(), _sample(server_id=3, email="x-user-1", up=30, down=40, dup=20, ddown=20)])
    )

    assert started == 0
    assert len(conn.calls) == 3
    daily_sql, daily_rows = conn.calls[0]
    state_sql, state_rows = conn.calls[1]
    trial_sql, trial_args = conn.calls[2]
    assert "INSERT INTO bare_xray_traffic_daily" in daily_sql
    assert "+ EXCLUDED.uplink_bytes" in daily_sql
    assert [r[3] for r in daily_rows] == [50, 20]  # дельты up
    assert [r[4] for r in daily_rows] == [200, 20]  # дельты down
    assert "INSERT INTO bare_xray_traffic_state" in state_sql
    assert [r[2] for r in state_rows] == [150, 30]  # абсолюты up
    assert [r[3] for r in state_rows] == [700, 40]  # абсолюты down
    assert "trial_first_traffic_at IS NULL" in trial_sql
    assert "s.plan_id IS NULL" in trial_sql
    assert "s.trial_expires_at > NOW()" in trial_sql
    assert trial_args == [["user-1", "x-user-1"], 3]


def test_apply_batch_reports_started_trials_and_deduplicates_positive_emails() -> None:
    from app.persistence import postgres_traffic_stats as pts

    conn = _FakeConn(fetch_rows=[{"internal_user_id": "u1"}])
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))

    started = _run(
        repo.apply_batch(
            [
                _sample(server_id=2, email="user-1", dup=1, ddown=0),
                _sample(server_id=3, email="user-1", dup=0, ddown=2),
            ]
        )
    )

    assert started == 1
    _, trial_args = conn.calls[2]
    assert trial_args[0] == ["user-1"]


def test_apply_batch_zero_delta_does_not_start_trial() -> None:
    from app.persistence import postgres_traffic_stats as pts

    conn = _FakeConn()
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))

    started = _run(repo.apply_batch([_sample(dup=0, ddown=0)]))

    assert started == 0
    assert len(conn.calls) == 2


def test_apply_batch_empty_is_noop() -> None:
    from app.persistence import postgres_traffic_stats as pts

    pool = _FakePool(_FakeConn())
    assert _run(pts.PostgresTrafficStatsRepository(pool).apply_batch([])) == 0
    assert pool.acquired == 0


def test_monthly_total_uses_forward_emails_and_msk_bounds(monkeypatch) -> None:
    from app.persistence import postgres_traffic_stats as pts

    monkeypatch.setattr(pts, "msk_month_bounds", lambda now=None: (date(2026, 9, 1), date(2026, 9, 30)))
    conn = _FakeConn(fetchval_result=12345)
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))

    total = _run(repo.monthly_total_for_user("abcdefgh12345678xyz"))

    assert total == 12345
    sql, args = conn.calls[0]
    assert "SUM(uplink_bytes + downlink_bytes)" in sql
    assert "email = ANY($1)" in sql
    assert args[0] == ["user-abcdefgh12345678", "x-user-abcdefgh12345678"]  # усечение до 16
    assert args[1].isoformat() == "2026-09-01" and args[2].isoformat() == "2026-09-30"


def test_monthly_total_none_coerced_to_zero() -> None:
    from app.persistence import postgres_traffic_stats as pts

    conn = _FakeConn(fetchval_result=None)
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))
    assert _run(repo.monthly_total_for_user("u1")) == 0


def test_monthly_usage_returns_directions_and_uses_msk_bounds(monkeypatch) -> None:
    from app.persistence import postgres_traffic_stats as pts

    monkeypatch.setattr(pts, "msk_month_bounds", lambda now=None: (date(2026, 9, 1), date(2026, 9, 30)))
    conn = _FakeConn(fetchrow_result={"upload_bytes": 123, "download_bytes": 4567})
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))

    assert _run(repo.monthly_usage_for_user("abcdefgh12345678xyz")) == (123, 4567)

    sql, args = conn.calls[0]
    assert "SUM(uplink_bytes)" in sql and "SUM(downlink_bytes)" in sql
    assert args[0] == ["user-abcdefgh12345678", "x-user-abcdefgh12345678"]
    assert args[1].isoformat() == "2026-09-01" and args[2].isoformat() == "2026-09-30"


def test_monthly_usage_without_rows_is_zero() -> None:
    from app.persistence import postgres_traffic_stats as pts

    conn = _FakeConn(fetchrow_result=None)
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))
    assert _run(repo.monthly_usage_for_user("u1")) == (0, 0)


def test_per_server_maps_rows_verbatim() -> None:
    from app.persistence import postgres_traffic_stats as pts

    # Фейк не исполняет ORDER BY — порядок обеспечивает SQL; подаём уже DESC-порядок.
    rows = [
        {"server_id": 3, "up_bytes": 1000, "down_bytes": 0},
        {"server_id": 2, "up_bytes": 100, "down_bytes": 200},
    ]
    conn = _FakeConn(fetch_rows=rows)
    repo = pts.PostgresTrafficStatsRepository(_FakePool(conn))

    result = _run(repo.per_server_for_user("u1"))

    sql, args = conn.calls[0]
    assert "GROUP BY server_id" in sql
    assert "ORDER BY (SUM(uplink_bytes) + SUM(downlink_bytes)) DESC" in sql
    assert result == [
        {"server_id": 3, "uplink_bytes": 1000, "downlink_bytes": 0},
        {"server_id": 2, "uplink_bytes": 100, "downlink_bytes": 200},
    ]


# ---------------------------------------------------------------------------
# TrafficStatsScheduler: тик на фейках (сбой хоста изолирован)
# ---------------------------------------------------------------------------


class _FakeStatsHostClient:
    def __init__(self, server_id: int, host: str, transport: str, stats) -> None:
        self.server_id = server_id
        self.api_port = 10085
        self.server_config = types.SimpleNamespace(server_host=host, transport_type=transport)
        self._stats = stats

    async def query_stats(self):
        if isinstance(self._stats, Exception):
            raise self._stats
        return self._stats


class _FakeTickPool:
    """Пул только для advisory-lock коннекта тика."""

    def __init__(self, *, locked: bool = True) -> None:
        self.locked = locked
        self.unlocked: list[int] = []
        self.observations: list = []

    async def executemany(self, sql, values):
        self.observations.extend(values)

    def acquire(self):
        @contextlib.asynccontextmanager
        async def _acq():
            yield types.SimpleNamespace(
                fetchval=_immediate(self.locked),
                execute=_record_unlock(self.unlocked),
            )

        return _acq()


def _immediate(value):
    async def _coro(*args, **kwargs):
        return value

    return _coro


def _record_unlock(sink: list):
    async def _coro(sql, *args):
        sink.append(args[0] if args else None)

    return _coro


class _FakeRepo:
    def __init__(self, state_map=None) -> None:
        self.state_map = state_map or {}
        self.batches: list = []

    async def load_state(self, server_id):
        return self.state_map.get(server_id, {})

    async def apply_batch(self, batch):
        self.batches.append(list(batch))
        return 0


class _FakeProvider:
    def __init__(self, hosts) -> None:
        self._hosts = hosts

    async def bare_xray_stats_hosts(self):
        return self._hosts


def _scheduler(pool, provider, repo):
    sched = tss.TrafficStatsScheduler(pool=pool, vless_provider=provider)
    sched._repo = repo  # type: ignore[assignment]
    return sched


def test_tick_collects_and_applies() -> None:
    configs = [_cfg(2, transport="tcp", host="158.160.188.35")]
    hosts = [(_FakeStatsHostClient(2, "158.160.188.35", "tcp", {"user-1": {"up": 150, "down": 700}}), configs)]
    repo = _FakeRepo(state_map={2: {"user-1": {"up": 100, "down": 500}}})
    pool = _FakeTickPool()
    sched = _scheduler(pool, _FakeProvider(hosts), repo)

    _run(sched._run_tick())

    assert len(repo.batches) == 1
    ((row,),) = repo.batches
    assert row.server_id == 2
    assert (row.delta_up, row.delta_down) == (50, 200)
    assert (row.uplink, row.downlink) == (150, 700)
    assert pool.unlocked == [tss._ADVISED_LOCK_ID]


def test_tick_skips_host_on_transport_failure() -> None:
    configs = [_cfg(2, transport="tcp")]
    hosts = [
        (_FakeStatsHostClient(2, "h1", "tcp", None), configs),  # query_stats → None
        (_FakeStatsHostClient(3, "h2", "xhttp", {"x-user-1": {"up": 5, "down": 5}}), [_cfg(3, transport="xhttp")]),
    ]
    repo = _FakeRepo()
    sched = _scheduler(_FakeTickPool(), _FakeProvider(hosts), repo)

    _run(sched._run_tick())

    ((row,),) = repo.batches
    assert row.email == "x-user-1"  # живой хвост собран, сбойный хост пропущен


def test_tick_skips_host_on_query_exception() -> None:
    hosts = [(_FakeStatsHostClient(2, "h1", "tcp", RuntimeError("grpcurl died")), [_cfg(2, transport="tcp")])]
    repo = _FakeRepo()
    sched = _scheduler(_FakeTickPool(), _FakeProvider(hosts), repo)

    _run(sched._run_tick())

    assert repo.batches == [[]]  # тик не упал, пустой батч применён


def test_tick_state_load_failure_skips_host_not_resets() -> None:
    # Ошибка чтения state НЕ должна трактоваться как «state пуст» — иначе полный
    # текущий счётчик посчитался бы дельтой (двойной подсчёт). Хост пропускается.
    configs = [_cfg(2, transport="tcp")]
    hosts = [(_FakeStatsHostClient(2, "h1", "tcp", {"user-1": {"up": 150, "down": 700}}), configs)]

    class _ExplodingRepo(_FakeRepo):
        async def load_state(self, server_id):
            raise RuntimeError("db hiccup")

    repo = _ExplodingRepo()
    sched = _scheduler(_FakeTickPool(), _FakeProvider(hosts), repo)

    _run(sched._run_tick())

    assert repo.batches == [[]]


def test_tick_skipped_when_lock_not_held() -> None:
    repo = _FakeRepo()
    pool = _FakeTickPool(locked=False)
    sched = _scheduler(pool, _FakeProvider([]), repo)

    _run(sched._run_tick())

    assert repo.batches == []
    assert pool.unlocked == []  # лок не брали — не возвращаем


def test_traffic_stats_env_flag_default_off(monkeypatch) -> None:
    monkeypatch.delenv("TRAFFIC_STATS_ENABLE", raising=False)
    assert bxt.traffic_stats_enabled_from_env() is False
    assert tss.traffic_stats_enabled_from_env() is False
    monkeypatch.setenv("TRAFFIC_STATS_ENABLE", "1")
    assert bxt.traffic_stats_enabled_from_env() is True
    monkeypatch.setenv("TRAFFIC_STATS_ENABLE", "0")
    assert bxt.traffic_stats_enabled_from_env() is False


def test_parse_query_stats_via_client_module_json() -> None:
    # сквозная проверка: grpcurl-вывод → парсер клиента → дифф
    from app.issuance.xray_api_client import parse_query_stats_response

    raw = json.dumps(
        {
            "stat": [
                {"name": "user>>>user-1>>>traffic>>>uplink", "value": "150"},
                {"name": "user>>>user-1>>>traffic>>>downlink", "value": "700"},
            ]
        }
    )
    parsed = parse_query_stats_response(raw)
    assert diff_deltas(parsed, {"user-1": {"up": 100, "down": 500}}) == {"user-1": {"up": 50, "down": 200}}


def test_empty_stats_records_success_and_observation_failure_does_not_stop_collection():
    configs = [_cfg(2, transport="tcp", host="test-host")]
    hosts = [(_FakeStatsHostClient(2, "test-host", "tcp", {}), configs)]
    pool = _FakeTickPool()
    repo = _FakeRepo()
    _run(_scheduler(pool, _FakeProvider(hosts), repo)._run_tick())
    assert pool.observations == [(2, "success")]

    async def fail(*args):
        raise RuntimeError("Недоступна таблица мониторинга")

    pool.executemany = fail
    _run(_scheduler(pool, _FakeProvider(hosts), repo)._run_tick())
    assert len(repo.batches) == 2
    assert len(pool.unlocked) == 2


def test_stats_error_is_persisted_without_breaking_other_hosts():
    hosts = [
        (_FakeStatsHostClient(2, "test-host", "tcp", None), [_cfg(2, transport="tcp")]),
        (_FakeStatsHostClient(3, "other-host", "tcp", {}), [_cfg(3, transport="tcp")]),
    ]
    pool = _FakeTickPool()
    _run(_scheduler(pool, _FakeProvider(hosts), _FakeRepo())._run_tick())
    assert pool.observations == [(2, "error"), (3, "success")]
