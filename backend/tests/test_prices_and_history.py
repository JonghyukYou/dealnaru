from datetime import datetime, timedelta

import pytest
from test_api_smoke import auth_header, login
from test_api_smoke import client as client
from test_api_smoke import reset_database as reset_database

from app.database import SessionLocal
from app.models import (
    Game,
    Notification,
    Platform,
    PredictionResult,
    PriceSnapshot,
    Software,
    SoftwarePromotion,
    UsedMarketItem,
    UsedMarketSummary,
    User,
    WishlistItem,
)
from app.services import history_sync_service as history_service
from app.services import itad_collect_service as itad
from app.services.collection_service import update_game_price_and_notify
from app.services.game_price_reset_service import reset_game_prices
from app.services.price_policy_service import (
    cleanup_excessive_discounts,
    excessive_discount_reason,
)
from app.utils.time import utc_now


@pytest.mark.parametrize(
    "name,shop,regular,price,cut,excluded",
    [
        ("Fanatical", 6, 40201, 3003, 92.53, True),
        ("Unknown store", 999, 30000, 2999, 89, True),
        ("Humble Store", 37, 30000, 3000, 90, False),
        ("Humble Store", 37, 30000, 3000, 90.01, True),
        ("Steam", 61, 30000, 300, 99, False),
        ("Epic Game Store", 16, 30000, 0, 100, False),
        ("GOG", 35, 30000, 300, 99, False),
        ("EA Store", 52, 30000, 300, 99, False),
        ("Steam Keys Store", None, 30000, 300, 99, True),
        ("Steam", 999, 30000, 300, 99, True),
    ],
)
def test_discount_policy_boundary_and_official_store_exceptions(
    name, shop, regular, price, cut, excluded
):
    assert bool(excessive_discount_reason(name, shop, regular, price, cut)) is excluded


def test_excessive_deal_is_rejected_before_creating_game(monkeypatch):
    monkeypatch.setattr(
        itad,
        "itad_request",
        lambda *_, **__: {
            "hasMore": False,
            "list": [
                {
                    "id": "blocked",
                    "title": "A normal base game",
                    "type": "game",
                    "deal": {
                        "shop": {"id": 6, "name": "Fanatical"},
                        "cut": 95,
                        "price": {"amount": 1000, "currency": "KRW"},
                        "regular": {"amount": 20000, "currency": "KRW"},
                    },
                }
            ],
        },
    )
    with SessionLocal() as db:
        before = db.query(Game).count()
        result = itad.import_itad_deals(db, limit=1)
        db.commit()
        assert result["imported_count"] == 0 and result["skipped_count"] == 1
        assert "90%" in result["skipped"][0]["reason"]
        assert db.query(Game).count() == before


def test_current_filter_removes_old_disallowed_price_and_keeps_exactly_ninety(
    monkeypatch,
):
    from app.main import format_game_detail

    def request(*_, **__):
        return [
            {
                "id": "linked",
                "deals": [
                    {
                        "shop": {"id": 6, "name": "Fanatical"},
                        "cut": 95,
                        "price": {"amount": 1500, "currency": "KRW"},
                        "regular": {"amount": 30000, "currency": "KRW"},
                    },
                    {
                        "shop": {"id": 61, "name": "Steam"},
                        "cut": 0,
                        "price": {"amount": 30000, "currency": "KRW"},
                        "regular": {"amount": 30000, "currency": "KRW"},
                    },
                    {
                        "shop": {"id": 37, "name": "Humble Store"},
                        "cut": 90,
                        "price": {"amount": 3000, "currency": "KRW"},
                        "regular": {"amount": 30000, "currency": "KRW"},
                    },
                ],
            }
        ]

    monkeypatch.setattr(itad, "itad_request", request)
    with SessionLocal() as db:
        game = db.query(Game).filter(Game.title == "Test Game").one()
        game.itad_id = "linked"
        update_game_price_and_notify(
            db, game.id, "Fanatical", 15000, 30000, itad_shop_id=6
        )
        db.commit()
        result = itad.collect_current_prices_for_existing_itad_games(db)
        db.commit()
        db.expire_all()
        prices = format_game_detail(db.get(Game, game.id))["prices"]
        assert result["updated_count"] == 2 and result["skipped_count"] == 1
        assert {p["platform"] for p in prices} == {"Steam", "Humble Store"}


