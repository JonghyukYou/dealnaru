from datetime import timedelta

import pytest
from test_api_smoke import auth_header, login
from test_api_smoke import client as client
from test_api_smoke import reset_database as reset_database

from app.database import SessionLocal
from app.models import Game, GamePriceTest, Notification, PriceSnapshot, UsedMarketExclusion, UsedMarketItem, User, WishlistItem
from app.services import collection_service as collection
from app.services import used_market_collect_service as market
from app.services import email_service
from app.services.used_market_moderation_service import ExcludedMarketItem
from app.utils.time import utc_now


def admin_headers(client):
    return auth_header(login(client, 'admin@test.local', 'admin-pass-1234'))


def seed_market():
    with SessionLocal() as db:
        db.query(UsedMarketItem).delete()
        for i, (keyword, title, price) in enumerate([
            ('소닉', '다이슨 슈퍼소닉', 8000), ('소닉', '레고 소닉', 15000),
            ('세키로', '잘못 수집한 상품', 9000), ('닌텐도', '소닉 닌텐도 게임', 20000),
        ]):
            db.add(UsedMarketItem(keyword=keyword, title=title, price=price, source='중고나라',
                                  item_url=f'https://web.joongna.com/product/{100+i}', collected_at=utc_now()))
        db.commit()


def add_watchers():
    with SessionLocal() as db:
        db.query(Notification).delete()
        db.query(WishlistItem).delete()
        game = db.query(Game).filter_by(title='Test Game').one()
        for user in db.query(User).all():
            db.add(WishlistItem(user_id=user.id, game_id=game.id, target_price=12000, notify_enabled=True))
        db.commit()
        return game.id


def create_test(client, headers, game_id, **changes):
    payload = {'game_id': game_id, 'platform': 'Steam', 'discounted_price': 10000}
    payload.update(changes)
    return client.post('/api/admin/price-tests', headers=headers, json=payload)


def test_keyword_deletion_is_exact_and_removes_tab_but_keeps_other_collections(client):
    seed_market()
    response = client.delete('/api/admin/used-market/keyword', params={'keyword': '소닉'}, headers=admin_headers(client))
    assert response.status_code == 200 and response.json()['deleted_count'] == 2
    data = client.get('/api/used-market').json()
    assert '소닉' not in data['keywords'] and '세키로' in data['keywords']
    assert {row['title'] for row in data['items']} == {'잘못 수집한 상품', '소닉 닌텐도 게임'}
    assert client.get('/api/used-market', params={'keyword': '닌텐도'}).json()['lowest_price'] == 20000
    with SessionLocal() as db:
        assert db.query(UsedMarketExclusion).one().kind == 'keyword'


def test_deleted_keyword_is_not_requested_again_and_can_be_reallowed(client, monkeypatch):
    seed_market()
    headers = admin_headers(client)
    client.delete('/api/admin/used-market/keyword', params={'keyword': '소닉'}, headers=headers)
    monkeypatch.setattr(market, 'fetch_public_page', lambda *args: pytest.fail('Deleted keyword must not be fetched'))
    with SessionLocal() as db:
        result = market.collect_used_market_items(db, keywords=['소닉'])
        assert result['imported_count'] == 0 and result['skipped_count'] == 1 and result['error_count'] == 0
    rule = client.get('/api/admin/used-market/exclusions', headers=headers).json()[0]
    assert client.delete(f"/api/admin/used-market/exclusions/{rule['id']}", headers=headers).status_code == 200
    with SessionLocal() as db:
        item, created = market.upsert_used_market_item(db, {'keyword': '소닉', 'title': '소닉 게임CD', 'price': 15000,
            'source': '중고나라', 'item_url': 'https://web.joongna.com/product/100'})
        assert created
        db.commit()
    assert '소닉' in client.get('/api/used-market').json()['keywords']


def test_individual_delete_updates_lowest_and_prevents_url_reimport(client):
    seed_market()
    headers = admin_headers(client)
    data = client.get('/api/used-market', params={'keyword': '소닉'}).json()
    item = next(row for row in data['items'] if row['title'] == '다이슨 슈퍼소닉')
    assert client.delete(f"/api/admin/used-market/items/{item['id']}", headers=headers).status_code == 200
    assert client.get('/api/used-market', params={'keyword': '소닉'}).json()['lowest_price'] == 15000
    with SessionLocal() as db:
        with pytest.raises(ExcludedMarketItem):
            market.upsert_used_market_item(db, {'keyword': '다른 검색어', 'title': '같은 매물', 'price': 8000,
                'source': '중고나라', 'item_url': item['item_url']})


