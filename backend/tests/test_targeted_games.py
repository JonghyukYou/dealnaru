from datetime import timedelta

import pytest
import requests
from test_api_smoke import auth_header, login
from test_api_smoke import client as client
from test_api_smoke import reset_database as reset_database

from app.database import SessionLocal
from app.models import CrawlingLog, Game, PriceSnapshot, UsedMarketItem, User
from app.services import itad_collect_service as itad
from app.services import targeted_game_service as targeted
from app.utils.time import utc_now

ITAD_ID = "018d937f-69ff-7253-9db4-e6393cb13f1a"


def baba_data():
    return {
        "steam_appid": 736260,
        "title": "Baba Is You",
        "genre": "인디, 퍼즐",
        "developer": "Hempuli Oy",
        "publisher": "Hempuli Oy",
        "release_date": "2019-03-13",
        "description": "규칙을 바꾸는 퍼즐 게임",
        "header_image": None,
        "store_url": "https://store.steampowered.com/app/736260/",
        "tags": ["퍼즐"],
        "original_price": 15500,
        "discounted_price": 15500,
        "discount_rate": 0,
        "currency": "KRW",
        "review_count": 10000,
    }


def admin_headers(client):
    return auth_header(login(client, "admin@test.local", "admin-pass-1234"))


@pytest.mark.parametrize(
    "path,payload",
    [
        ("find", {"query": "Baba Is You"}),
        ("import", {"steam_appid": 736260}),
    ],
)
def test_targeted_endpoints_require_admin(client, path, payload):
    assert client.post(f"/api/admin/games/{path}", json=payload).status_code == 401
    token = login(client, "user@test.local", "user-pass-1234")
    assert (
        client.post(
            f"/api/admin/games/{path}", json=payload, headers=auth_header(token)
        ).status_code
        == 403
    )


@pytest.mark.parametrize(
    "query",
    [
        "https://example.com/app/736260/",
        "https://store.steampowered.com/bundle/1/",
        "https://store.steampowered.com.evil.test/app/736260/",
        "0",
        "2147483648",
    ],
)
def test_requested_reference_rejects_non_store_urls_and_invalid_ids(query):
    with pytest.raises(ValueError):
        targeted.steam_reference(query)


def test_search_is_readonly_merges_sources_and_marks_existing(client, monkeypatch):
    monkeypatch.setattr(itad, "ITAD_API_KEY", "test")
    monkeypatch.setattr(
        targeted,
        "search_steam_titles",
        lambda _: [targeted.steam_candidate(baba_data())],
    )
    monkeypatch.setattr(
        targeted,
        "search_itad_title",
        lambda _: [
            {
                **targeted.steam_candidate(baba_data()),
                "itad_id": ITAD_ID,
                "sources": ["IsThereAnyDeal"],
            }
        ],
    )
    with SessionLocal() as db:
        game = Game(title="Baba Is You", steam_appid=736260)
        db.add(game)
        db.commit()
        gid = game.id
        before = db.query(PriceSnapshot).count()
    response = client.post(
        "/api/admin/games/find",
        json={"query": "Baba Is You"},
        headers=admin_headers(client),
    )
    assert response.status_code == 200
    candidate = response.json()["candidates"][0]
    assert candidate["sources"] == ["IsThereAnyDeal", "Steam"]
    assert candidate["itad_id"] == ITAD_ID and candidate["existing_game_id"] == gid
    with SessionLocal() as db:
        assert db.query(Game).count() == 3 and db.query(PriceSnapshot).count() == before


def test_steam_search_filters_packages_soundtracks_adult_and_free(monkeypatch):
    class Response:
        def raise_for_status(self):
            pass

        def json(self):
            return {
                "items": [
                    {
                        "id": 736260,
                        "type": "app",
                        "name": "Baba Is You",
                        "price": {"initial": 1550000},
                    },
                    {"id": 955710, "type": "app", "name": "Baba Is You Soundtrack"},
                    {"id": 1, "type": "bundle", "name": "Game collection"},
                    {"id": 2, "type": "app", "name": "Sex Simulator"},
                    {
                        "id": 3,
                        "type": "app",
                        "name": "Free Game",
                        "price": {"initial": 0},
                    },
                    {"id": 4, "type": "app", "name": "DOOM Eternal"},
                ]
            }

    monkeypatch.setattr(targeted.requests, "get", lambda *_, **__: Response())
    assert [row["title"] for row in targeted.search_steam_titles("Baba")] == [
        "Baba Is You",
        "DOOM Eternal",
    ]


@pytest.mark.parametrize(
    "query", ["736260", "https://store.steampowered.com/app/736260/Baba_Is_You/"]
)
def test_direct_reference_search_validates_details_without_creating_game(
    monkeypatch, query
):
    monkeypatch.setattr(
        targeted,
        "fetch_steam_app_details",
        lambda appid, **_: baba_data() if appid == 736260 else None,
    )
    with SessionLocal() as db:
        response = targeted.search_requested_games(db, query)
        assert response["candidates"][0]["steam_appid"] == 736260
        assert db.query(Game).count() == 2