def test_excluded_history_creates_gap_and_preserves_major_store_sale(monkeypatch):
    from app.main import build_history_response

    def row(month, shop, price, cut):
        return {
            "timestamp": f"2020-{month:02d}-01T00:00:00Z",
            "shop": shop,
            "deal": {
                "price": {"amount": price, "currency": "KRW"},
                "regular": {"amount": 10000, "currency": "KRW"},
                "cut": cut,
            },
        }

    fan = {"id": 6, "name": "Fanatical"}
    rows = [
        row(1, fan, 10000, 0),
        row(2, fan, 500, 95),
        row(3, fan, 5000, 50),
        row(2, {"id": 61, "name": "Steam"}, 500, 95),
    ]
    monkeypatch.setattr(itad, "itad_request", lambda *_, **__: rows)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "history-test"
        db.commit()
        result = itad.collect_history_for_existing_itad_games(db, game_ids=[game.id])
        db.commit()
        db.expire_all()
        game = db.get(Game, game.id)
        fan_history = build_history_response(game, "Fanatical", "all")
        assert result["skipped_count"] == 1
        assert [p["price"] for p in fan_history["history"]] == [10000, None, 5000]
        assert fan_history["stats"]["lowest_price"] == 5000
        assert fan_history["external_history_count"] == 2
        steam = build_history_response(game, "Steam", "all")
        assert steam["stats"]["lowest_price"] == 500


def test_startup_policy_cleanup_only_changes_excessive_price_data():
    with SessionLocal() as db:
        game = db.query(Game).first()
        platform = Platform(name="Fanatical", itad_shop_id=6)
        db.add(platform)
        db.flush()
        db.add_all(
            [
                PriceSnapshot(
                    game_id=game.id,
                    platform_id=platform.id,
                    currency="KRW",
                    original_price=40201,
                    discounted_price=3003,
                    discount_rate=92.53,
                    collected_at=utc_now() - timedelta(days=i),
                    source=source,
                    is_current=i == 0,
                )
                for i, source in enumerate(["itad-current", "itad-history"])
            ]
        )
        db.commit()
        kept = (
            db.query(Game).count(),
            db.query(User).count(),
            db.query(WishlistItem).count(),
        )
        preview = cleanup_excessive_discounts(db)
        assert preview["matched_count"] == 2 and preview["removed_count"] == 0
        assert cleanup_excessive_discounts(db, apply=True)["removed_count"] == 2
        db.commit()
        rows = (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.platform_id == platform.id)
            .all()
        )
        assert len(rows) == 1 and rows[0].is_available is False
        assert rows[0].discounted_price == 0 and rows[0].is_current is False
        assert kept == (
            db.query(Game).count(),
            db.query(User).count(),
            db.query(WishlistItem).count(),
        )


def test_manual_excessive_price_cannot_create_target_alert():
    with SessionLocal() as db:
        game = db.query(Game).first()
        before = db.query(Notification).count()
        with pytest.raises(ValueError, match="90%"):
            update_game_price_and_notify(
                db, game.id, "Fanatical", 3003, 40201, itad_shop_id=6
            )
        db.rollback()
        assert db.query(Notification).count() == before
        assert (
            not db.query(PriceSnapshot)
            .join(Platform)
            .filter(Platform.name == "Fanatical")
            .first()
        )