def test_excluded_rows_are_hidden_even_if_an_inflight_collector_had_saved_them(client):
    seed_market()
    headers = admin_headers(client)
    client.delete('/api/admin/used-market/keyword', params={'keyword': '소닉'}, headers=headers)
    with SessionLocal() as db:
        db.add(UsedMarketItem(keyword='소닉', title='늦게 저장된 수집 결과', price=1000,
            item_url='https://web.joongna.com/product/999', collected_at=utc_now()))
        db.commit()
    data = client.get('/api/used-market').json()
    assert '소닉' not in data['keywords'] and data['total'] == 2
    assert all(row['price'] != 1000 for row in data['items'])


def test_market_moderation_requires_admin_and_all_tab_cannot_be_deleted(client):
    seed_market()
    assert client.delete('/api/admin/used-market/keyword', params={'keyword': '소닉'}).status_code == 401
    user = auth_header(login(client, 'user@test.local', 'user-pass-1234'))
    assert client.delete('/api/admin/used-market/keyword', params={'keyword': '소닉'}, headers=user).status_code == 403
    assert client.get('/api/admin/used-market/exclusions', headers=user).status_code == 403
    assert client.delete('/api/admin/used-market/items/1', headers=user).status_code == 403
    assert client.delete('/api/admin/used-market/keyword', params={'keyword': '전체'}, headers=admin_headers(client)).status_code == 400
    with SessionLocal() as db:
        assert db.query(UsedMarketItem).count() == 4 and db.query(UsedMarketExclusion).count() == 0


def test_test_price_reaches_detail_wishlist_and_chart_without_changing_observations(client, monkeypatch):
    game_id = add_watchers()
    headers = admin_headers(client)
    before = client.get(f'/api/games/{game_id}').json()
    with SessionLocal() as db:
        rows = [(p.id, p.discounted_price, p.is_current, p.collected_at) for p in db.query(PriceSnapshot)]
    monkeypatch.setattr(collection, 'send_target_price_email', lambda **kwargs: pytest.fail('Email is opt-in for tests'))
    response = create_test(client, headers, game_id)
    assert response.status_code == 201
    assert response.json()['notifications_created'] == 1 and response.json()['emails_sent'] == 0
    detail = client.get(f'/api/games/{game_id}').json()
    assert detail['discounted_price'] == 10000 and detail['is_test_price']
    assert detail['prices'][0]['is_test_price'] and len(detail['active_price_tests']) == 1
    assert detail['stats'] == before['stats'] and detail['prediction'] == before['prediction']
    assert detail['history'] == before['history'] and detail['test_history'][0]['is_test_price']
    assert detail['test_history'][0]['source'] == 'admin-test'
    wish = client.get('/api/wishlist', headers=headers).json()[0]
    assert wish['discounted_price'] == 10000 and wish['is_test_price'] and wish['is_target_reached']
    with SessionLocal() as db:
        assert [(p.id, p.discounted_price, p.is_current, p.collected_at) for p in db.query(PriceSnapshot)] == rows
        notifications = db.query(Notification).all()
        assert len(notifications) == 1 and notifications[0].type == 'TEST_TARGET_PRICE'
        assert notifications[0].user.email == 'admin@test.local'
        assert '(테스트용)' in notifications[0].title and '실제 판매 가격' in notifications[0].message
    assert 'admin@test.local' not in str(detail) and 'user@test.local' not in str(detail)


def test_specified_test_account_and_opt_in_email_are_marked_and_other_watchers_are_untouched(client, monkeypatch):
    game_id = add_watchers()
    sent = []
    monkeypatch.setattr(collection, 'send_target_price_email', lambda **kwargs: sent.append(kwargs))
    response = create_test(client, admin_headers(client), game_id, recipient_email='user@test.local', send_email=True)
    assert response.status_code == 201 and response.json()['notifications_created'] == response.json()['emails_sent'] == 1
    assert len(sent) == 1 and sent[0]['to'] == 'user@test.local' and sent[0]['is_test']
    with SessionLocal() as db:
        assert db.query(Notification).one().user.email == 'user@test.local'


def test_test_email_subject_and_body_make_simulation_explicit(monkeypatch):
    sent = []
    monkeypatch.setattr(email_service, 'send_email', lambda **kwargs: sent.append(kwargs))
    email_service.send_target_price_email(to='test@example.test', nickname='Tester', game_title='Baba Is You',
        platform_name='Steam', discounted_price=10000, target_price=12000, is_test=True)
    assert '(테스트용)' in sent[0]['subject'] and '실제 할인이나 판매 가격이 아닙니다' in sent[0]['html']


