"""새 요금 파싱·검색 전체 최저가·예측 검증·개인정보 삭제 회귀 테스트."""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import accumulate
from types import SimpleNamespace

import pytest
from app import main
from app.database import SessionLocal
from app.models import Game, Notification, Software, SoftwarePlan, UsedMarketItem, User, UserSession, WishlistItem
from app.services import software_plan_service as pricing
from app.services import used_market_collect_service as market
from app.services.prediction_service import clean_history, fit_cycle, predict_discount_from_history, rolling_backtest, extract_discount_events
from app.services.subscription_service import format_subscription_card, seed_subscription_catalog
from app.utils.time import utc_now
from test_api_smoke import client, reset_database  # noqa: F401


def entries():
    return {entry['source_key']: entry for entry in pricing.load_plan_catalog()['plans']}


def test_new_adapters_distinguish_billing_and_trial():
    plans = entries()
    table = '<table><tr><th></th><th>Basic Plan</th><th>Pro Plan</th></tr><tr><td>Monthly Price</td><td>$10</td><td>$60</td></tr><tr><td>Annual Price</td><td>$96 ($8 / month)</td><td>$576 ($48 / month)</td></tr></table>'
    assert pricing.parse_plan_price(plans['midjourney-basic-month'], table, '') == 10
    assert pricing.parse_plan_price(plans['midjourney-basic-year'], table, '') == 96
    body = 'Starter $1 first month Then $6 per month Creator Popular $11 first month Then $22 per month'
    assert pricing.parse_plan_price(plans['elevenlabs-starter'], '', body) == 6
    assert pricing.parse_plan_price(plans['elevenlabs-creator'], '', body) == 22
    with pytest.raises(ValueError):
        pricing.parse_plan_price(plans['elevenlabs-starter'], '', 'Starter $1 first month')
    assert pricing.parse_plan_price(plans['descript-hobbyist-year'], '', 'Hobbyist $16 $24 per person / month') == 192


def test_structured_price_requires_currency_interval_and_unique_amount():
    entry = dict(entries()['chatgpt-plus-month'], pattern=r'(?!)')
    def offer(currency, duration, amount):
        return {'@type':'Offer','name':'Plus','priceCurrency':currency,'billingDuration':duration,'price':amount}
    def html(objects):
        return '<script type="application/ld+json">' + json.dumps(objects) + '</script>'
    with pytest.raises(ValueError):
        pricing.parse_plan_price(entry, html([offer('KRW','P1M',20),offer('USD','P1Y',200)]), '')
    assert pricing.parse_plan_price(entry, html([offer('USD','P1M',20)]), '') == 20
    with pytest.raises(ValueError):
        pricing.parse_plan_price(entry, html([offer('USD','P1M',20),offer('USD','P1M',25)]), '')


def test_notion_escaped_json_keeps_currency_billing_and_duplicate_safety():
    obj={'external_id':'plus_monthly_usd','product':'plus','billing_interval':'month','currency':'USD','unit_amount':1200,'state':'current'}
    raw=json.dumps(obj)
    html='<script>self.__next_f.push([1,'+json.dumps(raw)+']);</script>'
    assert pricing.parse_plan_price(entries()['notion-plus-month-usd'],html,'') == 12
    with pytest.raises(ValueError):
        pricing.parse_plan_price(entries()['notion-plus-month'],html,'')
    with pytest.raises(ValueError):
        pricing.parse_plan_price(entries()['notion-plus-month-usd'],raw+json.dumps({**obj,'unit_amount':1300}),'')


def test_catalog_preserves_newer_runtime_prices_and_dates_last_known_prices():
    with SessionLocal() as db:
        seed_subscription_catalog(db)
        row=db.query(SoftwarePlan).filter_by(source_key='chatgpt-plus-month').one()
        row.current_price=Decimal('21');row.checked_at=utc_now()+timedelta(days=1)
        row.url='https://outdated.example/pricing';db.commit()
        seed_subscription_catalog(db)
        assert row.current_price == 21 and 'help.openai.com' in row.url
        row.checked_at=utc_now()-timedelta(days=12);row.source_status='needs_check'
        formatted=pricing.format_software_plan(row)
        assert formatted['current_price'] is None
        assert formatted['display_price'] == formatted['last_confirmed_price'] == 21
        assert formatted['price_basis'] == 'last_confirmed'
        free=db.query(Software).filter_by(source_key='libreoffice').one()
        card=format_subscription_card(free)
        assert card['starting_plan']['display_price'] == 0 and card['has_free_plan']


@pytest.mark.parametrize('value,expected',[('35,000원',35000),('1.5만원',15000),('2만 5천원',25000),('1만5000원',15000),('30000',30000),('가격협의',None),('나눔',None),('20,000원 30,000원',None),(None,None)])
def test_market_price_parser(value,expected):
    assert market.parse_price(value)==expected