def test_price_reset_preserves_games_users_wishlist_software_and_used_items():
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "keep-link"
        game.history_synced_at = utc_now()
        game.history_start_at = datetime(2005, 1, 1)
        game.history_queued_at = utc_now()
        game.history_lease_token = "old-work"
        game.history_lease_until = utc_now() + timedelta(minutes=5)
        db.add(PredictionResult(game_id=game.id, expected_date="cached"))
        db.add(
            UsedMarketSummary(
                game_id=game.id,
                digital_lowest=40000,
                digital_discount_rate=20,
                joonggonara_average=23000,
            )
        )
        db.add(
            UsedMarketItem(
                keyword="Test Game",
                title="Physical copy",
                price=20000,
                item_url="https://example.test/used",
                source="중고나라",
            )
        )
        db.commit()
        models = [
            Game,
            User,
            WishlistItem,
            Notification,
            Software,
            SoftwarePromotion,
            UsedMarketItem,
            Platform,
        ]
        before = {m: db.query(m).count() for m in models}
        count = db.query(PriceSnapshot).count()
        preview = reset_game_prices(db)
        assert (
            preview["price_snapshots"] == count
            and db.query(PriceSnapshot).count() == count
        )
        assert db.get(Game, game.id).history_synced_at is not None
        result = reset_game_prices(db, apply=True)
        db.commit()
        db.expire_all()
        assert result["games_kept"] == before[Game]
        assert (
            db.query(PriceSnapshot).count() == 0
            and db.query(PredictionResult).count() == 0
        )
        assert before == {m: db.query(m).count() for m in models}
        game = db.get(Game, game.id)
        assert game.itad_id == "keep-link" and game.history_synced_at is None
        assert game.history_start_at is None and game.history_lease_token is None
        assert (
            game.used_market.digital_lowest == 0
            and game.used_market.joonggonara_average == 23000
        )


def test_previous_partial_history_is_backfilled_from_original_date(monkeypatch):
    calls = []

    def request(*_, params=None, **__):
        calls.append(params)
        return []

    monkeypatch.setattr(itad, "itad_request", request)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "partial"
        game.history_synced_at = utc_now()
        game.history_start_at = utc_now() - timedelta(days=90)
        db.commit()
        itad.collect_history_for_existing_itad_games(db, game_ids=[game.id])
        db.commit()
        assert calls[0]["since"].startswith("2005-")
        assert game.history_start_at == datetime(2005, 1, 1)


def test_viewed_game_queue_is_idempotent_and_prioritized_without_auto_refresh(
    client, monkeypatch
):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")
    with SessionLocal() as db:
        games = db.query(Game).order_by(Game.id).all()
        for i, game in enumerate(games):
            game.itad_id = f"link-{i}"
        db.commit()
        scheduler = __import__(
            "app.services.scheduler_service", fromlist=["get_scheduler_status"]
        )
        assert scheduler.get_scheduler_status(db)["enabled"] is False
        history_service.queue_history_sync(db, include_due=False)
        db.commit()
        gid = games[-1].id
    first = client.post(f"/api/games/{gid}/history/sync")
    second = client.post(f"/api/games/{gid}/history/sync")
    assert first.status_code == second.status_code == 202
    assert second.json()["queued_count"] == 0
    assert client.get(f"/api/games/{gid}/history").json()["history_status"] == "queued"
    with SessionLocal() as db:
        job = history_service.claim_next_history_job(db)
        assert job[0] == gid
        assert (
            client.get(f"/api/games/{gid}/history").json()["history_status"]
            == "running"
        )


def test_history_lease_prevents_duplicate_worker_and_recovers_expired_work(monkeypatch):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "lease-test"
        db.commit()
        history_service.queue_history_sync(db, game.id)
        db.commit()
        first = history_service.claim_next_history_job(db)
        assert history_service.claim_next_history_job(db) is None
        db.expire_all()
        game = db.get(Game, first[0])
        game.history_lease_until = utc_now() - timedelta(seconds=1)
        db.commit()
        second = history_service.claim_next_history_job(db)
        assert second[0] == first[0] and second[1] != first[1]