def test_restoring_test_uses_latest_real_price_and_does_not_consume_real_notification_budget(client, monkeypatch):
    game_id = add_watchers()
    headers = admin_headers(client)
    created = create_test(client, headers, game_id).json()
    monkeypatch.setattr(collection, 'send_target_price_email', lambda **kwargs: None)
    with SessionLocal() as db:
        result = collection.update_game_price_and_notify(db, game_id, 'Steam', 9000, 50000, source='steam')
        db.commit()
        assert result['notifications_created'] == 2
    assert client.get(f'/api/games/{game_id}').json()['discounted_price'] == 10000
    assert client.delete(f"/api/admin/price-tests/{created['test']['id']}", headers=headers).status_code == 200
    detail = client.get(f'/api/games/{game_id}').json()
    assert detail['discounted_price'] == 9000 and not detail['is_test_price']
    assert not detail['active_price_tests'] and not detail['test_history'][0]['is_active']
    assert detail['stats']['lowest_price'] == 9000
    with SessionLocal() as db:
        assert db.query(Notification).filter_by(type='TARGET_PRICE').count() == 2
        assert db.query(Notification).filter_by(type='TEST_TARGET_PRICE').count() == 1


def test_expired_test_falls_back_without_waiting_for_a_worker(client):
    game_id = add_watchers()
    headers = admin_headers(client)
    created = create_test(client, headers, game_id).json()
    with SessionLocal() as db:
        db.get(GamePriceTest, created['test']['id']).expires_at = utc_now() - timedelta(seconds=1)
        db.commit()
    detail = client.get(f'/api/games/{game_id}').json()
    assert detail['discounted_price'] == 40000 and not detail['is_test_price']
    assert not detail['active_price_tests'] and len(detail['test_history']) == 1
    assert not client.get('/api/admin/price-tests', headers=headers).json()['items'][0]['active']


@pytest.mark.parametrize('field,value', [('discounted_price', -1), ('discounted_price', 1.5),
    ('discounted_price', True), ('discounted_price', 2147483648), ('duration_minutes', 121)])
def test_invalid_simulation_is_rejected_without_writes(client, field, value):
    response = create_test(client, admin_headers(client), 1, **{field: value})
    assert response.status_code == 422
    with SessionLocal() as db:
        assert db.query(GamePriceTest).count() == 0


def test_simulation_is_admin_only_but_available_in_production(client, monkeypatch):
    import app.main as main
    headers = admin_headers(client)
    client.cookies.clear()  # 다른 계정 로그인으로 앞 세션을 종료하지 않도록 분리합니다.
    user = auth_header(login(client, 'user@test.local', 'user-pass-1234'))
    monkeypatch.setattr(main, 'PRODUCTION', True)
    assert client.get('/api/admin/price-tests', headers=user).status_code == 403
    assert create_test(client, user, 1).status_code == 403
    assert client.delete('/api/admin/price-tests/1', headers=user).status_code == 403
    assert create_test(client, headers, 1).status_code == 201


def test_replacing_same_store_test_keeps_one_active_and_preserves_marked_history(client):
    game_id = add_watchers()
    headers = admin_headers(client)
    create_test(client, headers, game_id)
    create_test(client, headers, game_id, discounted_price=0)
    detail = client.get(f'/api/games/{game_id}').json()
    assert detail['discounted_price'] == 0 and len(detail['active_price_tests']) == 1
    assert len(detail['test_history']) == 2 and sum(row['is_active'] for row in detail['test_history']) == 1
    assert detail['stats']['lowest_price'] == 40000


def test_no_notification_without_saved_and_enabled_target(client):
    with SessionLocal() as db:
        game = db.query(Game).filter_by(title='Test Game').one()
        user = db.query(User).filter_by(email='admin@test.local').one()
        db.add(WishlistItem(user_id=user.id, game_id=game.id, target_price=12000, notify_enabled=False))
        db.commit()
    response = create_test(client, admin_headers(client), 1)
    assert response.status_code == 201 and response.json()['notifications_created'] == 0
    assert '꺼져' in response.json()['notification_reason']


def test_bad_platform_and_recipient_do_not_create_test_records(client):
    headers = admin_headers(client)
    assert create_test(client, headers, 1, platform='Imaginary Store').status_code == 400
    assert create_test(client, headers, 1, recipient_email='missing@test.local').status_code == 400
    assert create_test(client, headers, 1, discounted_price=60000).status_code == 400
    with SessionLocal() as db:
        assert db.query(GamePriceTest).count() == 0
