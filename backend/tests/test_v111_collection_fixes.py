"""첨부 화면의 가격 제목·동적 Google 요금·수집 오류 원인 손실 회귀 테스트."""
import json
from datetime import timedelta
from decimal import Decimal

import pytest
import requests

from app.database import SessionLocal
from app.models import Software, SoftwarePlan, UsedMarketItem
from app.services import google_pricing_service as google
from app.services import software_plan_service as pricing
from app.services import used_market_collect_service as market
from app.services.subscription_service import seed_subscription_catalog
from app.utils.time import utc_now
from test_api_smoke import client, reset_database  # noqa: F401


def google_html():
    return '''<html><script type="module" src="/about/assets/d/layout.js"></script>
    <script type="module" src="/about/assets/d/pricing-component.js"></script>
    <main><h2>Google AI Plus</h2><g1-localized-price country="KR" variant="PRICE_GEN_AI_PLUS_MONTHLY"></g1-localized-price>/월
    <h2>Google AI Pro</h2><g1-localized-price country="KR" variant="PRICE_GEN_AI_PRO_MONTHLY"></g1-localized-price>/월
    <h2>Google AI Ultra</h2>최저 <g1-localized-price country="KR" variant="PRICE_GEN_AI_ULTRA_100_MONTHLY"></g1-localized-price>/월
    <h3>Google AI Ultra 20배</h3><g1-localized-price country="KR" variant="PRICE_GEN_AI_ULTRA_200_MONTHLY"></g1-localized-price>/월
    <span>매월 $10 크레딧</span></main></html>'''


def google_feed(**overrides):
    return {'COUNTRY_CODE':'KR','CURRENCY_CODE':'KRW',
            'PRICE_GEN_AI_PLUS_MONTHLY':7500,'PRICE_GEN_AI_PRO_MONTHLY':29000,
            'PRICE_GEN_AI_ULTRA_100_MONTHLY':119000,'PRICE_GEN_AI_ULTRA_200_MONTHLY':300000,
            **overrides}


def fetch_google_fixture(feed=None, calls=None):
    def fetch(url):
        if calls is not None:calls.append(url)
        if url.endswith('/layout.js'):return 'customElements.define("layout", Layout)'
        if url.endswith('/pricing-component.js'):
            return 'const file=preview()||`pricing_2027_02_03.json`;customElements.define(`g1-localized-price`,Price);'
        assert url=='https://one.google.com/intl/ALL_kr/about/feeds/pricing_2027_02_03.json'
        return json.dumps(google_feed() if feed is None else feed)
    return fetch


@pytest.mark.parametrize('markup,expected',[
    ('<a href="/product/123"><h3>39,000</h3><p>엘든링 PS5</p></a>','엘든링 PS5'),
    ('<a href="/product/123"><h3>39,000원</h3><p>엘든링 PS5</p></a>','엘든링 PS5'),
    ('<a href="/product/123"><img alt="상품이미지"><h3>39,000</h3><div class="productName">엘든링 PS5</div></a>','엘든링 PS5'),
    ('<a href="/product/123"><span>엘든링 PS5</span><h3>39,000</h3><span>5시간 전</span></a>','엘든링 PS5'),
    ('<p>엘든링 PS5</p><a href="/product/123"><h3>39,000</h3></a>','엘든링 PS5'),
])
def test_price_h3_is_not_a_product_title(markup,expected):
    rows=market.parse_market_page('<main><article>'+markup+'</article></main>','joongna','게임CD',20)
    assert len(rows)==1 and rows[0]['title']==expected and rows[0]['price']==39000


def test_plain_number_price_neighbor_cards_and_trailing_price_are_separate():
    html='''<main><article><a href="/product/1"><h3>38,000</h3><p>PS4 세키로 팝니다 38,000</p></a></article>
    <article><a href="/product/2"><h3>15,000</h3><p>PS4 FIFA 17 피파 축구게임 15,000</p></a></article>
    <article><a href="/product/3"><h3>12,000</h3><span>4시간 전</span></a></article></main>'''
    rows=market.parse_market_page(html,'joongna','PS4',20)
    assert [(row['title'],row['price']) for row in rows]==[('PS4 세키로 팝니다',38000),('PS4 FIFA 17 피파 축구게임',15000)]


def test_structured_title_uses_product_name_when_title_contains_price():
    data={'products':[{'title':'39,000','productName':'엘든링 PS5','price':39000,'productSeq':123}]}
    rows=market.parse_market_page('<script type="application/json">'+json.dumps(data)+'</script>','joongna','게임CD',20)
    assert len(rows)==1 and rows[0]['title']=='엘든링 PS5'


def test_existing_bad_titles_are_repaired_or_excluded_then_restored_by_url(client):
    with SessionLocal() as db:
        bad=UsedMarketItem(source='중고나라',keyword='게임CD',title='39,000',price=39000,item_url='https://web.joongna.com/product/991')
        appended=UsedMarketItem(source='중고나라',keyword='PS4',title='PS4 세키로 팝니다 38,000',price=38000,item_url='https://web.joongna.com/product/992')
        db.add_all([bad,appended]);db.commit();bad_id=bad.id
        result=market.repair_used_market_titles(db,commit=True)
        assert result=={'cleaned_titles':1,'quarantined_titles':1}
        assert bad.status=='제목 확인 필요' and appended.title=='PS4 세키로 팝니다'
        assert db.get(UsedMarketItem,bad_id) is not None
    rows=client.get('/api/used-market').json()['items']
    assert all(row['title']!='39,000' for row in rows)
    with SessionLocal() as db:
        row,created=market.upsert_used_market_item(db,{'source':'중고나라','keyword':'게임CD','title':'엘든링 PS5','price':39000,'item_url':'https://web.joongna.com/product/991'})
        db.commit()
        assert not created and row.id==bad_id and row.status=='판매중'
    assert any(row['id']==bad_id and row['title']=='엘든링 PS5' for row in client.get('/api/used-market').json()['items'])


