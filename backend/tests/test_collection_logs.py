from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from types import SimpleNamespace

import pytest
import requests
from test_api_smoke import auth_header, login
from test_api_smoke import client as client
from test_api_smoke import reset_database as reset_database

from app.database import SessionLocal
from app.models import CrawlingLog, Game, PriceSnapshot, User, WishlistItem
from app.services import history_sync_service as history
from app.services import itad_collect_service as itad
from app.services import scheduler_service as scheduler
from app.services import source_request_service as source
from app.services.crawling_log_service import prune_crawling_logs
from app.utils.time import utc_now


def test_log_retention_keeps_boundary_running_and_all_collected_data():
    now = utc_now()
    with SessionLocal() as db:
        models = [Game, PriceSnapshot, User, WishlistItem]
        before = {model: db.query(model).count() for model in models}
        old = CrawlingLog(status="FAIL", start_time=now - timedelta(days=3, seconds=1))
        boundary = CrawlingLog(status="SUCCESS", start_time=now - timedelta(days=3))
        running = CrawlingLog(status="RUNNING", start_time=now - timedelta(days=4))
        recent = CrawlingLog(status="PARTIAL", start_time=now - timedelta(hours=1))
        db.add_all([old, boundary, running, recent])
        db.commit()
        assert prune_crawling_logs(db, now=now) == 1
        db.commit()
        assert {row.status for row in db.query(CrawlingLog)} == {"SUCCESS", "RUNNING", "PARTIAL"}
        assert {model: db.query(model).count() for model in models} == before


def test_log_api_prunes_completed_rows_clamps_pages_and_breaks_timestamp_ties(client):
    now = utc_now()
    with SessionLocal() as db:
        db.add(CrawlingLog(status="FAIL", start_time=now - timedelta(days=4), message="expired"))
        db.add_all([CrawlingLog(status="SUCCESS", start_time=now, message=f"entry-{i}") for i in range(61)])
        db.commit()
    token = login(client, "admin@test.local", "admin-pass-1234")
    first = client.get("/api/admin/crawling-logs", headers=auth_header(token), params={"page_size": 25}).json()
    assert first["retention_days"] == 3
    assert first["total"] == 61 and first["total_pages"] == 3
    assert len(first["items"]) == 25 and first["items"][0]["message"] == "entry-60"
    last = client.get("/api/admin/crawling-logs", headers=auth_header(token), params={"page": 999, "page_size": 25}).json()
    assert last["page"] == 3 and len(last["items"]) == 11
    assert last["items"][-1]["message"] == "entry-0"
    with SessionLocal() as db:
        assert db.query(CrawlingLog).count() == 61


def test_log_retention_requires_admin(client):
    with SessionLocal() as db:
        db.add(CrawlingLog(status="SUCCESS", start_time=utc_now() - timedelta(days=4)))
        db.commit()
    token = login(client, "user@test.local", "user-pass-1234")
    assert client.get("/api/admin/crawling-logs", headers=auth_header(token)).status_code == 403
    with SessionLocal() as db:
        assert db.query(CrawlingLog).count() == 1


def test_request_gate_spaces_requests_and_honors_shared_retry_after(monkeypatch):
    clock = [100.0]
    sleeps = []
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    def sleep(seconds):
        sleeps.append(seconds)
        clock[0] += seconds
    monkeypatch.setattr(source.time, "sleep", sleep)
    gate = source.SourceRequestGate("ITAD", 0.4)
    gate.wait()
    gate.wait()
    assert sleeps == pytest.approx([0.4])
    gate.pause(120)
    assert gate.retry_after() == 120
    with pytest.raises(source.SourceRequestError) as error:
        gate.wait()
    assert error.value.status_code == 429 and error.value.retry_after == 120
    clock[0] += 120
    gate.wait()
    assert gate.retry_after() == 0


@pytest.mark.parametrize("value,expected", [("120", 120), ("0", 1), ("bad", 60), (None, 60), ("NaN", 60)])
def test_retry_after_seconds_handles_missing_and_invalid_headers(value, expected):
    assert source.retry_after_seconds(value) == expected


def test_retry_after_accepts_http_dates():
    header = format_datetime(datetime.now(timezone.utc) + timedelta(seconds=120), usegmt=True)
    assert 118 <= source.retry_after_seconds(header) <= 120


def test_itad_http_status_is_visible_and_cooldown_does_not_make_more_requests(monkeypatch):
    clock = [100.0]
    monkeypatch.setattr(source.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(itad, "request_gate", source.SourceRequestGate("ITAD", 0))
    monkeypatch.setattr(itad, "ITAD_API_KEY", "test-private-api-key")
    calls = []
    def request(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(status_code=429 if len(calls) == 1 else 200,
                               headers={"Retry-After": "120"}, json=lambda: {"found": True})
    monkeypatch.setattr(itad.requests, "request", request)
    with pytest.raises(source.SourceRequestError) as caught:
        itad.itad_request("GET", "/games/history/v2")
    assert caught.value.status_code == 429 and "120" in str(caught.value)
    assert "test-private-api-key" not in str(caught.value)
    with pytest.raises(source.SourceRequestError):
        itad.itad_request("GET", "/games/lookup/v1")
    assert len(calls) == 1
    clock[0] += 120
    assert itad.itad_request("GET", "/games/lookup/v1") == {"found": True}


def test_history_worker_preserves_unclaimed_jobs_during_rate_limit(monkeypatch):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")
    gate = source.SourceRequestGate("ITAD", 0)
    monkeypatch.setattr(itad, "request_gate", gate)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "test-history"
        gid = game.id
        db.flush()
        history.queue_history_sync(db, gid)
        db.commit()
    gate.pause(120)
    assert history.run_next_history_job() is None
    with SessionLocal() as db:
        game = db.get(Game, gid)
        assert game.history_queued_at is not None and game.history_lease_token is None
        assert db.query(CrawlingLog).count() == 0


def test_history_and_auto_refresh_logs_include_actual_http_errors(monkeypatch):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")
    monkeypatch.setattr(itad, "request_gate", source.SourceRequestGate("ITAD", 0))
    def limited(*args, **kwargs):
        raise source.SourceRequestError("ITAD", 429, 120)
    monkeypatch.setattr(itad, "itad_request", limited)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "test-history"
        db.flush()
        history.queue_history_sync(db, game.id)
        db.commit()
    assert history.run_next_history_job()["error_count"] == 1
    with SessionLocal() as db:
        log = db.query(CrawlingLog).one()
        assert "HTTP 429" in log.message and "120" in log.message
    result = scheduler.run_logged_collection("IsThereAnyDeal", "test", limited)
    assert result["status"] == "WAITING" and "HTTP 429" in result["message"]


def test_source_errors_exclude_private_urls_and_request_headers():
    response = requests.Response()
    response.status_code = 403
    error = requests.HTTPError("failed https://example.test/?key=private-secret", response=response)
    assert source.collection_error_detail(error) == "HTTP 403"


def test_auto_refresh_error_summary_omits_urls_and_credentials():
    summary = source.collection_result_error({"error_count": 1, "errors": [{"error": "429 Client Error https://example.test/?key=private-secret token=another-secret"}]})
    assert "429" in summary and "private-secret" not in summary and "another-secret" not in summary
    assert "https://" not in summary
    assert source.collection_result_error({"error_count": 1, "errors": [""]}) == "응답 확인 필요"
