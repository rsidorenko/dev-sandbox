"""Транзакции временных ссылок в изолированной схеме PostgreSQL."""
import asyncio
import os
from pathlib import Path
import uuid
import asyncpg
import pytest
from app.persistence.pending_web_links import create_link, begin_attempt, attach_payment, promote_paid_link, cleanup_expired_links, LinkExpiredError

def test_lifecycle_and_payment_retention():
    dsn = os.environ.get('PENDING_LINK_TEST_DSN')
    if not dsn:
        pytest.skip('Нужна отдельная тестовая БД: PENDING_LINK_TEST_DSN')
    async def scenario():
        schema = 'pending_test_' + uuid.uuid4().hex
        conn = await asyncpg.connect(dsn)
        await conn.execute(f'CREATE SCHEMA {schema}')
        pool = await asyncpg.create_pool(dsn, min_size=1, max_size=4, server_settings={'search_path':schema})
        try:
            await pool.execute('CREATE TABLE user_identities (telegram_user_id bigint PRIMARY KEY, internal_user_id text UNIQUE, subscription_token text UNIQUE)')
            await pool.execute((Path(__file__).parents[1]/'migrations/107_pending_web_links.sql').read_text())
            await create_link(pool)
            awaiting = await create_link(pool)
            attempt, _ = await begin_attempt(pool, awaiting['internal_user_id'], 'yookassa', 24900)
            await attach_payment(pool, attempt, 'payment-test')
            unknown = await create_link(pool)
            await begin_attempt(pool, unknown['internal_user_id'], 'yookassa', 24900)
            await pool.execute("UPDATE pending_web_links SET expires_at=now()-interval '1 second'")
            assert await cleanup_expired_links(pool) == 1
            assert await pool.fetchval('SELECT count(*) FROM pending_web_links') == 3
            assert await cleanup_expired_links(pool, delete=True) == 1
            assert await pool.fetchval('SELECT count(*) FROM pending_web_links') == 2
            with pytest.raises(LinkExpiredError):
                await begin_attempt(pool, awaiting['internal_user_id'], 'yookassa', 24900)
            with pytest.raises(ValueError):
                await promote_paid_link(pool, internal_user_id=awaiting['internal_user_id'], attempt_id=attempt, provider='yookassa', payment_id='payment-test', amount_kopecks=1)
            for _ in range(2):
                await promote_paid_link(pool, internal_user_id=awaiting['internal_user_id'], attempt_id=attempt, provider='yookassa', payment_id='payment-test', amount_kopecks=24900)
            assert await pool.fetchval('SELECT subscription_token FROM user_identities') == awaiting['subscription_token']
            assert await cleanup_expired_links(pool, delete=True) == 0
            await pool.execute("UPDATE pending_web_payment_attempts SET status='canceled' WHERE internal_user_id=$1", unknown['internal_user_id'])
            assert await cleanup_expired_links(pool, delete=True) == 1
            assert await pool.fetchval('SELECT count(*) FROM pending_web_payment_attempts') == 1
            racing = await create_link(pool)
            racing_attempt, _ = await begin_attempt(pool, racing['internal_user_id'], 'yookassa', 24900)
            await attach_payment(pool, racing_attempt, 'race-payment')
            await pool.execute("UPDATE pending_web_links SET expires_at=now()-interval '1 second' WHERE internal_user_id=$1", racing['internal_user_id'])
            results = await asyncio.gather(
                cleanup_expired_links(pool, delete=True),
                promote_paid_link(pool, internal_user_id=racing['internal_user_id'], attempt_id=racing_attempt, provider='yookassa', payment_id='race-payment', amount_kopecks=24900),
            )
            assert results[0] == 0
            assert await pool.fetchval('SELECT subscription_token FROM user_identities WHERE internal_user_id=$1', racing['internal_user_id']) == racing['subscription_token']
        finally:
            await pool.close()
            await conn.execute(f'DROP SCHEMA {schema} CASCADE')
            await conn.close()
    asyncio.run(scenario())

async def test_payment_requires_explicit_offer_and_current_amount(monkeypatch):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from app.web_api.payment import handle_create_payment
    from app.web_api import payment
    from app.web_api.legal import OFFER_VERSION
    import json
    pool = AsyncMock()
    monkeypatch.setattr(payment, 'require_auth', AsyncMock(return_value={'telegram_user_id':-1,'internal_user_id':'web_pending','auth_method':'pending_link'}))
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(pool=pool)), json=AsyncMock(return_value={'plan_id':'1m'}))
    response = await handle_create_payment(request)
    assert response.status_code == 400
    assert json.loads(response.body)['error'] == 'offer_acceptance_required'
    pool.fetchrow.assert_not_called()
    from app.domain.plans import get_plan
    monkeypatch.setattr(payment,'get_plan_pricing',lambda pool: SimpleNamespace(get_plan=AsyncMock(return_value=get_plan('1m')),get_global_discount=AsyncMock(return_value=None)))
    monkeypatch.setenv('YOOKASSA_ENABLED','1')
    request.json.return_value={'plan_id':'1m','offer_accepted':True,'offer_version':OFFER_VERSION,'expected_amount_kopecks':1}
    response = await handle_create_payment(request)
    assert response.status_code == 409
    assert json.loads(response.body)['error'] == 'price_changed'
    pool.execute.assert_not_called()


async def test_pending_auth_restricts_access(monkeypatch):
    from types import SimpleNamespace
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock
    from starlette.requests import Request
    from starlette.responses import JSONResponse
    from app.web_api.middleware import _resolve_subscription_token, _token_auth_fail_store
    _token_auth_fail_store.clear()
    row={'internal_user_id':'web_pending','telegram_user_id':-1,'expires_at':datetime.now(UTC)+timedelta(hours=48)}
    pool=AsyncMock()
    for path, allowed in [('/api/v1/user/profile',True),('/api/v1/payment/create',True),('/api/v1/user/keys',False),('/api/v1/payment/balance',False)]:
        pool.fetchrow.side_effect=[None,row]
        request=Request({'type':'http','path':path,'headers':[], 'server':('test',80),'scheme':'http','app':SimpleNamespace(state=SimpleNamespace(pool=pool))})
        result=await _resolve_subscription_token(request,'temporarytoken')
        if allowed:
            assert result['auth_method']=='pending_link'
        else:
            assert isinstance(result,JSONResponse) and result.status_code==403
