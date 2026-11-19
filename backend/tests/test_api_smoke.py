from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text

import app.main as main_module
import app.services.itad_collect_service as itad_collect_service
import app.services.scheduler_service as scheduler_service
import app.services.steam_collect_service as steam_collect_service
import app.services.subscription_service as subscription_service
from app.database import Base, SessionLocal, engine
from conftest import TEST_DB_PATH
from app.main import app
from app.models import (
    AutoRefreshState,
    CrawlingLog,
    Game,
    Notification,
    Platform,
    PriceSnapshot,
    Software,
    SoftwarePromotion,
    UsedMarketItem,
    User,
    WishlistItem,
)
from app.seed import main as seed_demo_data
from app.services.auth_service import hash_password
from app.services.content_filter_service import (
    has_explicit_sexual_content,
    is_explicit_adult_itad_item,
)
from app.services.price_freshness_service import deactivate_stale_current_prices
from app.services.schema_service import upgrade_subscription_schema
from app.services.software_demo_service import upsert_demo_software
from app.services.subscription_service import (
    format_subscription_promotion,
    parse_official_offer,
    promotion_status,
    refresh_subscription_offers,
    seed_subscription_catalog,
)
from app.services.used_market_collect_service import delete_stale_used_market_items
from app.utils.time import utc_now


@pytest.fixture(autouse=True)
def reset_database(monkeypatch):
    from app.services.source_request_service import SourceRequestGate
    monkeypatch.setattr(itad_collect_service, "request_gate", SourceRequestGate("ITAD", 0))
    monkeypatch.setattr(steam_collect_service, "request_gate", SourceRequestGate("Steam", 0))
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        admin = User(
            email="admin@test.local",
            nickname="테스트관리자",
            password_hash=hash_password("admin-pass-1234"),
            is_admin=True,
            email_verified=True,
            email_verified_at=utc_now(),
        )
        user = User(
            email="user@test.local",
            nickname="테스트사용자",
            password_hash=hash_password("user-pass-1234"),
            is_admin=False,
            email_verified=True,
            email_verified_at=utc_now(),
        )
        platform = Platform(name="Steam", website="https://store.steampowered.com")
        game = Game(title="Test Game", genre="RPG", review_count=100)
        game_without_price = Game(title="No Price Game", genre="인디", review_count=10)
        software = Software(
            name="Test Tool",
            category="개발",
            developer="GameSoft",
            description="테스트용 소프트웨어",
        )

        db.add_all([admin, user, platform, game, game_without_price, software])
        db.flush()

        db.add(
            PriceSnapshot(
                game_id=game.id,
                platform_id=platform.id,
                original_price=50000,
                discounted_price=40000,
                discount_rate=20,
                collected_at=utc_now(),
                is_current=True,
            )
        )
        db.add(
            SoftwarePromotion(
                software_id=software.id,
                title="테스트 할인",
                platform="공식 사이트",
                promo_type="기간 할인",
                original_price=10000,
                discounted_price=5000,
                discount_rate=50,
                is_current=True,
            )
        )

        priced_item = WishlistItem(
            user_id=user.id,
            game_id=game.id,
            target_price=35000,
            notify_enabled=True,
        )
        no_price_item = WishlistItem(
            user_id=user.id,
            game_id=game_without_price.id,
            target_price=10000,
            notify_enabled=True,
        )
        db.add_all([priced_item, no_price_item])
        db.flush()

        db.add(
            Notification(
                user_id=user.id,
                wishlist_item_id=priced_item.id,
                title="테스트 알림",
                message="읽음 처리 테스트",
                is_read=False,
            )
        )
        db.commit()
    finally:
        db.close()

    yield

    Base.metadata.drop_all(bind=engine)
    engine.dispose()
    if TEST_DB_PATH is not None:
        TEST_DB_PATH.unlink(missing_ok=True)


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


def login(client: TestClient, email: str, password: str) -> str:
    response = client.post(
        "/api/auth/login",
        json={"email": email, "password": password},
    )
    assert response.status_code == 200
    return response.json()["access_token"]


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_health_and_software_beta(client):
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json()["database"] == "connected"

    software = client.get("/api/software")
    assert software.status_code == 200
    assert software.json()[0]["name"] == "Test Tool"


def test_notification_read_all_uses_static_route(client):
    token = login(client, "user@test.local", "user-pass-1234")
    response = client.patch("/api/notifications/read-all", headers=auth_header(token))

    assert response.status_code == 200
    assert (
        client.get(
            "/api/notifications/unread-count",
            headers=auth_header(token),
        ).json()["count"]
        == 0
    )


def test_price_simulation_requires_admin(client):
    payload = {
        "platform": "Steam",
        "discounted_price": 30000,
        "original_price": 50000,
    }
    game_id = client.get("/api/games", params={"keyword": "Test Game"}).json()[0]["id"]

    assert (
        client.post(
            f"/api/dev/games/{game_id}/simulate-price", json=payload
        ).status_code
        == 401
    )

    user_token = login(client, "user@test.local", "user-pass-1234")
    assert (
        client.post(
            f"/api/dev/games/{game_id}/simulate-price",
            json=payload,
            headers=auth_header(user_token),
        ).status_code
        == 403
    )

    admin_token = login(client, "admin@test.local", "admin-pass-1234")
    response = client.post(
        f"/api/dev/games/{game_id}/simulate-price",
        json=payload,
        headers=auth_header(admin_token),
    )
    assert response.status_code == 200
    assert response.json()["notifications_created"] == 1


def test_admin_uses_lightweight_game_options_and_has_no_scheduler_routes(client):
    admin_token = login(client, "admin@test.local", "admin-pass-1234")
    headers = auth_header(admin_token)

    response = client.get("/api/admin/game-options", headers=headers)

    assert response.status_code == 200
    assert response.json()
    assert all(set(item) == {"id", "title"} for item in response.json())
    assert client.get("/api/admin/scheduler/status", headers=headers).status_code == 404


def test_wishlist_does_not_treat_missing_price_as_target_reached(client):
    token = login(client, "user@test.local", "user-pass-1234")
    items = client.get("/api/wishlist", headers=auth_header(token)).json()
    no_price_item = next(item for item in items if item["title"] == "No Price Game")

    assert no_price_item["discounted_price"] is None
    assert no_price_item["has_price"] is False
    assert no_price_item["is_target_reached"] is False


