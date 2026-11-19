from datetime import timedelta
from types import SimpleNamespace
from concurrent.futures import ThreadPoolExecutor

import pytest
from test_api_smoke import auth_header, login
from test_api_smoke import client as client
from test_api_smoke import reset_database as reset_database

from app.database import SessionLocal
from app.models import AutoRefreshState, CollectionSourceState, CrawlingLog, Game, Platform
from app.services import history_sync_service as history
from app.services import itad_collect_service as itad
from app.services import scheduler_service as scheduler
from app.services import source_request_service as source
from app.services import steam_collect_service as steam
from app.services.targeted_game_service import safe_source_error
from app.utils.time import utc_now


def test_cooldown_survives_new_process_gate_and_remains_visible():
    gate = source.SourceRequestGate('ITAD', 1, persistent=True)
    gate.pause(120, detail='Request window exhausted')
    second = source.SourceRequestGate('ITAD', 1, persistent=True)
    with pytest.raises(source.SourceRequestError) as caught:
        second.ensure_available()
    assert caught.value.deferred and caught.value.status_code == 429
    assert 118 <= caught.value.retry_after <= 121
    assert second.interval == 2
    with SessionLocal() as db:
        assert db.get(CollectionSourceState, 'ITAD').http_status == 429
        assert source.source_statuses(db)[0]['status'] == 'WAITING'


def test_concurrent_cooldown_writes_keep_the_later_deadline():
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [pool.submit(source.save_source_pause, 'ITAD', delay, 429, min_interval=2) for delay in [30, 120]]
        for result in results:
            result.result()
    with SessionLocal() as db:
        rows = db.query(CollectionSourceState).all()
        assert len(rows) == 1 and rows[0].paused_until > utc_now() + timedelta(seconds=110)


