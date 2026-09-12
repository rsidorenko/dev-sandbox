"""Unit и PostgreSQL-проверки first-touch атрибуции Telegram Ads."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import asyncpg
import pytest

from app.application.bootstrap import build_slice1_composition
from app.application.telegram_ads_attribution import (
    InMemoryTelegramAdsAttributionRecorder,
    TelegramAdsAttributionRecord,
    parse_telegram_ads_campaign,
    safely_record_telegram_ads_first_touch,
)
from app.bot_transport.dispatcher import dispatch_slice1_transport
from app.bot_transport.normalized import NormalizedSlice1Bootstrap, TransportIncomingEnvelope, parse_slice1_transport
from app.persistence.postgres_migrations import apply_postgres_migrations
from app.persistence.postgres_telegram_ads_attribution import PostgresTelegramAdsAttributionRecorder
from app.shared.correlation import new_correlation_id
from app.web_api.admin_stats import _telegram_ads_campaigns

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
_MIGRATIONS_DIR = _BACKEND_ROOT / "migrations"
_PREFIX = "test-tgads-"


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("tgads_apk_01", "apk_01"),
        ("ads_apk_01", "apk_01"),
        ("TGADS_Channel-Video_A", "channel-video_a"),
        ("ADS_Channel-Video_A", "channel-video_a"),
        ("tgads_a" + "1" * 47, "a" + "1" * 47),
        ("ads_a" + "1" * 47, "a" + "1" * 47),
        ("ref_abcd", None),
        ("tgads_", None),
        ("ads_", None),
        ("tgads__bad", None),
        ("ads_bad.dot", None),
        ("tgads_bad.dot", None),
        ("tgads_a" + "1" * 48, None),
        ("ads_a" + "1" * 48, None),
        (None, None),
    ],
)
def test_parse_campaign(value, expected):
    assert parse_telegram_ads_campaign(value) == expected


def envelope(text: str, *, uid: int = 991001, update_id: int = 1) -> TransportIncomingEnvelope:
    return TransportIncomingEnvelope(
        telegram_user_id=uid,
        telegram_update_id=update_id,
        correlation_id=new_correlation_id(),
        normalized_command_text=text,
    )


def test_normalized_start_keeps_referral_and_ads_separate():
    for prefix in ("tgads_", "ads_"):
        ads = parse_slice1_transport(envelope(f"/start {prefix}apk_01"))
        assert isinstance(ads, NormalizedSlice1Bootstrap)
        assert ads.telegram_ads_campaign == "apk_01"
        assert ads.referral_code is None

    referral = parse_slice1_transport(envelope("/start ref_abcd"))
    assert isinstance(referral, NormalizedSlice1Bootstrap)
    assert referral.referral_code == "abcd"
    assert referral.telegram_ads_campaign is None


async def test_dispatch_records_immutable_first_touch_and_retries():
    composition = build_slice1_composition()
    await dispatch_slice1_transport(envelope("/start ads_apk_01", update_id=1), composition)
    await dispatch_slice1_transport(envelope("/start tgads_tiktok_02", update_id=2), composition)
    recorder = composition.telegram_ads_attribution
    assert isinstance(recorder, InMemoryTelegramAdsAttributionRecorder)
    record = await recorder.get_for_user("u991001")
    assert record == TelegramAdsAttributionRecord("u991001", "apk_01", 1)


async def test_safe_write_failure_does_not_leak_and_cancellation_propagates(caplog):
    class Broken:
        async def record_first_touch(self, record):
            raise RuntimeError("campaign_SECRET")

    assert not await safely_record_telegram_ads_first_touch(
        Broken(), internal_user_id="u1", campaign_key="secret", telegram_update_id=1
    )
    assert "SECRET" not in caplog.text

    class Slow:
        async def record_first_touch(self, record):
            await asyncio.sleep(1)

    assert not await safely_record_telegram_ads_first_touch(
        Slow(), internal_user_id="u1", campaign_key="safe", telegram_update_id=1
    )

    class Cancelled:
        async def record_first_touch(self, record):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await safely_record_telegram_ads_first_touch(
            Cancelled(), internal_user_id="u1", campaign_key="safe", telegram_update_id=1
        )


@pytest.fixture
async def pool():
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("Нужна отдельная тестовая PostgreSQL DATABASE_URL")
    pool = await asyncpg.create_pool(url, min_size=1, max_size=4)
    await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
    await apply_postgres_migrations(pool, migrations_directory=_MIGRATIONS_DIR)
    try:
        yield pool
    finally:
        await pool.execute("DELETE FROM funnel_interactions WHERE internal_user_id LIKE $1", f"{_PREFIX}%")
        await pool.execute("DELETE FROM billing_events_ledger WHERE internal_user_id LIKE $1", f"{_PREFIX}%")
        await pool.execute("DELETE FROM subscription_snapshots WHERE internal_user_id LIKE $1", f"{_PREFIX}%")
        await pool.execute("DELETE FROM user_identities WHERE internal_user_id LIKE $1", f"{_PREFIX}%")
        await pool.close()


async def add_user(pool, suffix: str, index: int, attributed_at: datetime, campaign: str, *, internal=False):
    uid = f"{_PREFIX}{suffix}"
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id, telegram_user_id, created_at, is_internal) VALUES ($1,$2,$3,$4)",
        uid,
        998000000000 + index,
        attributed_at,
        internal,
    )
    await pool.execute(
        "INSERT INTO telegram_ads_attributions (internal_user_id,campaign_key,telegram_update_id,attributed_at) "
        "VALUES ($1,$2,$3,$4)",
        uid,
        campaign,
        index,
        attributed_at,
    )
    return uid


async def add_trial(pool, uid: str, when: datetime):
    await pool.execute(
        "INSERT INTO subscription_snapshots (internal_user_id,state_label,trial_started_at) VALUES ($1,'active',$2)",
        uid,
        when,
    )


async def add_payment(pool, uid: str, ref: str, when: datetime):
    await pool.execute(
        "INSERT INTO billing_events_ledger "
        "(internal_fact_ref,billing_provider_key,external_event_id,event_type,event_effective_at,event_received_at,"
        "internal_user_id,amount_minor_units,currency_code,status,ingestion_correlation_id) "
        "VALUES ($1,'yookassa_v1',$1,'subscription_activated',$2,$2,$3,16900,'RUB','accepted','test-tgads')",
        ref,
        when,
        uid,
    )


async def add_interaction(pool, uid: str, event: str, when: datetime):
    await pool.execute(
        "INSERT INTO funnel_interactions (internal_user_id,event_name,surface,dedup_key,occurred_at) "
        "VALUES ($1,$2,'telegram',$3,$4)",
        uid,
        event,
        f"{uid}:{event}",
        when,
    )


async def test_postgres_first_touch_and_campaign_funnel(pool):
    now = datetime.now(UTC)
    recorder = PostgresTelegramAdsAttributionRecorder(pool)
    uid = f"{_PREFIX}repo"
    await pool.execute(
        "INSERT INTO user_identities (internal_user_id,telegram_user_id) VALUES ($1,$2)", uid, 998000000099
    )
    assert await recorder.record_first_touch(TelegramAdsAttributionRecord(uid, "first", 10))
    assert not await recorder.record_first_touch(TelegramAdsAttributionRecord(uid, "second", 11))
    assert (
        await pool.fetchval("SELECT campaign_key FROM telegram_ads_attributions WHERE internal_user_id=$1", uid)
        == "first"
    )

    touch = now - timedelta(days=20)
    converted = await add_user(pool, "converted", 1, touch, "apk_01")
    trial = touch + timedelta(hours=1)
    await add_trial(pool, converted, trial)
    for event in ("setup_started", "connection_confirmed", "checkout_opened", "payment_created"):
        await add_interaction(pool, converted, event, trial + timedelta(hours=1))
    await add_payment(pool, converted, f"{_PREFIX}paid-1", trial + timedelta(days=1))
    await add_payment(pool, converted, f"{_PREFIX}paid-2", trial + timedelta(days=15))

    pending = await add_user(pool, "pending", 2, now - timedelta(days=2), "apk_01")
    await add_trial(pool, pending, now - timedelta(days=1))
    await add_interaction(pool, pending, "setup_started", now - timedelta(hours=12))

    no_pay = await add_user(pool, "no-pay", 3, touch, "tiktok_02")
    await add_trial(pool, no_pay, trial)

    pre_paid = await add_user(pool, "pre-paid", 4, touch, "tiktok_02")
    await add_payment(pool, pre_paid, f"{_PREFIX}pre", touch - timedelta(days=1))
    await add_trial(pool, pre_paid, trial)
    await add_payment(pool, pre_paid, f"{_PREFIX}renewal", trial + timedelta(days=1))

    returning = await add_user(pool, "returning", 6, touch, "tiktok_02")
    await add_interaction(pool, returning, "setup_started", touch + timedelta(hours=1))

    internal = await add_user(pool, "internal", 5, touch, "apk_01", internal=True)
    await add_trial(pool, internal, trial)
    await add_payment(pool, internal, f"{_PREFIX}internal", trial + timedelta(days=1))

    rows = await _telegram_ads_campaigns(pool, now - timedelta(days=30), now)
    rows.sort(key=lambda row: row["campaign_key"])
    assert rows == [
        {
            "campaign_key": "apk_01",
            "attributed_users": 2,
            "trials_started": 2,
            "setup_started_users": 2,
            "connection_confirmed_users": 1,
            "checkout_opened_users": 1,
            "payment_created_users": 1,
            "mature_trials": 1,
            "pending_trials": 1,
            "first_payment_users": 1,
            "second_payment_users": 1,
            "trial_activation_rate": 1.0,
            "trial_to_first_payment_rate": 1.0,
        },
        {
            "campaign_key": "tiktok_02",
            "attributed_users": 3,
            "trials_started": 2,
            "setup_started_users": 0,
            "connection_confirmed_users": 0,
            "checkout_opened_users": 0,
            "payment_created_users": 0,
            "mature_trials": 2,
            "pending_trials": 0,
            "first_payment_users": 0,
            "second_payment_users": 0,
            "trial_activation_rate": round(2 / 3, 4),
            "trial_to_first_payment_rate": 0.0,
        },
    ]