def test_market_html_does_not_mix_neighbors_or_include_sold_digital():
    html='''<main><article><a href="/product/501"><h2>엘든링 PS5</h2><p>25,000원</p>2시간 전</a></article>
    <article><a href="/product/502"><h2>엘든링 PS4</h2><p>10,000원</p>판매완료</a></article>
    <article><a href="/product/503"><h2>엘든링 디지털 코드</h2><p>5,000원</p></a></article></main>'''
    rows=market.parse_market_page(html,'joongna','엘든링',20)
    assert len(rows)==1 and rows[0]['price']==25000 and rows[0]['title']=='엘든링 PS5'
    assert rows[0]['source']=='중고나라' and 'seller' not in rows[0]
    assert market.parse_market_page('<p>엘든링 1만원</p>','joongna','엘든링',20)==[]


def test_structured_market_prices_and_links_are_validated():
    data={'products':[
        {'title':'엘든링 PS5','price':'30,000','url':'/product/501','status':'ON_SALE','regionName':'역삼동'},
        {'title':'엘든링 PS5','price':1,'url':'https://evil.example/item/1'},
        {'title':'엘든링 PS5','price':5000,'url':'/product/502','status':'SOLD'},
        {'title':'엘든링 PS5','price':30,'url':'/product/504','currency':'USD'}]}
    rows=market.parse_market_page('<script type="application/json">'+json.dumps(data)+'</script>','joongna','엘든링',20)
    assert len(rows)==1 and rows[0]['price']==30000 and rows[0]['location']=='역삼동'
    assert market.canonical_item_url('//evil.example/product/5','joongna') is None


def test_robots_disallow_prevents_search_request(monkeypatch):
    calls=[]
    def request(url):
        calls.append(url);return 'User-agent: *\nDisallow: /search/'
    monkeypatch.setattr(market,'_ROBOTS',{})
    monkeypatch.setattr(market,'_request_text',request)
    with pytest.raises(market.CollectionUnavailable):
        market.fetch_public_page(market.build_search_url('joongna','엘든링'),'joongna')
    assert calls==['https://web.joongna.com/robots.txt']


def test_blocked_collector_reports_error_without_inventing_listings(monkeypatch):
    def fetch(url,source):
        raise market.CollectionUnavailable('HTTP 403')
    monkeypatch.setattr(market,'fetch_public_page',fetch)
    with SessionLocal() as db:
        result=market.collect_used_market_items(db,['엘든링','젤다'])
        assert result['error_count']==1 and result['imported_count']==0
        assert len(result['source_results'])==1 and result['source_results'][0]['status']=='blocked'
        assert db.query(UsedMarketItem).count()==0


def test_lowest_uses_all_pages_ties_freshness_and_status(client):
    with SessionLocal() as db:
        for i in range(15):
            db.add(UsedMarketItem(source='중고나라',keyword='PS5',title=f'엘든링 PS5 {i}',price=30000+i*100,item_url=f'https://web.joongna.com/product/{i}',collected_at=utc_now()))
        for i in range(2):
            db.add(UsedMarketItem(source='중고나라',keyword='PS5',title='엘든링 PS5',price=20000,item_url=f'https://web.joongna.com/product/tie-{i}',collected_at=utc_now()-timedelta(days=2)))
        for i,price,status,age in [(0,1,'판매완료',0),(1,2,'판매중',20),(2,0,'판매중',0),(3,None,'판매중',0),(4,3,'예약중',0)]:
            db.add(UsedMarketItem(source='중고나라',keyword='PS5',title='엘든링 PS5',price=price,status=status,item_url=f'https://web.joongna.com/product/excluded-{i}',collected_at=utc_now()-timedelta(days=age)))
        db.commit()
    first=client.get('/api/used-market',params={'keyword':'엘든링','size':1}).json()
    assert first['lowest_price']==20000 and first['lowest_count']==2 and first['items'][0]['is_lowest']
    second=client.get('/api/used-market',params={'keyword':'엘든링','size':1,'page':2}).json()
    assert second['lowest_price']==20000 and second['items'][0]['is_lowest']
    third=client.get('/api/used-market',params={'keyword':'엘든링','size':1,'page':3}).json()
    assert not third['items'][0]['is_lowest']
    assert all(row['source']=='중고나라' for row in first['items'])
    all_items=client.get('/api/used-market').json()
    assert all_items['lowest_price'] is None and not any(row['is_lowest'] for row in all_items['items'])
    assert len(all_items['search_links'])==1 and all_items['search_links'][0]['source']=='중고나라'


