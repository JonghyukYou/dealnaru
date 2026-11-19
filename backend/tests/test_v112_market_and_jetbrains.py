"""중고나라 전용 경로·이전 출처 정리·JetBrains 공식 가격 검증."""
import json
from copy import deepcopy
from decimal import Decimal

import pytest

from app import main
from app.database import SessionLocal
from app.models import Game, Software, SoftwarePlan, UsedMarketItem, UsedMarketSummary, User, WishlistItem
from app.services import used_market_collect_service as market
from app.services.software_plan_service import load_plan_catalog, parse_plan_price
from app.services.subscription_service import seed_subscription_catalog
from test_api_smoke import client, reset_database  # noqa: F401


def jetbrains_entries():
    return {row['source_key']:row for row in load_plan_catalog()['plans'] if row['product']=='jetbrains'}


def jetbrains_document():
    return {'currency':{'symbol':'$','isPrefix':True,'iso':'USD'},'countryCode':'US',
            'ALL':{'code':'ALL','name':'All Products Pack',
                   'prices':{'personal':{'monthly':['$29.90'],'yearly':['$299.00','$239.00','$179.00'],'yearlyPerMonth':['$24.92','$19.92','$14.92']},
                             'commercial':{'monthly':['$97.90'],'yearly':['$979.00']}},
                   'disc_prices':{'personal':{'monthly':['0'],'yearly':['0','0','0']}}},
            'PC':{'code':'PC','prices':{'personal':{'monthly':['$10.90'],'yearly':['$109.00']}}}}


def page(data):
    return '<html><script>var productsData = '+json.dumps(data)+'; var other="ignored";</script><main>Store</main></html>'


def test_jetbrains_uses_personal_plan_first_year_total_and_correct_currency():
    plans=jetbrains_entries();html=page(jetbrains_document())
    assert parse_plan_price(plans['jetbrains-all-products'],html,'Store')==Decimal('29.90')
    assert parse_plan_price(plans['jetbrains-all-products-year'],html,'Store')==299
    # 같은 원문에서도 기업용을 명시하면 기업용만 선택합니다.
    commercial={**plans['jetbrains-all-products'],'pricing_section':'commercial'}
    assert parse_plan_price(commercial,html,'Store')==Decimal('97.90')
    assert parse_plan_price(plans['jetbrains-toolbox-free'],'','No, the application is completely free of charge.')==0


@pytest.mark.parametrize('change',['currency','product_code','missing_personal','malformed_prices','missing_interval','zero'])
def test_jetbrains_does_not_borrow_wrong_or_ambiguous_prices(change):
    data=deepcopy(jetbrains_document())
    if change=='currency':data['currency']['iso']='EUR'
    elif change=='product_code':data['ALL']['code']='PC'
    elif change=='missing_personal':data['ALL']['prices'].pop('personal')
    elif change=='malformed_prices':data['ALL']['prices']='not a price map'
    elif change=='missing_interval':data['ALL']['prices']['personal'].pop('monthly')
    elif change=='zero':data['ALL']['prices']['personal']['monthly']=['$0.00']
    with pytest.raises(ValueError):
        parse_plan_price(jetbrains_entries()['jetbrains-all-products'],page(data),'')


def test_jetbrains_conflicting_price_documents_are_not_accepted():
    data=jetbrains_document();other=deepcopy(data)
    other['ALL']['prices']['personal']['monthly']=['$39.90']
    with pytest.raises(ValueError):
        parse_plan_price(jetbrains_entries()['jetbrains-all-products'],page(data)+page(other),'')


def test_existing_unknown_jetbrains_plan_is_updated_without_recreating_user_data(client):
    with SessionLocal() as db:
        seed_subscription_catalog(db)
        row=db.query(SoftwarePlan).filter_by(source_key='jetbrains-all-products').one();plan_id=row.id
        row.current_price=None;row.checked_at=None;row.source_status='reference';db.commit()
        before_users=db.query(User).count();before_wishlist=db.query(WishlistItem).count()
        seed_subscription_catalog(db)
        assert row.id==plan_id and float(row.current_price)==29.90 and row.source_status=='verified'
        assert db.query(User).count()==before_users and db.query(WishlistItem).count()==before_wishlist
        product=db.query(Software).filter_by(source_key='jetbrains').one();product_id=product.id
    detail=client.get(f'/api/software/{product_id}').json()
    plans={row['source_key']:row for row in detail['plans']}
    assert plans['jetbrains-all-products']['current_price']==29.90
    assert plans['jetbrains-all-products-year']['current_price']==299
    assert plans['jetbrains-all-products-year']['monthly_equivalent']==24.92
    assert plans['jetbrains-all-products-year']['annual_saving_rate']==16.7
    assert plans['jetbrains-toolbox-free']['current_price']==0
    assert detail['has_free_plan'] and len(plans)==3