def test_seed_without_reset_keeps_existing_data(capsys):
    db = SessionLocal()
    try:
        before = db.query(User).count()
    finally:
        db.close()

    seed_demo_data()

    db = SessionLocal()
    try:
        assert db.query(User).count() == before
    finally:
        db.close()

    assert "실행하지 않았습니다" in capsys.readouterr().out


def test_software_demo_seed_is_non_destructive_and_idempotent():
    db = SessionLocal()

    try:
        user_count = db.query(User).count()
        game_count = db.query(Game).count()

        first_result = upsert_demo_software(db)
        software_count_after_first_run = db.query(Software).count()
        promotion_count_after_first_run = db.query(SoftwarePromotion).count()

        second_result = upsert_demo_software(db)

        assert first_result["software_created"] == 5
        assert first_result["promotions_created"] == 5
        assert second_result["software_created"] == 0
        assert second_result["promotions_created"] == 0
        assert db.query(Software).count() == software_count_after_first_run
        assert db.query(SoftwarePromotion).count() == promotion_count_after_first_run
        assert db.query(User).count() == user_count
        assert db.query(Game).count() == game_count
    finally:
        db.close()


def test_home_excludes_stale_and_expired_deals(client):
    db = SessionLocal()

    try:
        platform = db.query(Platform).filter(Platform.name == "Steam").first()
        stale_game = Game(title="Detroit: Become Human", genre="어드벤쳐")
        expired_game = Game(title="Expired Deal", genre="액션")
        db.add_all([stale_game, expired_game])
        db.flush()

        db.add_all(
            [
                PriceSnapshot(
                    game_id=stale_game.id,
                    platform_id=platform.id,
                    original_price=50000,
                    discounted_price=10000,
                    discount_rate=80,
                    collected_at=utc_now() - timedelta(days=30),
                    is_current=True,
                ),
                PriceSnapshot(
                    game_id=expired_game.id,
                    platform_id=platform.id,
                    original_price=50000,
                    discounted_price=5000,
                    discount_rate=90,
                    collected_at=utc_now(),
                    sale_end_at=utc_now() - timedelta(hours=1),
                    is_current=True,
                ),
            ]
        )
        db.commit()
    finally:
        db.close()

    home_data = client.get("/api/home").json()

    assert home_data["popular_games"][0]["title"] == "Test Game"
    assert {
        game["title"] for game in home_data["popular_games"] if game["has_price"]
    }.isdisjoint({"Detroit: Become Human", "Expired Deal"})


def test_stale_price_cleanup_keeps_history_row():
    db = SessionLocal()

    try:
        platform = db.query(Platform).filter(Platform.name == "Steam").first()
        game = Game(title="Old Discount", genre="액션")
        db.add(game)
        db.flush()

        stale_price = PriceSnapshot(
            game_id=game.id,
            platform_id=platform.id,
            original_price=50000,
            discounted_price=10000,
            discount_rate=80,
            collected_at=utc_now() - timedelta(days=30),
            is_current=True,
        )
        db.add(stale_price)
        db.commit()
        stale_price_id = stale_price.id
        row_count_before = db.query(PriceSnapshot).count()

        deactivated = deactivate_stale_current_prices(
            db,
            max_age_hours=7 * 24,
            commit=True,
        )

        stored_price = db.query(PriceSnapshot).filter_by(id=stale_price_id).one()
        assert stale_price_id in {price.id for price in deactivated}
        assert stored_price.is_current is False
        assert db.query(PriceSnapshot).count() == row_count_before
    finally:
        db.close()


def test_known_future_sale_end_overrides_seven_day_fallback():
    db = SessionLocal()

    try:
        platform = db.query(Platform).filter(Platform.name == "Steam").first()
        game = Game(title="Month-long Sale", genre="전략")
        db.add(game)
        db.flush()

        active_long_sale = PriceSnapshot(
            game_id=game.id,
            platform_id=platform.id,
            original_price=50000,
            discounted_price=25000,
            discount_rate=50,
            collected_at=utc_now() - timedelta(days=10),
            sale_end_at=utc_now() + timedelta(days=20),
            is_current=True,
        )
        db.add(active_long_sale)
        db.commit()

        deactivated = deactivate_stale_current_prices(
            db,
            max_age_hours=7 * 24,
            commit=True,
        )

        db.refresh(active_long_sale)
        assert active_long_sale.id not in {price.id for price in deactivated}
        assert active_long_sale.is_current is True
    finally:
        db.close()


def test_used_market_cleanup_deletes_only_items_older_than_14_days():
    db = SessionLocal()

    try:
        old_item = UsedMarketItem(
            source="중고나라",
            keyword="PS5",
            title="오래된 게임 타이틀",
            price=20000,
            item_url="https://example.com/old",
            collected_at=utc_now() - timedelta(days=90),
        )
        recent_item = UsedMarketItem(
            source="중고나라",
            keyword="PS5",
            title="최근 게임 타이틀",
            price=30000,
            item_url="https://example.com/recent",
            collected_at=utc_now() - timedelta(days=7),
        )
        db.add_all([old_item, recent_item])
        db.commit()

        deleted_count = delete_stale_used_market_items(
            db,
            max_age_days=14,
            commit=True,
        )

        remaining_titles = {item.title for item in db.query(UsedMarketItem).all()}
        assert deleted_count == 1
        assert remaining_titles == {"최근 게임 타이틀"}
    finally:
        db.close()