def test_history_worker_populates_old_dates_then_api_reports_completion(
    client, monkeypatch
):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")
    calls = []

    def request(method, path, params=None, **kwargs):
        assert path == "/games/history/v2"
        calls.append(params)
        return [
            {
                "timestamp": "2015-12-12T00:00:00Z",
                "shop": {"id": 61, "name": "Steam"},
                "deal": {
                    "price": {"amount": 20000, "currency": "KRW"},
                    "regular": {"amount": 50000, "currency": "KRW"},
                    "cut": 60,
                },
            }
        ]

    monkeypatch.setattr(itad, "itad_request", request)
    with SessionLocal() as db:
        game = db.query(Game).filter(Game.title == "Test Game").one()
        game.itad_id = "background"
        db.commit()
        gid = game.id
    client.post(f"/api/games/{gid}/history/sync")
    result = history_service.run_next_history_job()
    assert result["history_rows_imported"] == 1
    response = client.get(f"/api/games/{gid}/history", params={"period": "all"}).json()
    assert (
        response["history_status"] == "synced"
        and response["external_history_count"] == 1
    )
    assert response["history"][0]["date"] == "2015-12-12"
    assert calls[0]["since"].startswith("2005-")
    with SessionLocal() as db:
        game = db.get(Game, gid)
        assert game.history_queued_at is None and game.history_lease_token is None


def test_history_failure_is_visible_and_not_immediately_requeued(client, monkeypatch):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")

    def failure(*_, **__):
        raise RuntimeError("unavailable")

    monkeypatch.setattr(itad, "itad_request", failure)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "failure"
        db.commit()
        gid = game.id
    client.post(f"/api/games/{gid}/history/sync")
    assert history_service.run_next_history_job()["error_count"] == 1
    assert (
        client.get(f"/api/games/{gid}/history").json()["history_status"]
        == "needs_check"
    )
    assert client.post(f"/api/games/{gid}/history/sync").json()["queued_count"] == 0
    with SessionLocal() as db:
        game = db.get(Game, gid)
        assert game.history_synced_at is None and game.history_lease_until is None


def test_price_recollection_operates_on_existing_games_only(monkeypatch):
    from app.game_price_cli import collect_existing_game_prices

    monkeypatch.setattr(itad, "ITAD_API_KEY", "placeholder")

    def request(method, path, params=None, json_body=None):
        if path == "/games/lookup/v1":
            return {"found": False}
        if path == "/games/prices/v3":
            return [
                {
                    "id": "kept",
                    "deals": [
                        {
                            "shop": {"id": 61, "name": "Steam"},
                            "price": {"amount": 50000, "currency": "KRW"},
                            "regular": {"amount": 50000, "currency": "KRW"},
                            "cut": 0,
                        }
                    ],
                }
            ]
        assert path == "/games/history/v2" and params["since"].startswith("2005-")
        return [
            {
                "timestamp": "2020-01-01T00:00:00Z",
                "shop": {"id": 61, "name": "Steam"},
                "deal": {
                    "price": {"amount": 25000, "currency": "KRW"},
                    "regular": {"amount": 50000, "currency": "KRW"},
                    "cut": 50,
                },
            }
        ]

    monkeypatch.setattr(itad, "itad_request", request)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "kept"
        db.commit()
        models = [Game, User, WishlistItem, Notification, Software, SoftwarePromotion]
        before = {m: db.query(m).count() for m in models}
        reset_game_prices(db, apply=True)
        db.commit()
    result = collect_existing_game_prices()
    assert all(step["status"] == "SUCCESS" for step in result["steps"])
    with SessionLocal() as db:
        assert before == {m: db.query(m).count() for m in models}
        assert db.query(PriceSnapshot).count() == 2
        assert (
            db.query(PriceSnapshot).filter(PriceSnapshot.is_current.is_(True)).count()
            == 1
        )