def test_removed_source_is_excluded_from_search_keywords_prices_and_links(client):
    with SessionLocal() as db:
        db.add(UsedMarketItem(source='중고나라',keyword='엘든링',title='엘든링 PS5',price=25000,item_url='https://web.joongna.com/product/12345'))
        db.add(UsedMarketItem(source='당근',keyword='제거된키워드',title='엘든링 PS5',price=1,item_url='https://www.daangn.com/kr/buy-sell/old-12345/'))
        db.commit()
    result=client.get('/api/used-market',params={'keyword':'엘든링'}).json()
    assert result['lowest_price']==25000 and result['total']==1
    assert all(row['source']=='중고나라' for row in result['items'])
    assert '제거된키워드' not in result['keywords']
    assert len(result['search_links'])==1 and result['search_links'][0]['url'].startswith('https://web.joongna.com/')


def test_legacy_source_cleanup_is_selective_and_idempotent():
    with SessionLocal() as db:
        before_users=db.query(User).count();before_games=db.query(Game).count()
        db.add_all([
            UsedMarketItem(source='중고나라',keyword='게임',title='정상 게임',price=25000,item_url='https://web.joongna.com/product/12345'),
            UsedMarketItem(source='당근',keyword='게임',title='이전 게임',price=10000,item_url='https://www.daangn.com/kr/buy-sell/old-a/'),
            UsedMarketItem(source='당근마켓',keyword='게임',title='이전 게임',price=20000,item_url='https://www.daangn.com/kr/buy-sell/old-b/'),
        ]);db.commit()
        assert market.remove_retired_market_data(db)==2
        db.commit()
        assert market.remove_retired_market_data(db)==0
        assert db.query(UsedMarketItem).one().source=='중고나라'
        assert db.query(User).count()==before_users and db.query(Game).count()==before_games


def test_collector_requests_only_joongna_and_rejects_unsupported_storage(monkeypatch):
    calls=[]
    def fetch(url,source):
        calls.append((url,source))
        return '<a href="/product/12345"><p>엘든링 PS5</p><h3>25,000</h3></a>'
    monkeypatch.setattr(market,'fetch_public_page',fetch)
    with SessionLocal() as db:
        result=market.collect_used_market_items(db,['엘든링'])
        assert result['imported_count']==1 and len(result['source_results'])==1
        assert result['source_results'][0]['source']=='중고나라'
        with pytest.raises(ValueError):
            market.upsert_used_market_item(db,{'source':'retired-market','title':'엘든링 PS5','price':25000,'item_url':'https://example.org/old/1','keyword':'엘든링'})
    assert calls==[('https://web.joongna.com/search/%EC%97%98%EB%93%A0%EB%A7%81','joongna')]
    assert set(market.PROVIDERS)=={'joongna'}
    with pytest.raises(ValueError):market.build_search_url('retired-market','엘든링')


def test_removed_fields_are_absent_from_api_contract_and_game_summary(client):
    schema=client.get('/openapi.json').json()
    model=schema['components']['schemas']['UsedMarketCollectRequest']['properties']
    assert set(model)=={'keywords','limit_per_keyword'}
    params=schema['paths']['/api/used-market']['get']['parameters']
    assert 'source' not in {p['name'] for p in params}
    assert 'carrot_average' not in UsedMarketSummary.__table__.columns
    assert client.post('/api/auth/login',json={'email':'admin@test.local','password':'admin-pass-1234'}).status_code==200
    assert client.post('/api/admin/used-market/collect',json={'sources':['retired-market']}).status_code==422
    assert client.post('/api/admin/used-market/collect',json={'region':'retired-region'}).status_code==422


def test_existing_database_with_unused_legacy_column_still_accepts_new_summaries():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from app.database import Base
    from app.services.schema_service import upgrade_subscription_schema
    engine=create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with engine.begin() as connection:
        connection.execute(text('ALTER TABLE used_market_summaries ADD COLUMN carrot_average INTEGER'))
    upgrade_subscription_schema(engine,metadata=Base.metadata)
    with Session(engine) as db:
        game=Game(title='Legacy schema compatibility');db.add(game);db.flush()
        db.add(UsedMarketSummary(game_id=game.id,joonggonara_average=25000));db.commit()
        assert db.query(UsedMarketSummary).one().joonggonara_average==25000
        assert 'carrot_average' not in UsedMarketSummary.__mapper__.attrs
    engine.dispose()