def test_steam_403_stops_following_requests_without_hiding_reason(monkeypatch):
    monkeypatch.setattr(steam, 'request_gate', source.SourceRequestGate('Steam', 0, persistent=True))
    calls = []
    def request(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(status_code=403, headers={}, raise_for_status=lambda: None)
    monkeypatch.setattr(steam.requests, 'get', request)
    for _ in range(3):
        with pytest.raises(source.SourceRequestError) as error:
            steam.fetch_steam_app_details(620)
        assert error.value.status_code == 403
    assert len(calls) == 1
    with SessionLocal() as db:
        assert db.get(CollectionSourceState, 'Steam').paused_until > utc_now() + timedelta(minutes=14)


def test_bad_history_requests_are_grouped_and_not_retried_every_30_minutes(monkeypatch):
    monkeypatch.setenv('ITAD_API_KEY', 'placeholder')
    def failure(*args, **kwargs):
        raise source.SourceRequestError('ITAD', 400, detail='Invalid request parameter: since')
    monkeypatch.setattr(itad, 'itad_request', failure)
    with SessionLocal() as db:
        games = db.query(Game).all()
        for i, game in enumerate(games):
            game.itad_id = f'history-{i}'
        db.flush()
        history.queue_history_sync(db)
        db.commit()
    assert history.run_next_history_job()['error_count'] == 1
    assert history.run_next_history_job()['error_count'] == 1
    with SessionLocal() as db:
        logs = db.query(CrawlingLog).all()
        assert len(logs) == 1 and logs[0].repeat_count == 2
        assert logs[0].status == 'WARNING' and 'since' in logs[0].message
        assert all(game.history_retry_at > utc_now() + timedelta(hours=23) for game in db.query(Game))
        assert history.queue_history_sync(db)['queued_count'] == 0


def test_repeated_source_waits_are_one_log_with_real_http_status():
    def deferred(db):
        raise source.SourceRequestError('ITAD', 429, 120, deferred=True)
    a = scheduler.run_logged_collection('IsThereAnyDeal', 'current', deferred)
    b = scheduler.run_logged_collection('IsThereAnyDeal', 'current', deferred)
    assert a['status'] == b['status'] == 'WAITING'
    assert a['error_count'] == 0
    with SessionLocal() as db:
        row = db.query(CrawlingLog).one()
        assert row.repeat_count == 2 and 'HTTP 429' in row.message


def test_unexpected_application_error_still_is_failure():
    def failure(db):
        raise RuntimeError('unexpected application problem')
    result = scheduler.run_logged_collection('Steam', 'test', failure)
    assert result['status'] == 'FAIL' and result['error_count'] == 1
    with SessionLocal() as db:
        assert db.query(CrawlingLog).one().status == 'FAIL'


def test_manual_steam_requests_during_pause_do_not_add_failure_logs(client, monkeypatch):
    steam.request_gate.pause(120, 403)
    monkeypatch.setattr(steam.requests, 'get', lambda *args, **kwargs: pytest.fail('No request during cooldown'))
    token = login(client, 'admin@test.local', 'admin-pass-1234')
    for _ in range(2):
        response = client.post('/api/admin/steam/import-featured', json={'max_items': 1}, headers=auth_header(token))
        assert response.status_code == 503 and '403' in response.json()['detail']
    with SessionLocal() as db:
        assert db.query(CrawlingLog).count() == 0


def test_summary_view_preserves_detailed_logs_and_groups_known_source_errors(client):
    with SessionLocal() as db:
        db.add_all([CrawlingLog(status='FAIL', message=f'가격 이력 우선 보강: game-{i} · ITAD HTTP 400') for i in range(4)])
        db.add(CrawlingLog(status='WARNING', message='가격 이력 우선 보강: HTTP 400', group_key='history:400', repeat_count=4))
        db.add(CrawlingLog(status='SUCCESS', message='Steam collection completed'))
        db.add(CrawlingLog(status='FAIL', message='Steam HTTP 403: Forbidden'))
        db.commit()
    token = login(client, 'admin@test.local', 'admin-pass-1234')
    headers = auth_header(token)
    all_logs = client.get('/api/admin/crawling-logs', headers=headers).json()
    summary = client.get('/api/admin/crawling-logs', headers=headers, params={'view': 'summary'}).json()
    issues = client.get('/api/admin/crawling-logs', headers=headers, params={'view': 'summary', 'filter': 'issues'}).json()
    assert all_logs['total'] == 7 and summary['total'] == 3 and issues['total'] == 2
    old_block = next(row for row in summary['items'] if row['status'] == 'FAIL')
    assert old_block['display_status'] == 'BLOCKED'
    assert next(row for row in summary['items'] if row['repeat_count'] == 4)['duration_seconds'] is None
    with SessionLocal() as db:
        assert db.query(CrawlingLog).count() == 7


def test_source_error_body_is_useful_but_redacts_credentials_and_urls():
    response = SimpleNamespace(json=lambda: {'errors': [{'message': 'bad since; key=private-key owner@private.test https://example.test/?key=private-key'}]})
    detail = source.response_error_detail(response, secrets=('private-key',))
    assert 'since' in detail and 'private-key' not in detail and 'https://' not in detail
    assert 'owner@private.test' not in detail
    message = safe_source_error('IsThereAnyDeal', source.SourceRequestError('ITAD', 429, 120))['message']
    assert 'HTTP 429' in message and 'SourceRequestError' not in message


def test_history_waits_for_current_price_refresh(monkeypatch):
    monkeypatch.setenv('ITAD_API_KEY', 'placeholder')
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = 'history-test'
        db.flush()
        history.queue_history_sync(db)
        db.add(AutoRefreshState(id=1, lease_token='running', lease_until=utc_now() + timedelta(minutes=10)))
        db.commit()
    assert history.run_next_history_job() is None
    with SessionLocal() as db:
        assert db.query(CrawlingLog).count() == 0
        assert db.query(Game).filter(Game.history_queued_at.isnot(None)).count() == 1


def test_deferred_refresh_is_not_marked_successful():
    with SessionLocal() as db:
        db.add(AutoRefreshState(id=1, enabled=True, lease_token='token'))
        db.commit()
    scheduler.finish_refresh('token', {'steps': [{'source': 'ITAD', 'status': 'WAITING', 'error_count': 0, 'updated_count': 0}]})
    with SessionLocal() as db:
        assert db.get(AutoRefreshState, 1).last_status == 'WAITING'


def test_auto_refresh_compacts_remaining_steps_when_itad_is_paused(monkeypatch):
    monkeypatch.setenv('ITAD_API_KEY', 'placeholder')
    itad.request_gate.pause(120)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = 'existing-game'
        game.steam_appid = 620
        db.commit()
        scheduler.request_refresh(db)
    def done(db, **kwargs):
        return {'updated_count': 1, 'imported_count': 1, 'error_count': 0}
    monkeypatch.setattr(scheduler, 'collect_steam_featured_games', done)
    monkeypatch.setattr(scheduler, 'collect_existing_steam_prices', done)
    monkeypatch.setattr(scheduler, 'collect_epic_popular_games', done)
    monkeypatch.setattr(scheduler, 'refresh_subscription_offers', done)
    def unexpected(*args, **kwargs):
        pytest.fail('No ITAD network callback may run during a shared pause')
    for name in ['import_itad_deals', 'collect_current_prices_for_existing_itad_games', 'link_catalog_to_itad', 'enrich_itad_catalog_metadata']:
        monkeypatch.setattr(scheduler, name, unexpected)
    result = scheduler.run_auto_collect_once()
    waiting = [row for row in result['steps'] if row['status'] == 'WAITING']
    assert len(waiting) == 1 and waiting[0]['deferred_steps'] >= 2


def test_recent_itad_steam_price_avoids_duplicate_direct_request(monkeypatch):
    from app.models import PriceSnapshot
    from app.services.collection_service import update_game_price_and_notify
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.steam_appid = 620
        db.flush()
        update_game_price_and_notify(db, game_id=game.id, platform_name='Steam',
                                    discounted_price=20000, original_price=40000,
                                    currency='KRW', source='itad-current', collected_at=utc_now())
        db.commit()
        before = db.query(PriceSnapshot).count()
        monkeypatch.setattr(steam, 'fetch_steam_app_details', lambda *args, **kwargs: pytest.fail('Already checked current price'))
        result = steam.collect_existing_steam_prices(db, delay_seconds=0)
        assert result['reused_count'] == 1 and result['updated_count'] == 0
        assert result['status'] == 'SUCCESS' and result['error_count'] == 0
        assert db.query(PriceSnapshot).count() == before


def test_manual_collection_only_queues_history_not_whole_catalog(monkeypatch):
    import app.main as main
    monkeypatch.setenv('APP_ENV', 'development')
    calls = []
    monkeypatch.setattr(main, 'queue_history_sync', lambda db, **kwargs: calls.append(kwargs))
    monkeypatch.setattr(main, 'request_refresh', lambda *args, **kwargs: pytest.fail('Manual collection must not trigger a whole refresh'))
    with SessionLocal() as db:
        main.queue_price_enrichment(db)
    assert calls == [{'include_due': False}]


def test_bulk_manual_collection_does_not_overlap_full_refresh(client, monkeypatch):
    with SessionLocal() as db:
        scheduler.request_refresh(db)
    monkeypatch.setattr(steam.requests, 'get', lambda *args, **kwargs: pytest.fail('No overlapping collection'))
    token = login(client, 'admin@test.local', 'admin-pass-1234')
    response = client.post('/api/admin/steam/import-featured', json={'max_items': 1}, headers=auth_header(token))
    assert response.status_code == 409
    with SessionLocal() as db:
        assert db.query(CrawlingLog).count() == 0
