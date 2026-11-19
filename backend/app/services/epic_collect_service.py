"""Epic 공식 스토어의 인기 판매/인기 플레이 모듈과 실제 구매 가격을 수집합니다."""

import json
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import requests
from sqlalchemy import func

from app.models import Game
from app.services.catalog_filter_service import non_game_reason
from app.services.collection_service import update_game_price_and_notify
from app.services.content_filter_service import is_explicit_adult_itad_item
from app.services.itad_collect_service import parse_optional_datetime
from app.utils.time import utc_now

LAYOUT_URL = "https://store-site-backend-static-ipv4.ak.epicgames.com/storefrontLayout"
GRAPHQL_URL = "https://store.epicgames.com/graphql"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; GameSoft/0.5)",
    "Accept": "application/json",
}
POPULAR_GROUPS = {"top sellers", "most played", "most popular"}
OFFER_FIELDS = """id namespace title description offerType status productSlug urlSlug
    categories { path } keyImages { type url } developerDisplayName publisherDisplayName
    releaseDate effectiveDate prePurchase countriesBlacklist countriesWhitelist
    catalogNs { mappings(pageType: "productHome") { pageSlug pageType } }
    offerMappings { pageSlug pageType }
    price(country: $country) { totalPrice { originalPrice discountPrice currencyCode currencyInfo { decimals } }
        lineOffers { appliedRules { endDate } } }"""
OFFER_QUERY = (
    'query game($namespace:String!,$id:String!,$country:String!){Catalog{catalogOffer(namespace:$namespace,id:$id,locale:"en-US"){ '
    + OFFER_FIELDS
    + " }}}"
)


def fetch_epic_layout(country="KR"):
    response = requests.get(
        LAYOUT_URL,
        params={"locale": "en-US", "country": country},
        headers=HEADERS,
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    if data.get("errors"):
        raise ValueError("Epic 인기 목록 응답 오류")
    return data


def popular_epic_offers(layout):
    found = []
    seen = set()

    def walk(value):
        if isinstance(value, dict):
            title = (value.get("title") or "").strip().lower()
            if title in POPULAR_GROUPS:
                for rank, entry in enumerate(value.get("offers") or [], 1):
                    offer = entry.get("offer") or {}
                    key = (offer.get("namespace"), offer.get("id"))
                    if all(key) and key not in seen:
                        found.append((offer, value["title"], rank))
                        seen.add(key)
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)

    walk(layout)
    if not found:
        raise ValueError(
            "Epic 공식 인기 모듈을 확인하지 못했습니다. 일반 목록을 인기 순위로 대체하지 않습니다."
        )
    return found


def epic_skip_reason(offer, country="KR"):
    categories = {row.get("path") for row in offer.get("categories") or []}
    if offer.get("offerType") != "BASE_GAME" or "games/edition/base" not in categories:
        return "본편 게임이 아님"
    if offer.get("status") not in {None, "ACTIVE"}:
        return "판매 중이 아님"
    if country in (offer.get("countriesBlacklist") or []) or (
        offer.get("countriesWhitelist") and country not in offer["countriesWhitelist"]
    ):
        return "해당 국가에서 구매 불가"
    if non_game_reason(offer.get("title")):
        return "번들·추가 콘텐츠"
    if is_explicit_adult_itad_item(
        {"title": offer.get("title"), "description": offer.get("description")}
    ):
        return "노골적인 성인 콘텐츠"
    released = parse_optional_datetime(offer.get("effectiveDate"))
    if offer.get("prePurchase") or (released and released > utc_now()):
        return "출시 예정 게임"
    total = (offer.get("price") or {}).get("totalPrice") or {}
    if total.get("originalPrice") == 0:
        return "상시 무료 게임"
    return None