def snapshots(intervals=(30,30,30,30,30),last_age=5):
    now=datetime(2026,11,19,12);start=now-timedelta(days=sum(intervals)+last_age);prices=[]
    for offset in [0,*accumulate(intervals)]:
        at=start+timedelta(days=offset)
        prices += [SimpleNamespace(collected_at=at,discount_rate=40,discounted_price=30000,original_price=50000,currency='KRW',is_available=True),SimpleNamespace(collected_at=at+timedelta(days=2),discount_rate=0,discounted_price=50000,original_price=50000,currency='KRW',is_available=True)]
    return prices,now


def test_forecast_is_future_bounded_with_real_rolling_error():
    prices,now=snapshots();result=predict_discount_from_history(prices,now)
    assert result['forecast_window'] and result['backtest']=={'samples':3,'mae_days':0.0}
    assert datetime.fromisoformat(result['forecast_window']['start']) > now
    assert result['evidence']['weighted_cycle_days']==30 and result['confidence_score']>50
    assert result['expected_discount_rate']=='40% 내외' and 'probability' not in result


def test_prediction_downweights_outliers_and_backtests_without_future_leakage():
    assert fit_cycle([30,30,30,300,30])[0]<60
    assert fit_cycle([90,90,90,30,30,30])[0]<60
    prices,now=snapshots();events=extract_discount_events(prices);old=rolling_backtest(events[:4])
    events[-1].start_date += timedelta(days=10)
    assert rolling_backtest(events[:4])==old


def test_prediction_handles_stale_missing_duplicate_future_and_timezone():
    assert predict_discount_from_history([])['forecast_window'] is None
    prices,now=snapshots(last_age=50);result=predict_discount_from_history(prices,now)
    assert result['forecast_window'] is None and result['expected_event']=='최신 이력 필요'
    prices,now=snapshots()
    assert len(clean_history(prices+[prices[0]],now))==len(prices)
    future=SimpleNamespace(collected_at=now+timedelta(days=1),discount_rate=90,discounted_price=1,currency='KRW')
    assert clean_history(prices+[future],now)==clean_history(prices,now)
    assert predict_discount_from_history(prices,now.replace(tzinfo=timezone.utc))['forecast_window']


def test_current_sale_is_separate_from_next_event():
    prices,now=snapshots(last_age=0);result=predict_discount_from_history(prices,now)
    assert result['current_sale'] and result['forecast_window']['start']>now.date().isoformat()


def test_sparse_positive_observations_do_not_invent_new_sale_events():
    now=datetime(2026,11,19,12)
    prices=[SimpleNamespace(collected_at=now-timedelta(days=age),discount_rate=40,discounted_price=30000,currency='KRW') for age in (180,120,60,0)]
    result=predict_discount_from_history(prices,now)
    assert result['evidence']['event_count']==1 and result['forecast_window'] is None


def test_withdrawal_checks_password_deletes_personal_rows_and_all_sessions(client):
    assert client.post('/api/auth/withdraw',json={'password':'x'}).status_code==401
    assert client.post('/api/auth/login',json={'email':'user@test.local','password':'user-pass-1234'}).status_code==200
    with SessionLocal() as db:
        user=db.query(User).filter_by(email='user@test.local').one();uid=user.id;gid=db.query(Game.id).first()[0]
        existing=db.query(WishlistItem).filter_by(user_id=uid,game_id=gid).first()
        if existing is None:db.add(WishlistItem(user_id=uid,game_id=gid,target_price=12345))
        db.add(UserSession(token_hash='a'*64,user_id=uid,expires_at=utc_now()+timedelta(hours=1)))
        db.add(Notification(user_id=uid,title='목표가',message='알림'));db.commit()
    assert client.post('/api/auth/withdraw',json={'password':'wrong'}).status_code==400
    assert client.get('/api/auth/me').status_code==200
    assert client.post('/api/auth/withdraw',json={'password':'user-pass-1234'}).status_code==204
    assert client.get('/api/auth/me').status_code==401
    with SessionLocal() as db:
        assert db.get(User,uid) is None
        assert db.query(UserSession).filter_by(user_id=uid).count()==0
        assert db.query(WishlistItem).filter_by(user_id=uid).count()==0
        assert db.query(Notification).filter_by(user_id=uid).count()==0
        assert db.query(Game).count()>0


def test_production_signup_needs_age_confirmation(monkeypatch,client):
    monkeypatch.setattr(main,'PRODUCTION',True)
    payload={'email':'age-check@proton.me','nickname':'연령확인','password':'long-password-123','accept_terms':True}
    response=client.post('/api/auth/register',json=payload)
    assert response.status_code==400 and '14세' in response.json()['detail']