@pytest.mark.parametrize(
    ("shop_id", "shop_name"),
    [(35, "GOG"), (6, "Fanatical")],
)
def test_itad_shop_filter_imports_without_per_game_request(
    monkeypatch,
    shop_id,
    shop_name,
):
    db = SessionLocal()
    sale_end_at = (utc_now() + timedelta(days=14)).replace(microsecond=0)
    captured_params = {}

    def fake_itad_request(method, path, params=None, json_body=None):
        assert method == "GET"
        assert path == "/deals/v2"
        assert json_body is None
        captured_params.update(params or {})
        return {
            "list": [
                {
                    "id": f"{shop_name.lower()}-test-game-id",
                    "slug": f"{shop_name.lower()}-test-game",
                    "title": f"{shop_name} Test Game",
                    "type": "game",
                    "assets": {},
                    "deal": {
                        "shop": {"id": shop_id, "name": shop_name},
                        "price": {"amount": 12000, "currency": "KRW"},
                        "regular": {"amount": 30000, "currency": "KRW"},
                        "url": "https://example.com/game/test",
                        "timestamp": utc_now().isoformat() + "Z",
                        "expiry": sale_end_at.isoformat() + "Z",
                    },
                }
            ],
            "hasMore": False,
        }

    monkeypatch.setattr(itad_collect_service, "itad_request", fake_itad_request)
    monkeypatch.setattr(
        itad_collect_service,
        "fetch_itad_game_info",
        lambda _itad_id: pytest.fail(
            "Deals 수집에서 게임별 상세 API를 호출하면 안 됩니다."
        ),
    )

    try:
        result = itad_collect_service.import_itad_deals(
            db,
            limit=10,
            country="KR",
            shop_ids=[shop_id],
        )
        db.commit()

        platform = db.query(Platform).filter(Platform.name == shop_name).one()
        price = (
            db.query(PriceSnapshot)
            .filter(
                PriceSnapshot.platform_id == platform.id,
                PriceSnapshot.is_current.is_(True),
            )
            .one()
        )

        assert captured_params["shops"] == str(shop_id)
        assert result["shop_ids"] == [shop_id]
        assert result["imported_count"] == 1
        assert result["error_count"] == 0
        assert platform.itad_shop_id == shop_id
        assert price.discounted_price == 12000
        assert price.sale_end_at == sale_end_at
    finally:
        db.close()