def test_bulk_full_history_queue_requires_admin_and_supports_disabled_schedule(
    client, monkeypatch
):
    monkeypatch.setenv("ITAD_API_KEY", "placeholder")
    assert client.post("/api/admin/history/sync").status_code == 401
    user_token = login(client, "user@test.local", "user-pass-1234")
    assert (
        client.post(
            "/api/admin/history/sync", headers=auth_header(user_token)
        ).status_code
        == 403
    )
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "admin-backfill"
        game.history_synced_at = utc_now()
        game.history_start_at = datetime(2005, 1, 1)
        db.commit()
    token = login(client, "admin@test.local", "admin-pass-1234")
    response = client.post("/api/admin/history/sync", headers=auth_header(token))
    assert response.status_code == 202 and response.json()["queued_count"] == 1
    assert response.json()["status"]["enabled"] is False


def test_missing_history_key_reports_configuration_status_without_fake_queue(
    client, monkeypatch
):
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.setattr(itad, "ITAD_API_KEY", None)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "missing-key"
        db.commit()
        gid = game.id
    response = client.post(f"/api/games/{gid}/history/sync")
    assert response.json()["queued_count"] == 0
    assert (
        client.get(f"/api/games/{gid}/history").json()["history_status"]
        == "unconfigured"
    )
    with SessionLocal() as db:
        assert db.get(Game, gid).history_queued_at is None


def test_simulation_also_rejects_excluded_prices_before_alerts(client):
    with SessionLocal() as db:
        game_id = db.query(Game).first().id
        db.add(Platform(name="Fanatical", itad_shop_id=6))
        db.commit()
        alerts = db.query(Notification).count()
    token = login(client, "admin@test.local", "admin-pass-1234")
    response = client.post(
        f"/api/dev/games/{game_id}/simulate-price",
        headers=auth_header(token),
        json={
            "platform": "Fanatical",
            "original_price": 40201,
            "discounted_price": 3003,
        },
    )
    assert response.status_code == 400 and "90%" in response.json()["detail"]
    with SessionLocal() as db:
        assert db.query(Notification).count() == alerts


def test_home_prioritizes_popular_regular_game_over_extreme_discount(client):
    with SessionLocal() as db:
        steam = db.query(Platform).filter(Platform.name == "Steam").one()
        gog = Platform(name="GOG", itad_shop_id=35)
        normal = Game(
            title="Popular regular game",
            review_count=100000,
            catalog_source="Steam 인기 게임",
        )
        deeply_discounted = Game(title="Small flash sale game", review_count=1)
        db.add_all([gog, normal, deeply_discounted])
        db.flush()
        db.add_all(
            [
                PriceSnapshot(
                    game_id=normal.id,
                    platform_id=steam.id,
                    original_price=30000,
                    discounted_price=30000,
                    discount_rate=0,
                    is_current=True,
                    collected_at=utc_now(),
                ),
                PriceSnapshot(
                    game_id=deeply_discounted.id,
                    platform_id=gog.id,
                    original_price=50000,
                    discounted_price=500,
                    discount_rate=99,
                    is_current=True,
                    collected_at=utc_now(),
                ),
            ]
        )
        db.commit()
    result = client.get("/api/home").json()
    assert result["popular_games"][0]["title"] == "Popular regular game"
    assert result["popular_games"][0]["discount_rate"] == 0
    assert "popular_deals" not in result
    assert result["today_deal"]["title"] == "Test Game"
    assert result["today_deal"]["platform"] == "Steam"
    assert result["random_game"]["id"] != result["today_deal"]["id"]
    # GOG의 실제 큰 할인은 구매처 가격에서 계속 볼 수 있습니다.
    assert any(
        game["title"] == "Small flash sale game" and game["discount_rate"] == 99
        for game in result["popular_games"]
    )