def test_price_only_title_cannot_be_saved():
    with SessionLocal() as db:
        with pytest.raises(ValueError):
            market.upsert_used_market_item(db,{'source':'중고나라','keyword':'게임CD','title':'39,000','price':39000,'item_url':'https://web.joongna.com/product/999'})
        assert db.query(UsedMarketItem).count()==0


def test_google_uses_dynamic_official_feed_name_and_all_four_exact_keys():
    calls=[]
    html=google.hydrate_google_prices(google_html(),fetch=fetch_google_fixture(calls=calls))
    entries={p['source_key']:p for p in pricing.load_plan_catalog()['plans']}
    for key,expected in [('google-ai-plus',7500),('google-ai-pro',29000),('google-ai-ultra-5x',119000),('google-ai-ultra-20x',300000)]:
        assert pricing.parse_plan_price(entries[key],html,'')==expected
    assert any('pricing_2027_02_03.json' in url for url in calls)
    assert all(url.startswith('https://one.google.com/') for url in calls)


@pytest.mark.parametrize('bad_feed',[google_feed(COUNTRY_CODE='US'),google_feed(CURRENCY_CODE='USD'),[],{'COUNTRY_CODE':'KR','CURRENCY_CODE':'KRW'}])
def test_google_does_not_use_wrong_country_currency_or_empty_prices(bad_feed):
    with pytest.raises(ValueError):
        google.hydrate_google_prices(google_html(),fetch=fetch_google_fixture(feed=bad_feed))


def test_google_missing_tier_does_not_borrow_another_tier_or_credit():
    feed=google_feed(PRICE_GEN_AI_PRO_MONTHLY=None)
    html=google.hydrate_google_prices(google_html(),fetch=fetch_google_fixture(feed=feed))
    entry=next(row for row in pricing.load_plan_catalog()['plans'] if row['source_key']=='google-ai-pro')
    with pytest.raises(ValueError):pricing.parse_plan_price(entry,html,'매월 $10 크레딧')


def test_google_seed_updates_existing_empty_plan_and_api_shows_price(client):
    with SessionLocal() as db:
        seed_subscription_catalog(db)
        pro=db.query(SoftwarePlan).filter_by(source_key='google-ai-pro').one()
        pro.current_price=None;pro.regular_price=None;pro.checked_at=None;pro.source_status='needs_check';db.commit()
        seed_subscription_catalog(db)
        gemini_id=db.query(Software.id).filter_by(source_key='gemini').scalar()
    detail=client.get(f'/api/software/{gemini_id}').json()
    plans={row['source_key']:row for row in detail['plans']}
    assert plans['google-ai-plus']['current_price']==7500
    assert plans['google-ai-pro']['current_price']==29000
    assert plans['google-ai-ultra-5x']['current_price']==119000
    assert plans['google-ai-ultra-20x']['current_price']==300000
    assert all(row['status']=='verified' and row['checked_at'] for row in plans.values())


def test_google_refresh_failure_retains_confirmed_amount_and_date(monkeypatch):
    catalog=pricing.load_plan_catalog()
    entry=next(row for row in catalog['plans'] if row['source_key']=='google-ai-pro')
    with SessionLocal() as db:
        seed_subscription_catalog(db)
        row=db.query(SoftwarePlan).filter_by(source_key=entry['source_key']).one()
        row.checked_at=utc_now()-timedelta(days=1);db.commit();checked=row.checked_at
        monkeypatch.setattr(pricing,'load_plan_catalog',lambda:{**catalog,'plans':[entry]})
        def fail(url):raise requests.ConnectionError('test')
        monkeypatch.setattr(pricing,'fetch_plan_page',fail)
        result=pricing.refresh_software_plans(db)
        assert result['error_count']==1 and row.current_price==Decimal('29000') and row.checked_at==checked
        formatted=pricing.format_software_plan(row)
        assert formatted['current_price'] is None and formatted['display_price']==29000 and formatted['collection_error']


@pytest.mark.parametrize('blocked_stage',['robots','page'])
def test_collection_diagnostics_preserve_http_status_and_failure_stage(monkeypatch,blocked_stage):
    calls=[]
    def request(url):
        calls.append(url)
        if url.endswith('/robots.txt') and blocked_stage=='page':return 'User-agent: *\nAllow: /'
        raise market.CollectionUnavailable('공개 요청 HTTP 403',reason_code='http_403',http_status=403)
    monkeypatch.setattr(market,'_request_text',request)
    monkeypatch.setattr(market,'_ROBOTS',{})
    monkeypatch.setattr(market,'_LAST_REQUEST',{})
    monkeypatch.setattr(market,'sleep',lambda _:None)
    with SessionLocal() as db:
        result=market.collect_used_market_items(db,['엘든링'])
    row=result['source_results'][0]
    assert row['status']=='blocked' and row['http_status']==403 and row['stage']==blocked_stage and row['reason_code']=='http_403'
    assert result['imported_count']==0 and result['error_count']==1
    assert len(calls)==(1 if blocked_stage=='robots' else 2)


def test_robots_timeout_is_reported_as_connection_failure_not_source_block(monkeypatch):
    def timeout(url):raise requests.Timeout('test')
    monkeypatch.setattr(market,'_request_text',timeout)
    monkeypatch.setattr(market,'_ROBOTS',{})
    with SessionLocal() as db:
        result=market.collect_used_market_items(db,['엘든링'])
    row=result['source_results'][0]
    assert row['status']=='unavailable' and row['stage']=='robots' and row['reason_code']=='robots_timeout'
    assert row['http_status'] is None