def test_itad_shop_collection_log_uses_selected_store(client, monkeypatch):
    monkeypatch.setattr(
        main_module,
        "import_itad_deals",
        lambda **_kwargs: {
            "imported_count": 2,
            "skipped_count": 0,
            "error_count": 0,
        },
    )
    admin_token = login(client, "admin@test.local", "admin-pass-1234")

    response = client.post(
        "/api/admin/itad/import-deals",
        json={"limit": 2, "country": "KR", "shop_ids": [35]},
        headers=auth_header(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["log"]["platform"] == "GOG"

    db = SessionLocal()
    try:
        stored_log = db.query(CrawlingLog).one()
        assert stored_log.platform is not None
        assert stored_log.platform.name == "GOG"
    finally:
        db.close()


def test_existing_itad_shop_log_recovers_store_name(client):
    db = SessionLocal()
    try:
        db.add(
            CrawlingLog(
                platform_id=None,
                status="SUCCESS",
                start_time=utc_now(),
                end_time=utc_now(),
                rows_count=1,
                message="Fanatical 할인 게임 1개 수집 완료, 스킵 0개, 오류 0개",
            )
        )
        db.commit()
    finally:
        db.close()

    admin_token = login(client, "admin@test.local", "admin-pass-1234")
    response = client.get(
        "/api/admin/crawling-logs",
        headers=auth_header(admin_token),
    )

    assert response.status_code == 200
    assert response.json()["items"][0]["platform"] == "Fanatical"


def test_explicit_content_filter_keeps_violent_games():
    assert has_explicit_sexual_content("Adult Only Sexual Content") is True
    assert has_explicit_sexual_content(["Hentai", "Nudity"]) is True
    assert has_explicit_sexual_content("19금 성인용 게임") is False
    assert has_explicit_sexual_content("18+ Mature Nudity Sexual Themes") is False
    assert has_explicit_sexual_content("Sexual Content") is False
    assert has_explicit_sexual_content("No explicit sexual content") is False
    assert has_explicit_sexual_content("포르노 야겜") is True

    assert has_explicit_sexual_content("Blood and Gore") is False
    assert has_explicit_sexual_content("Extreme Violence", "Horror") is False
    assert has_explicit_sexual_content("Mature fantasy action RPG") is False


def test_steam_collection_rejects_explicit_but_keeps_gore(monkeypatch):
    class FakeResponse:
        def __init__(self, details):
            self.details = details

        def raise_for_status(self):
            return None

        def json(self):
            return {"100": {"success": True, "data": self.details}}

    base_details = {
        "type": "game",
        "is_free": False,
        "name": "Presentation Test Game",
        "genres": [{"description": "Action"}],
        "price_overview": {
            "initial": 3000000,
            "final": 1500000,
            "discount_percent": 50,
        },
    }

    adult_details = {
        **base_details,
        "content_descriptors": {"notes": "Adult Only Sexual Content"},
    }
    monkeypatch.setattr(
        steam_collect_service.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(adult_details),
    )
    assert steam_collect_service.fetch_steam_app_details(100) is None

    violent_details = {
        **base_details,
        "content_descriptors": {"notes": "Blood, Gore and Extreme Violence"},
    }
    monkeypatch.setattr(
        steam_collect_service.requests,
        "get",
        lambda *_args, **_kwargs: FakeResponse(violent_details),
    )
    assert steam_collect_service.fetch_steam_app_details(100) is not None


def test_itad_collection_rejects_explicit_but_keeps_gore(monkeypatch):
    def build_item(item_id, title, tags, mature=False):
        return {
            "id": item_id,
            "slug": item_id,
            "title": title,
            "type": "game",
            "mature": mature,
            "tags": tags,
            "assets": {},
            "deal": {
                "shop": {"id": 35, "name": "GOG"},
                "price": {"amount": 10000, "currency": "KRW"},
                "regular": {"amount": 20000, "currency": "KRW"},
                "url": f"https://example.com/{item_id}",
                "timestamp": utc_now().isoformat() + "Z",
            },
        }

    monkeypatch.setattr(
        itad_collect_service,
        "itad_request",
        lambda *_args, **_kwargs: {
            "list": [
                build_item(
                    "explicit-test-game",
                    "Explicit Test Game",
                    ["Adult Only", "Sexual Content"],
                ),
                build_item(
                    "violent-test-game",
                    "Violent Test Game",
                    ["Blood", "Gore", "Extreme Violence"],
                ),
            ],
            "hasMore": False,
        },
    )

    db = SessionLocal()
    try:
        result = itad_collect_service.import_itad_deals(
            db,
            limit=2,
            country="KR",
            shop_ids=[35],
        )
        db.commit()

        stored_titles = {game.title for game in db.query(Game).all()}
        assert result["imported_count"] == 1
        assert result["skipped_count"] == 1
        assert "Violent Test Game" in stored_titles
        assert "Explicit Test Game" not in stored_titles
        assert result["skipped"][0]["reason"] == "노골적인 성인 콘텐츠"
    finally:
        db.close()


def test_mature_flag_does_not_exclude_normal_adult_rated_games():
    assert not is_explicit_adult_itad_item(
        {"title": "Mature Action RPG", "mature": True, "tags": ["Nudity", "Violence"]}
    )
    assert is_explicit_adult_itad_item({"title": "Hentai Game", "mature": True})
    assert has_explicit_sexual_content("<b>Adult Only</b> Sexual Content")


def test_steam_collection_fills_target_after_rejections(monkeypatch):
    monkeypatch.setattr(
        steam_collect_service, "fetch_steam_featured_appids", lambda **_: [10, 20]
    )
    monkeypatch.setattr(
        steam_collect_service, "fetch_steam_search_appids", lambda *_, **__: [30, 40]
    )

    def fetch(appid, *_, **__):
        if appid == 10:
            return {"_skip_reason": "노골적인 성인 콘텐츠"}
        return {
            "steam_appid": appid,
            "title": f"Paid Game {appid}",
            "genre": "액션",
            "tags": [],
            "developer": None,
            "publisher": None,
            "release_date": None,
            "description": None,
            "header_image": None,
            "store_url": f"https://store.steampowered.com/app/{appid}",
            "original_price": 20000,
            "discounted_price": 10000,
            "discount_rate": 50,
        }

    monkeypatch.setattr(steam_collect_service, "fetch_steam_app_details", fetch)
    with SessionLocal() as db:
        result = steam_collect_service.collect_steam_featured_games(
            db, max_items=2, delay_seconds=0
        )
        db.commit()
        assert result["imported_count"] == 2
        assert result["scanned_count"] == 3
        assert result["skip_reasons"] == {"노골적인 성인 콘텐츠": 1}
        assert db.query(Game).filter(Game.steam_appid == 30).one()
        assert db.query(Game).filter(Game.steam_appid == 10).first() is None


def test_itad_paginates_without_per_game_details_or_empty_games(monkeypatch):
    calls = []

    def item(game_id, price=10000):
        return {
            "id": game_id,
            "title": f"Game {game_id}",
            "type": "game",
            "mature": True,
            "assets": {},
            "deal": {
                "shop": {"id": 35, "name": "GOG"},
                "price": {"amount": price, "currency": "KRW"},
                "regular": {"amount": 20000},
            },
        }

    def request(method, path, params=None, **_):
        assert path == "/deals/v2"
        assert params["mature"] is True
        calls.append(params["offset"])
        return {
            "list": [item("zero", 0), item("good-one")]
            if params["offset"] == 0
            else [item("good-two")],
            "hasMore": params["offset"] == 0,
            "nextOffset": 2,
        }

    monkeypatch.setattr(itad_collect_service, "itad_request", request)
    monkeypatch.setattr(
        itad_collect_service,
        "fetch_itad_game_info",
        lambda *_: pytest.fail("per-game request"),
    )
    with SessionLocal() as db:
        result = itad_collect_service.import_itad_deals(db, limit=2, shop_ids=[35])
        db.commit()
        assert result["imported_count"] == 2
        assert result["scanned_count"] == 3
        assert calls == [0, 2]
        assert db.query(Game).filter(Game.itad_id == "zero").first() is None


def test_subscription_seed_keeps_accounts_and_real_promotions():
    with SessionLocal() as db:
        before_users = db.query(User).count()
        before_games = db.query(Game).count()
        seed_subscription_catalog(db)
        seed_subscription_catalog(db)
        assert db.query(Software).filter(Software.kind == "ai").count() == 9
        assert (
            db.query(SoftwarePromotion)
            .filter(SoftwarePromotion.source_key.isnot(None))
            .count()
            == 8
        )
        assert db.query(User).count() == before_users
        assert db.query(Game).count() == before_games
        assert (
            db.query(SoftwarePromotion)
            .filter(SoftwarePromotion.title == "테스트 할인")
            .one()
            .is_current
        )
        unknown = (
            db.query(SoftwarePromotion)
            .filter(SoftwarePromotion.source_key == "chatgpt-plus-partner")
            .one()
        )
        assert unknown.discounted_price is None


def test_official_catalog_retires_only_marked_demo_promotions():
    with SessionLocal() as db:
        software = Software(name="Adobe Creative Cloud")
        db.add(software)
        db.flush()
        demo = SoftwarePromotion(
            software_id=software.id,
            title="old demo",
            url="#",
            description="첫해 학생 할인 예시 프로모션입니다.",
            is_current=True,
        )
        actual = SoftwarePromotion(
            software_id=software.id,
            title="actual",
            url="https://www.adobe.com/kr/",
            description="실제 등록 정보",
            is_current=True,
        )
        db.add_all([demo, actual])
        db.commit()
        seed_subscription_catalog(db)
        db.refresh(demo)
        db.refresh(actual)
        assert not demo.is_current and demo.source_status == "demo"
        assert actual.is_current


def test_subscription_filters_and_conditions(client):
    with SessionLocal() as db:
        seed_subscription_catalog(db)
    cards = client.get("/api/software?kind=ai").json()
    assert {c["name"] for c in cards} == {
        "ChatGPT",
        "Gemini · Google AI",
        "Claude",
        "Perplexity",
        "Cursor",
        "GitHub Copilot",
        "Midjourney", "ElevenLabs", "Descript",
    }
    claude = next(card for card in cards if card["name"] == "Claude")
    detail = client.get(f"/api/software/{claude['id']}").json()
    promotion = detail["promotions"][0]
    assert promotion["currency"] == "USD"
    assert promotion["billing_period"] == "연간"
    assert (
        promotion["eligibility"]
        and promotion["renewal_notice"]
        and promotion["url"].startswith("https://")
    )
    assert client.get("/api/software?kind=invalid").status_code == 422


def test_offer_money_uses_same_period_and_does_not_guess_prices():
    fields = parse_official_offer(
        "claude",
        "Pro and Max navigation. Pro For everyday work $17 Per month with annual subscription discount ( $200 billed up front). $20 if billed monthly. Try Claude Everything in Free",
    )
    assert fields["original_price"] == 240
    assert fields["discounted_price"] == 200
    assert float(fields["discount_rate"]) == 16.67
    info = parse_official_offer(
        "chatgpt", "Plus promotions: eligible new subscribers obtain a partner code"
    )
    assert info["discounted_price"] is None and not info["is_offer"]
    with pytest.raises(ValueError):
        parse_official_offer(
            "claude", "Pro pricing changed. Contact sales for annual plans."
        )


def test_adobe_student_price_is_separate_from_general_sale():
    fields = parse_official_offer(
        "adobe",
        "Creative Cloud 학생 요금. 일반 할인 정가 ₩78,100/월 할인 ₩58,200/월. 학생과 교사에게는 첫해 할인. 정가 ₩78,100/월 또는 ₩26,400/월. 첫해 결제 금액은 ₩26,400/월이며 그 이후에는 ₩47,300/월입니다.",
    )
    assert fields["discounted_price"] == 26400
    assert fields["original_price"] == 78100
    assert "47,300" in fields["renewal_notice"]


def test_fresh_itad_observation_does_not_use_old_deal_event_time(monkeypatch):
    monkeypatch.setattr(
        itad_collect_service,
        "itad_request",
        lambda *_, **__: {
            "list": [
                {
                    "id": "old-event",
                    "title": "Still On Sale",
                    "type": "game",
                    "assets": {},
                    "deal": {
                        "shop": {"id": 35, "name": "GOG"},
                        "timestamp": "2020-01-01T00:00:00Z",
                        "price": {"amount": 10000},
                        "regular": {"amount": 20000},
                    },
                }
            ],
            "hasMore": False,
        },
    )
    with SessionLocal() as db:
        result = itad_collect_service.import_itad_deals(db, limit=1, shop_ids=[35])
        db.commit()
        assert result["imported_count"] == 1
        price = (
            db.query(PriceSnapshot).join(Game).filter(Game.itad_id == "old-event").one()
        )
        assert price.collected_at >= utc_now() - timedelta(minutes=1)


def test_itad_missing_deals_keeps_direct_steam_price(monkeypatch):
    with SessionLocal() as db:
        game = db.query(Game).filter(Game.title == "Test Game").one()
        game.itad_id = "linked-test-game"
        game.steam_appid = 1234
        db.commit()
    monkeypatch.setattr(
        itad_collect_service,
        "itad_request",
        lambda *_, **__: [{"id": "linked-test-game", "deals": []}],
    )
    with SessionLocal() as db:
        itad_collect_service.collect_current_prices_for_existing_itad_games(db)
        db.commit()
        assert db.query(PriceSnapshot).one().is_current is True


def test_offer_expiry_staleness_and_unknown_money():
    expired = SoftwarePromotion(
        is_current=True,
        source_key="test-expired",
        source_status="verified",
        checked_at=utc_now(),
        end_date=(utc_now() - timedelta(days=1)).date().isoformat(),
        discounted_price=0,
        is_offer=True,
    )
    assert promotion_status(expired) == "expired"
    assert format_subscription_promotion(expired)["discounted_price"] is None
    stale = SoftwarePromotion(
        is_current=True,
        source_key="test-stale",
        source_status="verified",
        checked_at=utc_now() - timedelta(days=8),
        discounted_price=200,
        is_offer=True,
    )
    assert promotion_status(stale) == "stale"
    assert format_subscription_promotion(stale)["discounted_price"] is None
    unknown = SoftwarePromotion(
        is_current=True,
        source_key="unknown",
        source_status="needs_check",
        discounted_price=0,
        is_offer=True,
    )
    assert format_subscription_promotion(unknown)["discounted_price"] is None


def test_official_failure_preserves_data_but_hides_current_discount(monkeypatch):
    monkeypatch.setattr(
        subscription_service,
        "fetch_official_page",
        lambda *_: (_ for _ in ()).throw(ValueError("blocked")),
    )
    import app.services.software_plan_service as plans_service

    monkeypatch.setattr(
        plans_service,
        "fetch_plan_page",
        lambda *_: (_ for _ in ()).throw(ValueError("blocked")),
    )
    with SessionLocal() as db:
        seed_subscription_catalog(db)
        result = refresh_subscription_offers(db)
        assert result["error_count"] >= 8
        assert result["plans_updated"] == 0
        saved = (
            db.query(SoftwarePromotion)
            .filter(SoftwarePromotion.source_key == "claude-pro-annual")
            .one()
        )
        assert saved.discounted_price == 200
        assert saved.source_status == "needs_check"
        assert format_subscription_promotion(saved)["discounted_price"] is None


def test_schema_upgrade_keeps_legacy_rows_and_can_repeat():
    legacy = create_engine("sqlite:///:memory:")
    with legacy.begin() as connection:
        connection.execute(
            text("CREATE TABLE software (id INTEGER PRIMARY KEY, name TEXT)")
        )
        connection.execute(
            text(
                "CREATE TABLE software_promotions (id INTEGER PRIMARY KEY, title TEXT)"
            )
        )
        connection.execute(text("INSERT INTO software VALUES (4, 'Existing Tool')"))
    upgrade_subscription_schema(legacy)
    upgrade_subscription_schema(legacy)
    with legacy.connect() as connection:
        assert connection.execute(
            text("SELECT name, kind FROM software WHERE id=4")
        ).one() == ("Existing Tool", "software")
        assert "checked_at" in {
            c["name"] for c in inspect(connection).get_columns("software_promotions")
        }
    legacy.dispose()


def test_auto_refresh_authorization_settings_and_duplicate_queue(client):
    assert client.get("/api/admin/auto-refresh").status_code == 401
    user = auth_header(login(client, "user@test.local", "user-pass-1234"))
    assert client.post("/api/admin/auto-refresh/run", headers=user).status_code == 403
    admin = auth_header(login(client, "admin@test.local", "admin-pass-1234"))
    assert (
        client.put(
            "/api/admin/auto-refresh",
            json={"enabled": True, "interval_minutes": 1},
            headers=admin,
        ).status_code
        == 422
    )
    saved = client.put(
        "/api/admin/auto-refresh",
        json={
            "enabled": False,
            "interval_minutes": 60,
            "include_software": True,
            "new_games_per_source": 30,
        },
        headers=admin,
    )
    assert saved.status_code == 200
    assert saved.json()["interval_minutes"] == 60
    assert client.post("/api/admin/auto-refresh/run", headers=admin).status_code == 202
    assert client.post("/api/admin/auto-refresh/run", headers=admin).status_code == 409
    with SessionLocal() as db:
        assert db.get(AutoRefreshState, 1).interval_minutes == 60


def test_auto_refresh_lease_prevents_two_workers_and_recovers_crash():
    now = utc_now()
    with SessionLocal() as first:
        scheduler_service.initialize_state(first)
        scheduler_service.request_refresh(first)
        first_token = scheduler_service.claim_due_refresh(first, now)
    assert first_token
    with SessionLocal() as second:
        assert scheduler_service.claim_due_refresh(second, now) is None
        state = second.get(AutoRefreshState, 1)
        state.enabled = True
        state.next_run_at = now
        state.lease_until = now - timedelta(seconds=1)
        second.commit()
        replacement_token = scheduler_service.claim_due_refresh(second, now)
        assert replacement_token and replacement_token != first_token
    scheduler_service.finish_refresh(first_token, {"steps": []})
    with SessionLocal() as db:
        assert db.get(AutoRefreshState, 1).lease_token == replacement_token
    scheduler_service.finish_refresh(replacement_token, {"steps": []})


def test_auto_refresh_handles_independent_failure_and_keeps_going(monkeypatch):
    monkeypatch.delenv("ITAD_API_KEY", raising=False)
    monkeypatch.setattr(
        scheduler_service,
        "collect_existing_steam_prices",
        lambda *_, **__: {"updated_count": 1},
    )
    monkeypatch.setattr(
        scheduler_service,
        "collect_steam_featured_games",
        lambda *_, **__: (_ for _ in ()).throw(
            RuntimeError("Steam temporarily unavailable")
        ),
    )
    monkeypatch.setattr(
        scheduler_service,
        "refresh_subscription_offers",
        lambda *_: {"updated_count": 2},
    )
    monkeypatch.setattr(
        scheduler_service,
        "collect_epic_popular_games",
        lambda *_, **__: {"updated_count": 1},
    )
    with SessionLocal() as db:
        scheduler_service.request_refresh(db)
    result = scheduler_service.run_auto_collect_once()
    assert any(
        step["source"] == "프로그램·AI 공식 혜택" and step["status"] == "SUCCESS"
        for step in result["steps"]
    )
    with SessionLocal() as db:
        state = db.get(AutoRefreshState, 1)
        assert state.last_status == "PARTIAL"
        assert state.lease_token is None
        assert db.query(CrawlingLog).filter(CrawlingLog.status == "FAIL").count() == 2


@pytest.mark.parametrize(
    "title",
    [
        "AWS Certification Bundle",
        "Business Certification Bundle",
        "Data Certification Bundle",
        "Total Tech & Cybersecurity Masterclass Bundle",
        "Kali Linux Bundle 3rd Edition",
        "Python Programming Bundle",
        "Complete Linux Learning Bundle",
        "Unity Programming Bundle",
    ],
)
def test_screenshot_bundles_blocked_even_with_game_type(title):
    from app.services.catalog_filter_service import non_game_reason

    assert non_game_reason(title, [], "game")
    assert non_game_reason(title, [], None)
    assert non_game_reason("DOOM Eternal", ["Gore"], "game") is None
    assert (
        non_game_reason("Human Resource Machine", ["Education", "Programming"], "game")
        is None
    )


def test_bundle_cleanup_cascades_without_deleting_real_games_or_users():
    from app.models import GameTag, PredictionResult, UsedMarketSummary
    from app.services.catalog_filter_service import cleanup_invalid_catalog

    with SessionLocal() as db:
        before_users = db.query(User).count()
        bad = Game(title="Unity Programming Bundle", itad_id="bad-bundle")
        db.add(bad)
        db.flush()
        bad_id = bad.id
        user = db.query(User).filter(User.is_admin.is_(False)).one()
        item = WishlistItem(user_id=user.id, game_id=bad.id)
        db.add(item)
        db.flush()
        db.add_all(
            [
                GameTag(game_id=bad.id, name="Software"),
                PredictionResult(game_id=bad.id),
                UsedMarketSummary(game_id=bad.id),
                PriceSnapshot(
                    game_id=bad.id,
                    platform_id=1,
                    original_price=400000,
                    discounted_price=8000,
                    discount_rate=98,
                    collected_at=utc_now(),
                    is_current=True,
                ),
                Notification(
                    user_id=user.id,
                    wishlist_item_id=item.id,
                    type="TARGET_PRICE",
                    title="bad",
                    message="bad",
                ),
            ]
        )
        db.commit()
        assert cleanup_invalid_catalog(db)["matched_count"] == 1 and db.get(
            Game, bad_id
        )
        cleanup_invalid_catalog(db, apply=True)
        db.commit()
        assert db.get(Game, bad_id) is None
        for model in (
            GameTag,
            PredictionResult,
            UsedMarketSummary,
            PriceSnapshot,
            WishlistItem,
        ):
            assert db.query(model).filter(model.game_id == bad_id).count() == 0
        assert db.query(Notification).filter(Notification.title == "bad").count() == 0
        assert db.query(User).count() == before_users
        assert db.query(Game).filter(Game.title == "Test Game").one()
        assert cleanup_invalid_catalog(db, apply=True)["removed_count"] == 0


def test_regular_price_catalog_and_missing_price_detail(client):
    with SessionLocal() as db:
        game = db.query(Game).filter(Game.title == "Test Game").one()
        game.title = "The Binding of Isaac: Rebirth"
        for price in game.prices:
            price.discount_rate = 0
            price.discounted_price = price.original_price
        missing = db.query(Game).filter(Game.title == "No Price Game").one()
        missing.steam_appid = 250900
        gid = missing.id
        db.commit()
    home = client.get("/api/home").json()
    assert home["today_deal"] is None
    assert home["random_game"]["has_price"]
    assert any(
        g["title"] == "The Binding of Isaac: Rebirth" for g in home["popular_games"]
    )
    detail = client.get(f"/api/games/{gid}").json()
    assert detail["discounted_price"] is None and detail["prices"] == []
    assert detail["stats"]["lowest_price"] is None
    assert any(g["id"] == gid for g in client.get("/api/games?platform=Steam").json())
    assert all(g["has_price"] for g in client.get("/api/games?max_price=100000").json())


def test_itad_collects_all_stores_regular_prices_and_foreign_cents(monkeypatch):
    with SessionLocal() as db:
        game = db.query(Game).filter(Game.title == "Test Game").one()
        game.itad_id = "multi"
        for item in game.wishlist_items:
            item.notify_enabled = False
        gid = game.id
        db.commit()

    def request(method, path, params=None, **kwargs):
        assert (
            path == "/games/prices/v3"
            and params["deals"] is False
            and params["capacity"] == 0
        )
        return [
            {
                "id": "multi",
                "deals": [
                    {
                        "shop": {"id": 61, "name": "Steam"},
                        "price": {"amount": 20000, "currency": "KRW"},
                        "regular": {"amount": 20000},
                    },
                    {
                        "shop": {"id": 16, "name": "Epic Game Store"},
                        "price": {"amount": 18000, "currency": "KRW"},
                        "regular": {"amount": 20000},
                    },
                    {
                        "shop": {"id": 35, "name": "GOG"},
                        "price": {"amount": 9.99, "currency": "USD"},
                        "regular": {"amount": 19.99},
                    },
                ],
            }
        ]

    monkeypatch.setattr(itad_collect_service, "itad_request", request)
    with SessionLocal() as db:
        result = itad_collect_service.collect_current_prices_for_existing_itad_games(db)
        db.commit()
        assert result["updated_count"] == 3
        detail = main_module.format_game_detail(db.get(Game, gid))
        assert detail["discounted_price"] == 18000 and detail["currency"] == "KRW"
        assert (
            next(p for p in detail["prices"] if p["platform"] == "GOG")[
                "discounted_price"
            ]
            == 9.99
        )
        assert (
            next(p for p in detail["prices"] if p["platform"] == "Steam")[
                "discount_rate"
            ]
            == 0
        )


def test_foreign_price_does_not_trigger_krw_target_notification():
    from app.services.collection_service import update_game_price_and_notify

    with SessionLocal() as db:
        before = db.query(Notification).count()
        result = update_game_price_and_notify(
            db, db.query(Game).first().id, "GOG", 9.99, 19.99, currency="USD"
        )
        db.commit()
        assert (
            result["notifications_created"] == 0
            and db.query(Notification).count() == before
        )


def test_itad_history_retains_timestamps_foreign_cents_and_unavailable_gaps(
    monkeypatch,
):
    rows = [
        {
            "timestamp": "2021-01-01T10:00:00+09:00",
            "shop": {"id": 61, "name": "Steam"},
            "deal": {
                "price": {"amount": 20000, "currency": "KRW"},
                "regular": {"amount": 20000},
                "cut": 0,
            },
        },
        {
            "timestamp": "2021-02-01T10:00:00+09:00",
            "shop": {"id": 61, "name": "Steam"},
            "deal": {
                "price": {"amount": 10000, "currency": "KRW"},
                "regular": {"amount": 20000},
                "cut": 50,
            },
        },
        {
            "timestamp": "2021-03-01T10:00:00+09:00",
            "shop": {"id": 61, "name": "Steam"},
            "deal": None,
        },
        {
            "timestamp": "2021-02-01T00:00:00Z",
            "shop": {"id": 16, "name": "Epic Game Store"},
            "deal": {
                "price": {"amount": 5.99, "currency": "USD"},
                "regular": {"amount": 19.99},
                "cut": 70,
            },
        },
    ]
    calls = []

    def request(*args, params=None, **kwargs):
        calls.append(params)
        return rows + [rows[0]]

    monkeypatch.setattr(itad_collect_service, "itad_request", request)
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "history"
        gid = game.id
        db.commit()
        first = itad_collect_service.collect_history_for_existing_itad_games(
            db, limit=1
        )
        db.commit()
        second = itad_collect_service.collect_history_for_existing_itad_games(
            db, limit=1
        )
        db.commit()
        assert (
            first["history_rows_imported"] == 4 and second["history_rows_imported"] == 0
        )
        assert calls[0]["since"].startswith("2005-") and not calls[1][
            "since"
        ].startswith("2005-")
        hist = main_module.build_history_response(db.get(Game, gid), "Steam", "all")
        assert hist["history"][0]["datetime"] == "2021-01-01T01:00:00Z"
        assert any(p["price"] is None for p in hist["history"])
        assert {"Steam", "Epic Game Store"} <= set(hist["available_platforms"])
        epic = main_module.build_history_response(
            db.get(Game, gid), "Epic Game Store", "all"
        )
        assert epic["currency"] == "USD" and epic["stats"]["lowest_price"] == 5.99


def test_bad_history_time_is_not_fabricated(monkeypatch):
    monkeypatch.setattr(
        itad_collect_service,
        "itad_request",
        lambda *_, **__: [
            {
                "timestamp": "bad",
                "shop": {"id": 61, "name": "Steam"},
                "deal": {"price": {"amount": 1}},
            }
        ],
    )
    with SessionLocal() as db:
        game = db.query(Game).first()
        game.itad_id = "bad-date"
        db.commit()
        result = itad_collect_service.collect_history_for_existing_itad_games(db)
        db.commit()
        assert result["error_count"] == 1 and game.history_synced_at is None
        assert (
            db.query(PriceSnapshot)
            .filter(PriceSnapshot.source == "itad-history")
            .count()
            == 0
        )


def test_duplicate_game_merge_preserves_wishlist_notifications_and_prices():
    from app.services.catalog_sync_service import merge_game_records

    with SessionLocal() as db:
        target = db.query(Game).filter(Game.title == "Test Game").one()
        duplicate = Game(title="Test Game™", itad_id="same-game")
        db.add(duplicate)
        db.flush()
        duplicate_id = duplicate.id
        user = db.query(User).filter(User.is_admin.is_(False)).one()
        item = WishlistItem(user_id=user.id, game_id=duplicate.id)
        db.add(item)
        db.flush()
        db.add(
            Notification(
                user_id=user.id,
                wishlist_item_id=item.id,
                type="TARGET_PRICE",
                title="keep me",
                message="keep",
            )
        )
        db.add(
            PriceSnapshot(
                game_id=duplicate.id,
                platform_id=1,
                original_price=50000,
                discounted_price=20000,
                discount_rate=60,
                collected_at=utc_now(),
                is_current=True,
            )
        )
        db.commit()
        merge_game_records(db, target, duplicate)
        db.commit()
        assert db.get(Game, duplicate_id) is None and target.itad_id == "same-game"
        assert (
            db.query(WishlistItem)
            .filter(WishlistItem.user_id == user.id, WishlistItem.game_id == target.id)
            .count()
            == 1
        )
        assert (
            db.query(Notification)
            .filter(Notification.title == "keep me")
            .one()
            .wishlist_item.game_id
            == target.id
        )
        assert (
            db.query(PriceSnapshot).filter(PriceSnapshot.game_id == target.id).count()
            == 2
        )
        assert (
            db.query(PriceSnapshot)
            .filter(
                PriceSnapshot.game_id == target.id, PriceSnapshot.is_current.is_(True)
            )
            .count()
            == 1
        )


def test_software_plan_catalog_and_annual_prices_are_separate_from_promotions(client):
    from app.models import SoftwarePlan

    with SessionLocal() as db:
        seed_subscription_catalog(db)
        seed_subscription_catalog(db)
        assert db.query(SoftwarePlan).count() == 70
        assert db.query(Software).filter(Software.source_key.isnot(None)).count() == 20
        db.query(SoftwarePlan).filter(SoftwarePlan.current_price.isnot(None)).update(
            {SoftwarePlan.checked_at: utc_now()}
        )
        db.commit()
    cards = client.get("/api/software?kind=ai").json()
    chat = next(c for c in cards if c["name"] == "ChatGPT")
    assert {p["name"]: p["current_price"] for p in chat["plans"]} == {
        "Plus": 20,
        "Pro 100": 100,
        "Pro 200": 200,
        "Pro 500": 500,
    }
    assert all(p["period"] == "month" and p["currency"] == "USD" for p in chat["plans"])
    detail = client.get(f"/api/software/{chat['id']}").json()
    assert detail["promotions"][0]["discounted_price"] is None and detail[
        "logo_url"
    ].startswith("/brands/")
    claude = next(c for c in cards if c["name"] == "Claude")
    annual = next(p for p in claude["plans"] if p["period"] == "year")
    assert (
        annual["current_price"] == 200
        and annual["monthly_equivalent"] == 16.67
        and annual["annual_saving_rate"] == 16.7
    )


def test_plan_price_parser_does_not_mix_billing_intervals_or_other_prices():
    from app.services.software_plan_service import load_plan_catalog, parse_plan_price

    entries = {p["source_key"]: p for p in load_plan_catalog()["plans"]}
    body = "Pro For everyday work $17 Per month with annual subscription discount ( $200 billed up front). $20 if billed monthly."
    assert parse_plan_price(entries["claude-pro-month"], "", body) == 20
    assert parse_plan_price(entries["claude-pro-year"], "", body) == 200
    html = '{"external_id":"plus_monthly_krw","product":"plus","billing_interval":"month","currency":"KRW","unit_amount":16800,"state":"current"}'
    html += '{"external_id":"plus_yearly_krw","product":"plus","billing_interval":"year","currency":"KRW","unit_amount":168000,"state":"current"}'
    assert parse_plan_price(entries["notion-plus-month"], html, "credits $10") == 16800
    assert parse_plan_price(entries["notion-plus-year"], html, "credits $10") == 168000
    with pytest.raises(ValueError):
        parse_plan_price(entries["chatgpt-pro-100"], "", "Pro 200 $200 only")


def test_catalog_backfill_queues_for_admin_and_rejects_duplicates(client):
    assert client.post("/api/admin/catalog/sync").status_code == 401
    user = login(client, "user@test.local", "user-pass-1234")
    assert (
        client.post("/api/admin/catalog/sync", headers=auth_header(user)).status_code
        == 403
    )
    admin = login(client, "admin@test.local", "admin-pass-1234")
    result = client.post("/api/admin/catalog/sync", headers=auth_header(admin))
    assert result.status_code == 202 and result.json()["status"]["run_requested"]
    assert (
        client.post("/api/admin/catalog/sync", headers=auth_header(admin)).status_code
        == 409
    )
    assert "history_pending_count" in result.json()["status"]


def test_initial_history_queue_drains_all_games_independently_of_full_refresh(
    monkeypatch,
):
    from app.services import history_sync_service as history_service

    monkeypatch.setenv("ITAD_API_KEY", "unit-test-placeholder")
    for name in [
        "collect_existing_steam_prices",
        "collect_steam_featured_games",
        "collect_epic_popular_games",
        "collect_existing_epic_prices",
        "import_itad_deals",
        "link_catalog_to_itad",
        "enrich_itad_catalog_metadata",
        "collect_current_prices_for_existing_itad_games",
        "refresh_subscription_offers",
    ]:
        monkeypatch.setattr(
            scheduler_service, name, lambda *_, **__: {"updated_count": 1}
        )
    calls = []

    def request(method, path, params=None, **kwargs):
        if path == "/games/history/v2":
            calls.append(params["id"])
            assert params["since"].startswith("2005-")
        return []

    monkeypatch.setattr(itad_collect_service, "itad_request", request)
    with SessionLocal() as db:
        db.add_all(
            [
                Game(title=f"Tracked popular {i}", itad_id=f"tracked-{i}")
                for i in range(27)
            ]
        )
        db.commit()
        scheduler_service.request_refresh(db, force_catalog=True)
    result = scheduler_service.run_auto_collect_once()
    assert calls == []  # 전체 수집이 이력 HTTP 요청을 붙잡고 있지 않습니다.
    with SessionLocal() as db:
        assert db.query(Game).filter(Game.history_queued_at.isnot(None)).count() == 27
    for _ in range(27):
        assert history_service.run_next_history_job() is not None
    assert len(calls) == 27 and len(set(calls)) == 27
    assert {"Steam 인기 본편", "Epic 인기 본편"} <= {
        step["source"] for step in result["steps"]
    }
    with SessionLocal() as db:
        assert (
            db.query(Game)
            .filter(Game.itad_id.isnot(None), Game.history_synced_at.is_(None))
            .count()
            == 0
        )
        assert db.get(AutoRefreshState, 1).last_status == "SUCCESS"


def test_steam_search_supports_new_items_json_and_rejects_bundle_cover_ids():
    appids, count = steam_collect_service.extract_steam_search_appids(
        {
            "desc": "",
            "items": [
                {
                    "name": "The Binding of Isaac: Rebirth",
                    "logo": "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/250900/capsule.jpg",
                },
                {
                    "name": "A bundle",
                    "logo": "https://shared.akamai.steamstatic.com/store_item_assets/steam/bundles/123/capsule.jpg",
                },
            ],
        }
    )
    assert appids == [250900] and count == 2
    old_ids, _ = steam_collect_service.extract_steam_search_appids(
        {
            "results_html": '<a data-ds-appid="250900" href="https://store.steampowered.com/app/250900">Isaac</a><a data-ds-appid="1,2" href="/bundle/3">bundle</a>'
        }
    )
    assert old_ids == [250900]


def test_new_manual_store_log_keeps_actual_platform_name(client):
    token = login(client, "admin@test.local", "admin-pass-1234")
    with SessionLocal() as db:
        game_id = db.query(Game).filter(Game.title == "Test Game").one().id
    response = client.post(
        "/api/admin/collect-price",
        headers=auth_header(token),
        json={
            "game_id": game_id,
            "platform": "New Store",
            "discounted_price": 45000,
            "original_price": 50000,
        },
    )
    assert response.status_code == 200
    assert response.json()["log"]["platform"] == "New Store"