def test_requested_paid_regular_game_import_reuses_game_when_itad_unconfigured(
    client, monkeypatch
):
    monkeypatch.setattr(itad, "ITAD_API_KEY", "")
    monkeypatch.setattr(
        targeted, "fetch_steam_app_details", lambda *_, **__: baba_data()
    )
    headers = admin_headers(client)
    first = client.post(
        "/api/admin/games/import", json={"steam_appid": 736260}, headers=headers
    )
    second = client.post(
        "/api/admin/games/import", json={"steam_appid": 736260}, headers=headers
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["result"]["created"] and not second.json()["result"]["created"]
    assert first.json()["result"]["game_id"] == second.json()["result"]["game_id"]
    assert (
        first.json()["result"]["warnings"]
        and not first.json()["result"]["history_queued"]
    )
    with SessionLocal() as db:
        game = db.query(Game).filter(Game.steam_appid == 736260).one()
        assert game.catalog_source == "관리자 지정 수집" and db.query(User).count() == 2
        current = [price for price in game.prices if price.is_current]
        assert (
            len(current) == 1
            and current[0].discounted_price == 15500
            and current[0].discount_rate == 0
        )
        assert all(
            log.platform.name == "Steam" and log.status == "PARTIAL"
            for log in db.query(CrawlingLog)
        )


def test_targeted_import_links_and_updates_only_requested_game_and_queues_history(
    monkeypatch,
):
    monkeypatch.setattr(itad, "ITAD_API_KEY", "test")
    monkeypatch.setattr(
        targeted, "fetch_steam_app_details", lambda *_, **__: baba_data()
    )
    calls = []

    def request(method, path, params=None, json_body=None):
        calls.append((path, json_body))
        if path == "/lookup/id/shop/61/v1":
            assert json_body == ["app/736260"]
            return {"app/736260": ITAD_ID}
        if path == "/games/info/v2":
            return {
                "id": ITAD_ID,
                "type": "game",
                "title": "Baba Is You",
                "slug": "baba-is-you",
                "appid": 736260,
                "tags": ["Puzzle"],
                "assets": {},
            }
        if path == "/games/prices/v3":
            assert json_body == [ITAD_ID]
            return [
                {
                    "id": ITAD_ID,
                    "deals": [
                        {
                            "shop": {"id": 35, "name": "GOG"},
                            "price": {"amount": 10000, "currency": "KRW"},
                            "regular": {"amount": 15500, "currency": "KRW"},
                            "cut": 35,
                            "url": "https://www.gog.com/en/game/baba_is_you",
                        }
                    ],
                }
            ]
        raise AssertionError(path)

    monkeypatch.setattr(itad, "itad_request", request)
    with SessionLocal() as db:
        game = Game(
            title="Baba Is You", steam_appid=736260, itad_lookup_checked_at=utc_now()
        )
        other = Game(title="Other linked game", itad_id="other")
        db.add_all([game, other])
        db.commit()
        other_id = other.id
        result = targeted.collect_requested_game(db, steam_appid=736260)
        assert not result["created"] and result["itad_id"] == ITAD_ID
        assert (
            result["updated_count"] == 2
            and result["history_queued"]
            and not result["warnings"]
        )
        assert db.get(Game, result["game_id"]).history_priority == 2
        assert db.get(Game, other_id).history_queued_at is None
        assert not db.get(Game, other_id).prices
        assert len(calls) == 3


def test_itad_failure_preserves_steam_game_and_hides_request_secrets(monkeypatch):
    monkeypatch.setattr(itad, "ITAD_API_KEY", "test")
    monkeypatch.setattr(
        targeted, "fetch_steam_app_details", lambda *_, **__: baba_data()
    )

    def failed(*_, **__):
        raise requests.ConnectionError("https://provider.test/?key=DO-NOT-EXPOSE")

    monkeypatch.setattr(targeted, "link_catalog_to_itad", failed)
    with SessionLocal() as db:
        result = targeted.collect_requested_game(db, steam_appid=736260)
        assert db.get(Game, result["game_id"]).prices and result["warnings"]
        assert "DO-NOT-EXPOSE" not in str(result)


@pytest.mark.parametrize(
    "reason", ["게임 타입이 아님", "무료 게임", "노골적인 성인 콘텐츠"]
)
def test_rejected_product_import_creates_no_game_and_logs_failure(
    client, monkeypatch, reason
):
    monkeypatch.setattr(
        targeted, "fetch_steam_app_details", lambda *_, **__: {"_skip_reason": reason}
    )
    response = client.post(
        "/api/admin/games/import",
        json={"steam_appid": 1},
        headers=admin_headers(client),
    )
    assert response.status_code == 400
    with SessionLocal() as db:
        assert (
            db.query(Game).count() == 2 and db.query(CrawlingLog).one().status == "FAIL"
        )


def test_used_market_search_matches_titles_and_treats_wildcards_literally(client):
    with SessionLocal() as db:
        db.add_all(
            [
                UsedMarketItem(
                    source="중고나라",
                    keyword="PS5",
                    title="Baba Is You Switch 패키지",
                    price=10000,
                    item_url="https://example.com/baba",
                    collected_at=utc_now(),
                ),
                UsedMarketItem(
                    source="중고나라",
                    keyword="오래된 키워드",
                    title="old",
                    price=10000,
                    item_url="https://example.com/old",
                    collected_at=utc_now() - timedelta(days=30),
                ),
            ]
        )
        db.commit()
    response = client.get("/api/used-market", params={"keyword": "baba"}).json()
    assert response["total"] == 1 and "오래된 키워드" not in response["keywords"]
    assert client.get("/api/used-market", params={"keyword": "%"}).json()["total"] == 0


def test_home_random_uses_another_regular_game_when_only_one_game_is_discounted(client):
    with SessionLocal() as db:
        steam = (
            db.query(Game).filter(Game.title == "Test Game").one().prices[0].platform
        )
        regular = Game(title="Another regular game")
        db.add(regular)
        db.flush()
        db.add(
            PriceSnapshot(
                game_id=regular.id,
                platform_id=steam.id,
                original_price=15500,
                discounted_price=15500,
                discount_rate=0,
                is_current=True,
                collected_at=utc_now(),
            )
        )
        db.commit()
    home = client.get("/api/home").json()
    assert home["today_deal"]["title"] == "Test Game"
    assert home["random_game"]["title"] == "Another regular game"