def upsert_epic_game(db, offer, group=None, rank=None, country="KR"):
    reason = epic_skip_reason(offer, country)
    if reason:
        return {"skipped": reason, "title": offer.get("title")}
    mappings = (
        offer.get("offerMappings")
        or (offer.get("catalogNs") or {}).get("mappings")
        or []
    )
    slug = next(
        (row["pageSlug"] for row in mappings if row.get("pageType") == "productHome"),
        None,
    )
    slug = slug or offer.get("productSlug") or offer.get("urlSlug")
    if not slug:
        return {"skipped": "공식 구매 페이지 없음", "title": offer.get("title")}
    namespace, offer_id = offer["namespace"], offer["id"]
    game = (
        db.query(Game)
        .filter(Game.epic_namespace == namespace, Game.epic_offer_id == offer_id)
        .first()
    )
    if game is None:
        game = (
            db.query(Game)
            .filter(func.lower(Game.title) == offer["title"].lower())
            .first()
        )
    if game is None:
        game = Game(title=offer["title"], genre="기타", image_color="#292936")
        db.add(game)
    game.epic_namespace, game.epic_offer_id, game.epic_slug = namespace, offer_id, slug
    game.product_type = "game"
    game.developer = game.developer or offer.get("developerDisplayName")
    game.publisher = game.publisher or offer.get("publisherDisplayName")
    game.description = game.description or offer.get("description")
    game.release_date = game.release_date or (offer.get("releaseDate") or "")[:10]
    if group and not game.catalog_source:
        game.catalog_source = "Epic " + group
        game.catalog_rank = rank
    images = offer.get("keyImages") or []
    game.header_image = game.header_image or next(
        (
            row["url"]
            for row in images
            if row.get("type") in {"OfferImageWide", "DieselStoreFrontWide"}
        ),
        None,
    )
    url = "https://store.epicgames.com/ko/p/" + slug
    game.store_url = game.store_url or url
    db.flush()
    total = (offer.get("price") or {}).get("totalPrice") or {}
    if (
        total.get("discountPrice") is not None
        and total.get("originalPrice") is not None
    ):
        divisor = Decimal(10) ** int(
            (total.get("currencyInfo") or {}).get("decimals", 2)
        )
        discounted, regular = (
            Decimal(total["discountPrice"]) / divisor,
            Decimal(total["originalPrice"]) / divisor,
        )
        if regular > 0 and discounted >= 0:
            expiry = [
                parse_optional_datetime(rule.get("endDate"))
                for line in (offer.get("price") or {}).get("lineOffers") or []
                for rule in line.get("appliedRules") or []
            ]
            update_game_price_and_notify(
                db,
                game.id,
                "Epic Game Store",
                discounted,
                regular,
                url=url,
                currency=total.get("currencyCode") or "KRW",
                itad_shop_id=16,
                source="epic",
                sale_end_at=min(
                    (value for value in expiry if value and value > utc_now()),
                    default=None,
                ),
            )
    return {"game_id": game.id, "title": game.title}


def collect_epic_popular_games(db, max_items=40, country="KR"):
    offers = popular_epic_offers(fetch_epic_layout(country))
    imported, skipped, errors = [], [], []
    for offer, group, rank in offers:
        if len(imported) >= max_items:
            break
        try:
            with db.begin_nested():
                row = upsert_epic_game(db, offer, group, rank, country)
                (skipped if row.get("skipped") else imported).append(row)
        except Exception as error:
            errors.append({"title": offer.get("title"), "error": type(error).__name__})
    return {
        "imported_count": len(imported),
        "updated_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "skip_reasons": dict(Counter(row["skipped"] for row in skipped)),
        "imported": imported[:20],
        "errors": errors[:10],
        "scanned_count": len(offers),
    }


def collect_existing_epic_prices(db, limit=80, offset=0, country="KR"):
    games = (
        db.query(Game)
        .filter(Game.epic_offer_id.isnot(None))
        .order_by(Game.id)
        .offset(offset)
        .limit(limit)
        .all()
    )
    jobs = [(game.id, game.epic_namespace, game.epic_offer_id) for game in games]

    def fetch(job):
        gid, namespace, offer_id = job
        try:
            response = requests.get(
                GRAPHQL_URL,
                params={
                    "query": OFFER_QUERY,
                    "variables": json.dumps(
                        {"namespace": namespace, "id": offer_id, "country": country}
                    ),
                },
                headers=HEADERS,
                timeout=20,
            )
            response.raise_for_status()
            data = response.json()
            if data.get("errors"):
                raise ValueError("Epic 가격 API 응답 오류")
            offer = (data.get("data", {}).get("Catalog") or {}).get("catalogOffer")
            return gid, offer, None
        except Exception as error:
            return gid, None, type(error).__name__

    result = {"updated_count": 0, "skipped_count": 0, "error_count": 0}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for gid, offer, error in pool.map(fetch, jobs):
            if error:
                result["error_count"] += 1
            elif offer:
                with db.begin_nested():
                    row = upsert_epic_game(db, offer, country=country)
                    result[
                        "skipped_count" if row.get("skipped") else "updated_count"
                    ] += 1
            else:
                result["skipped_count"] += 1
    return result
