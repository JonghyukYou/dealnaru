import os
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation

import requests
from dotenv import load_dotenv
from sqlalchemy import func, or_

from app.models import (
    Game,
    GameTag,
    Platform,
    PriceSnapshot,
    Software,
    SoftwarePromotion,
)
from app.services.catalog_filter_service import non_game_reason
from app.services.collection_service import update_game_price_and_notify
from app.services.content_filter_service import is_explicit_adult_itad_item
from app.services.price_policy_service import excessive_discount_reason
from app.services.source_request_service import (
    SourceRequestError,
    SourceRequestGate,
    collection_error_detail,
    retry_after_seconds,
    response_error_detail,
)
from app.utils.time import utc_now

load_dotenv()

ITAD_API_KEY = os.getenv("ITAD_API_KEY")
BASE_URL = "https://api.isthereanydeal.com"
# 자동 갱신·이력 보강·관리자 요청이 하나의 요청 간격/대기 시간을 공유합니다.
request_gate = SourceRequestGate("ITAD", interval=1.0, persistent=True)

HEADERS = {
    "User-Agent": "GameDiscountPredictor-Capstone/1.0",
    "Accept": "application/json",
}

GAME_GENRE_RULES = {
    "액션": [
        "action",
        "action-adventure",
        "shooter",
        "fps",
        "third-person",
        "beat em up",
        "hack and slash",
        "fighting",
    ],
    "RPG": [
        "rpg",
        "role-playing",
        "jrpg",
        "crpg",
        "action rpg",
        "souls-like",
        "soulslike",
        "dungeon crawler",
    ],
    "어드벤쳐": [
        "adventure",
        "point & click",
        "story rich",
        "visual novel",
        "interactive fiction",
    ],
    "전략": [
        "strategy",
        "grand strategy",
        "rts",
        "turn-based strategy",
        "4x",
        "tactical",
        "tactics",
        "tower defense",
    ],
    "시뮬": [
        "simulation",
        "simulator",
        "management",
        "city builder",
        "colony sim",
        "life sim",
        "farming sim",
    ],
    "인디": ["indie"],
    "공포": ["horror", "survival horror", "psychological horror"],
    "퍼즐": ["puzzle", "logic", "hidden object"],
    "스포츠": [
        "sports",
        "football",
        "soccer",
        "basketball",
        "baseball",
        "tennis",
        "golf",
    ],
    "레이싱": ["racing", "driving", "automobile sim"],
    "캐주얼": ["casual", "family friendly", "relaxing"],
}


def should_skip_as_non_game(title, tags, game_type=None):
    return non_game_reason(title, tags, game_type) is not None


def fetch_itad_game_info(itad_id):
    return itad_request(
        "GET",
        "/games/info/v2",
        params={
            "id": itad_id,
        },
    )


def normalize_keyword(value):
    if value is None:
        return ""

    return str(value).lower().replace("-", " ").replace("_", " ").strip()


def map_genre_from_tags(tags):
    normalized_tags = [normalize_keyword(tag) for tag in tags if tag]

    for genre_name, keywords in GAME_GENRE_RULES.items():
        for tag in normalized_tags:
            for keyword in keywords:
                normalized_keyword = normalize_keyword(keyword)

                if normalized_keyword in tag or tag in normalized_keyword:
                    return genre_name

    return "기타"


def looks_like_software(title, tags, game_type=None):
    return should_skip_as_non_game(title, tags, game_type)


def require_api_key():
    if not ITAD_API_KEY:
        raise RuntimeError("ITAD_API_KEY가 .env에 설정되지 않았습니다.")


def itad_request(method, path, params=None, json_body=None):
    require_api_key()
    request_gate.wait()

    headers = {
        **HEADERS,
        "ITAD-API-Key": ITAD_API_KEY,
    }

    response = requests.request(
        method=method,
        url=f"{BASE_URL}{path}",
        params=params or {},
        json=json_body,
        headers=headers,
        timeout=30,
        allow_redirects=False,
    )

    if not 200 <= response.status_code < 300:
        detail = response_error_detail(response, secrets=(ITAD_API_KEY,)) if response.status_code == 400 else None
        retry_after = 0
        if response.status_code == 429:
            retry_after = retry_after_seconds(response.headers.get("Retry-After"))
            request_gate.pause(retry_after, detail=detail)
        elif response.status_code in {401, 403}:
            retry_after = 300
            request_gate.pause(retry_after, response.status_code, detail=detail)
        elif response.status_code in {500, 502, 503, 504}:
            retry_after = retry_after_seconds(response.headers.get("Retry-After"), default=60)
            request_gate.pause(retry_after, response.status_code, detail=detail)
        raise SourceRequestError("ITAD", response.status_code, retry_after, detail=detail)
    return response.json()


def money_to_int(money):
    """기존 함수명을 유지하되 USD/EUR의 소수점도 보존합니다."""
    if not money:
        return 0
    try:
        amount = money.get("amount")
        value = (
            Decimal(str(amount))
            if amount is not None
            else Decimal(str(money.get("amountInt", 0))) / 100
        )
        return (
            value.quantize(Decimal("0.01"))
            if value.is_finite() and value >= 0
            else Decimal(0)
        )
    except (InvalidOperation, TypeError, ValueError):
        return Decimal(0)


def deal_exclusion_reason(deal):
    shop = deal.get("shop") or {}
    price, regular = deal.get("price") or {}, deal.get("regular") or {}
    same_currency = price.get("currency") == regular.get("currency")
    return excessive_discount_reason(
        shop.get("name"),
        shop.get("id"),
        money_to_int(regular) if same_currency else None,
        money_to_int(price),
        deal.get("cut"),
    )


def parse_datetime(value):
    parsed = parse_optional_datetime(value)
    return parsed or datetime.utcnow()


def parse_optional_datetime(value):
    if not value:
        return None

    normalized = str(value).replace("Z", "+00:00")

    try:
        parsed = datetime.fromisoformat(normalized)

        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)

        return parsed
    except ValueError:
        return None


def build_since_datetime(days):
    since = datetime.now(timezone.utc) - timedelta(days=days)
    return since.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_best_asset(assets):
    if not assets:
        return None

    return (
        assets.get("banner600")
        or assets.get("banner400")
        or assets.get("banner300")
        or assets.get("boxart")
    )


def get_or_create_platform(db, shop):
    if shop is None:
        shop = {}

    if isinstance(shop, int):
        shop_id = shop
        shop_name = f"Shop {shop_id}"
    elif isinstance(shop, dict):
        shop_id = shop.get("id")
        shop_name = shop.get("name") or shop.get("title") or f"Shop {shop_id}"
    else:
        shop_id = None
        shop_name = str(shop)

    platform = None

    if shop_id is not None:
        platform = db.query(Platform).filter(Platform.itad_shop_id == shop_id).first()

    if platform is None:
        platform = db.query(Platform).filter(Platform.name == shop_name).first()

    if platform is None:
        platform = Platform(
            name=shop_name,
            itad_shop_id=shop_id,
        )
        db.add(platform)
        db.flush()
    else:
        if shop_id is not None and platform.itad_shop_id is None:
            platform.itad_shop_id = shop_id

    return platform


def normalize_history_response(history_response):
    if isinstance(history_response, list):
        return history_response

    if isinstance(history_response, dict):
        for key in ["list", "data", "history", "items", "rows"]:
            value = history_response.get(key)

            if isinstance(value, list):
                return value

    return []


def upsert_game_from_itad_item(db, item, info=None):
    itad_id = item.get("id")

    if not itad_id:
        return None

    if info is None:
        info = fetch_itad_game_info(itad_id)

    if is_explicit_adult_itad_item(item, info):
        return None

    title = info.get("title") or item.get("title")
    slug = info.get("slug") or item.get("slug")
    game_type = info.get("type")
    tags = info.get("tags") or []
    assets = info.get("assets") or {}

    if not title:
        return None

    if should_skip_as_non_game(title, tags, game_type):
        return None

    steam_appid = info.get("appid")

    game = db.query(Game).filter(Game.itad_id == itad_id).first()

    if game is None and steam_appid:
        game = db.query(Game).filter(Game.steam_appid == int(steam_appid)).first()

    if game is None:
        game = db.query(Game).filter(func.lower(Game.title) == title.lower()).first()

    if game is None:
        game = Game(
            title=title,
            image_color="#1f2933",
            rating="ITAD 수집",
            korean_support="정보 확인 필요",
        )
        db.add(game)
        db.flush()

    developers = info.get("developers") or []
    publishers = info.get("publishers") or []

    developer_names = [
        developer.get("name")
        for developer in developers
        if isinstance(developer, dict) and developer.get("name")
    ]

    publisher_names = [
        publisher.get("name")
        for publisher in publishers
        if isinstance(publisher, dict) and publisher.get("name")
    ]

    game.itad_id = itad_id
    if "appid" in info and "tags" in info:
        game.itad_info_checked_at = utc_now()
    if game_type:
        game.product_type = game_type
    game.itad_slug = slug
    game.title = title
    if tags or not game.genre:
        game.genre = map_genre_from_tags(tags)
    game.developer = ", ".join(developer_names) if developer_names else game.developer
    game.publisher = ", ".join(publisher_names) if publisher_names else game.publisher
    game.release_date = info.get("releaseDate") or game.release_date
    for review in info.get("reviews") or []:
        if isinstance(review, dict) and review.get("count"):
            game.review_count = max(game.review_count or 0, int(review["count"]))
    game.description = info.get("description") or game.description
    game.header_image = get_best_asset(assets) or game.header_image
    itad_url = f"https://isthereanydeal.com/game/{slug}/" if slug else None

    if not game.store_url:
        game.store_url = itad_url
    elif not game.steam_appid and itad_url:
        game.store_url = itad_url

    if steam_appid and not game.steam_appid:
        game.steam_appid = int(steam_appid)

    existing_tags = {tag.name for tag in game.tags}

    for tag_name in tags[:8]:
        if tag_name not in existing_tags:
            db.add(GameTag(game=game, name=tag_name))
            existing_tags.add(tag_name)

    db.flush()

    return game


def upsert_software_from_itad_info(db, info, deal=None):
    title = info.get("title")

    if not title:
        return None

    software = (
        db.query(Software).filter(func.lower(Software.name) == title.lower()).first()
    )

    if software is None:
        software = Software(
            name=title,
            category="기타",
            developer=None,
            description=info.get("description")
            or "ITAD에서 수집된 소프트웨어성 항목입니다.",
            image_color="#374151",
            official_url=f"https://isthereanydeal.com/game/{info.get('slug')}/"
            if info.get("slug")
            else "#",
            pricing_type="유료",
            rating_label="ITAD 수집",
        )
        db.add(software)
        db.flush()

    software.description = info.get("description") or software.description
    software.official_url = (
        f"https://isthereanydeal.com/game/{info.get('slug')}/"
        if info.get("slug")
        else software.official_url
    )

    if deal:
        shop = deal.get("shop") or {}
        price = deal.get("price")
        regular = deal.get("regular")

        discounted_price = money_to_int(price)
        original_price = money_to_int(regular)

        if discounted_price > 0:
            promotion = SoftwarePromotion(
                software=software,
                title="ITAD 수집 프로모션",
                platform=shop.get("name") or "Unknown",
                promo_type="가격 정보",
                original_price=original_price or discounted_price,
                discounted_price=discounted_price,
                discount_rate=deal.get("cut") or 0,
                start_date=None,
                end_date=None,
                coupon_code=None,
                url=deal.get("url") or software.official_url or "#",
                description="IsThereAnyDeal에서 수집된 가격 정보입니다.",
                is_current=True,
            )
            db.add(promotion)

    db.flush()

    return software


def import_itad_deals(
    db,
    limit=50,
    offset=0,
    country="KR",
    sort="-cut",
    shop_ids=None,
):
    params = {
        "country": country,
        "limit": limit,
        "offset": offset,
        "sort": sort,
        "nondeals": False,
        "mature": True,
    }
    normalized_shop_ids = [
        int(shop_id) for shop_id in (shop_ids or []) if int(shop_id) > 0
    ]

    if normalized_shop_ids:
        params["shops"] = ",".join(str(shop_id) for shop_id in normalized_shop_ids)

    imported = []
    skipped = []
    errors = []
    seen = set()
    scan_limit = min(1000, max(50, limit * 6))
    page_size = min(200, max(20, limit))
    next_offset = offset
    has_more = False
    deadline = time.monotonic() + 90

    def candidates():
        nonlocal next_offset, has_more
        for _ in range(10):
            if len(seen) >= scan_limit or time.monotonic() >= deadline:
                break
            params.update({"offset": next_offset, "limit": page_size})
            data = itad_request("GET", "/deals/v2", params=params)
            items = data.get("list") or []
            more_pages = bool(data.get("hasMore"))
            has_more = more_pages
            # 중간에 목표에 도달하면 다음 호출에서 미처리 항목을 이어받습니다.
            page_start = next_offset
            for index, item in enumerate(items):
                next_offset = page_start + index + 1
                has_more = more_pages or index + 1 < len(items)
                if item.get("id") in seen:
                    continue
                seen.add(item.get("id"))
                yield item
                if len(seen) >= scan_limit:
                    return
            reported_offset = data.get("nextOffset")
            if not items or not more_pages:
                return
            if reported_offset is not None:
                if int(reported_offset) <= page_start:
                    return
                next_offset = int(reported_offset)

    for item in candidates():
        try:
            with db.begin_nested():
                if is_explicit_adult_itad_item(item, item):
                    skipped.append(
                        {
                            "title": item.get("title"),
                            "reason": "노골적인 성인 콘텐츠",
                        }
                    )
                    continue

                non_game = non_game_reason(
                    item.get("title"), item.get("tags") or [], item.get("type")
                )
                if non_game:
                    skipped.append({"title": item.get("title"), "reason": non_game})
                    continue

                deal = item.get("deal") or {}
                shop = deal.get("shop") or {}
                if reason := deal_exclusion_reason(deal):
                    skipped.append(
                        {
                            "title": item.get("title"),
                            "platform": shop.get("name"),
                            "reason": reason,
                        }
                    )
                    continue
                price = deal.get("price")
                regular = deal.get("regular")

                discounted_price = money_to_int(price)
                original_price = money_to_int(regular)
                currency = (price or {}).get("currency") or "KRW"

                if discounted_price <= 0:
                    skipped.append(
                        {"title": item.get("title"), "reason": "유효한 가격 없음"}
                    )
                    continue

                # 명확한 타입이 있는 Deals는 빠르게 처리하고, 타입이 없는 상품만 상세 확인.
                info = item
                if not item.get("type"):
                    info = fetch_itad_game_info(item.get("id"))
                    if info.get("type") != "game":
                        skipped.append(
                            {
                                "title": item.get("title"),
                                "reason": "본편 게임 타입 확인 불가",
                            }
                        )
                        continue
                game = upsert_game_from_itad_item(db, item, info=info)
                if game is None:
                    skipped.append(
                        {"title": item.get("title"), "reason": "게임 정보 부족"}
                    )
                    continue

                result = update_game_price_and_notify(
                    db=db,
                    game_id=game.id,
                    platform_name=shop.get("name") or "Unknown",
                    itad_shop_id=shop.get("id"),
                    discounted_price=discounted_price,
                    original_price=original_price or discounted_price,
                    currency=currency,
                    url=deal.get("url") or game.store_url or "#",
                    collected_at=utc_now(),
                    sale_end_at=parse_optional_datetime(deal.get("expiry")),
                    source="itad-current",
                )

                imported.append(result)
            if len(imported) >= limit:
                break
        except Exception as error:
            errors.append(
                {
                    "title": item.get("title"),
                    "error": str(error),
                }
            )

    return {
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "imported": imported[:20],
        "skipped": skipped[:20],
        "errors": errors[:20],
        "scanned_count": len(seen),
        "requested_count": limit,
        "skip_reasons": dict(Counter(item["reason"] for item in skipped)),
        "has_more": has_more,
        "next_offset": next_offset,
        "shop_ids": normalized_shop_ids,
    }


def import_itad_popular_games(db, limit=100, offset=0, country="KR"):
    popular_items = itad_request(
        "GET",
        "/stats/most-popular/v1",
        params={
            "limit": limit,
            "offset": offset,
        },
    )

    imported = []
    moved_to_software = []
    skipped = []
    errors = []

    game_ids_for_price = []

    for item in popular_items:
        try:
            itad_id = item.get("id")

            if not itad_id:
                skipped.append(
                    {
                        "title": item.get("title"),
                        "reason": "ITAD ID 없음",
                    }
                )
                continue

            info = fetch_itad_game_info(itad_id)

            title = info.get("title") or item.get("title")
            game_type = info.get("type")
            tags = info.get("tags") or []

            if is_explicit_adult_itad_item(item, info):
                skipped.append(
                    {
                        "title": title,
                        "reason": "노골적인 성인 콘텐츠",
                    }
                )
                continue

            if should_skip_as_non_game(title, tags, game_type):
                skipped.append(
                    {
                        "title": title,
                        "reason": f"ITAD type이 game이 아님: {game_type}",
                    }
                )
                continue

            game = upsert_game_from_itad_item(db, item, info=info)

            if game is None:
                skipped.append(
                    {
                        "title": title,
                        "reason": "게임 정보 부족 또는 저장 실패",
                    }
                )
                continue

            imported.append(
                {
                    "id": game.id,
                    "title": game.title,
                    "itad_id": game.itad_id,
                }
            )

            game_ids_for_price.append(itad_id)
            if not game.catalog_source:
                game.catalog_source = "ITAD 인기"
                game.catalog_rank = offset + len(imported)

        except Exception as error:
            errors.append(
                {
                    "title": item.get("title"),
                    "error": str(error),
                }
            )

    # 인기 게임을 넣은 뒤 바로 현재가도 붙입니다.
    if game_ids_for_price:
        prices_data = itad_request(
            "POST",
            "/games/prices/v3",
            params={
                "country": country,
                "vouchers": True,
                "capacity": 0,
                "deals": False,
            },
            json_body=game_ids_for_price[:200],
        )

        game_map = {
            game.itad_id: game
            for game in db.query(Game)
            .filter(Game.itad_id.in_(game_ids_for_price))
            .all()
        }

        for item in prices_data:
            game = game_map.get(item.get("id"))

            if game is None:
                continue

            for deal in item.get("deals") or []:
                if deal_exclusion_reason(deal):
                    continue
                shop = deal.get("shop") or {}
                price = deal.get("price")
                regular = deal.get("regular")

                discounted_price = money_to_int(price)
                original_price = money_to_int(regular)
                currency = (price or {}).get("currency") or "KRW"

                if discounted_price <= 0:
                    continue

                update_game_price_and_notify(
                    db=db,
                    game_id=game.id,
                    platform_name=shop.get("name") or "Unknown",
                    itad_shop_id=shop.get("id"),
                    discounted_price=discounted_price,
                    original_price=original_price or discounted_price,
                    currency=currency,
                    url=deal.get("url") or game.store_url or "#",
                    collected_at=utc_now(),
                    sale_end_at=parse_optional_datetime(deal.get("expiry")),
                    source="itad-current",
                )

    db.flush()

    return {
        "imported_count": len(imported),
        "software_count": len(moved_to_software),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "imported": imported[:30],
        "moved_to_software": moved_to_software[:30],
        "skipped": skipped[:20],
        "errors": errors[:20],
    }


def collect_current_prices_for_existing_itad_games(
    db, limit=100, country="KR", offset=0, game_ids=None
):
    query = db.query(Game).filter(Game.itad_id.isnot(None))
    if game_ids is not None:
        query = query.filter(Game.id.in_(game_ids))
    games = query.order_by(Game.id.asc()).offset(offset).limit(limit).all()

    if not games:
        return {"updated_count": 0, "skipped_count": 0, "updated": [], "skipped": []}

    itad_ids = [game.itad_id for game in games]
    game_map = {game.itad_id: game for game in games}

    data = itad_request(
        "POST",
        "/games/prices/v3",
        params={
            "country": country,
            "vouchers": True,
            "capacity": 0,
            "deals": False,
        },
        json_body=itad_ids,
    )

    updated = []
    skipped = []

    for item in data:
        game = game_map.get(item.get("id"))

        if game is None:
            continue

        # Steam 직접 수집과 같은 잠금 순서로 갱신합니다.
        db.query(Game).filter(Game.id == game.id).with_for_update().first()
        deals = item.get("deals") or []

        # 이번 응답에 없는 이전 할인은 더 이상 현재 할인으로 남기지 않습니다.
        previous_prices = db.query(PriceSnapshot).filter(
            PriceSnapshot.game_id == game.id,
            PriceSnapshot.is_current.is_(True),
            PriceSnapshot.platform.has(Platform.itad_shop_id.isnot(None)),
        )
        # Steam 앱 직접 응답으로 방금 확인한 가격은 ITAD 누락으로 해제하지 않음.
        if game.steam_appid:
            previous_prices = previous_prices.filter(
                PriceSnapshot.platform.has(Platform.name != "Steam")
            )
        if game.epic_offer_id:
            previous_prices = previous_prices.filter(PriceSnapshot.source != "epic")
        previous_prices.update(
            {PriceSnapshot.is_current: False}, synchronize_session=False
        )

        if is_explicit_adult_itad_item(
            {
                "title": game.title,
                "slug": game.itad_slug,
                "description": game.description,
                "tags": [tag.name for tag in game.tags],
            }
        ):
            db.query(PriceSnapshot).filter(
                PriceSnapshot.game_id == game.id, PriceSnapshot.is_current.is_(True)
            ).update({PriceSnapshot.is_current: False}, synchronize_session=False)
            skipped.append({"title": game.title, "reason": "노골적인 성인 콘텐츠"})
            continue

        if not deals:
            skipped.append({"title": game.title, "reason": "현재 가격 없음"})
            continue

        for deal in deals:
            shop = deal.get("shop") or {}
            if reason := deal_exclusion_reason(deal):
                skipped.append(
                    {
                        "title": game.title,
                        "platform": shop.get("name"),
                        "reason": reason,
                    }
                )
                continue
            price = deal.get("price")
            regular = deal.get("regular")

            discounted_price = money_to_int(price)
            original_price = money_to_int(regular)
            currency = (price or {}).get("currency") or "KRW"

            if discounted_price <= 0:
                continue

            result = update_game_price_and_notify(
                db=db,
                game_id=game.id,
                platform_name=shop.get("name") or "Unknown",
                itad_shop_id=shop.get("id"),
                discounted_price=discounted_price,
                original_price=original_price or discounted_price,
                currency=currency,
                url=deal.get("url") or game.store_url or "#",
                collected_at=utc_now(),
                sale_end_at=parse_optional_datetime(deal.get("expiry")),
                source="itad-current",
            )

            updated.append(result)

    return {
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "updated": updated[:30],
        "skipped": skipped[:30],
    }


def collect_history_for_existing_itad_games(
    db,
    limit=30,
    country="KR",
    days=None,
    offset=0,
    due_only=False,
    game_ids=None,
    claim_token=None,
):
    """최초에는 ITAD 제공 전체 이력, 이후에는 마지막 성공일 이후 변경분을 수집.

    성공일/시도일을 따로 보존하여 실패한 게임도 순환하며, 처음 N개만 반복하지 않습니다.
    deal=null은 판매 중단 사건으로 저장하고 차트에서 선을 끊습니다.
    """
    query = db.query(Game).filter(Game.itad_id.isnot(None))
    if game_ids is not None:
        query = query.filter(Game.id.in_(game_ids))
    lease_free = or_(
        Game.history_lease_until.is_(None), Game.history_lease_until <= utc_now()
    )
    if claim_token:
        lease_free = or_(lease_free, Game.history_lease_token == claim_token)
    query = query.filter(lease_free)
    if due_only:
        query = query.filter(
            (Game.history_synced_at.is_(None))
            | (Game.history_synced_at < utc_now() - timedelta(hours=20))
        )
        query = query.filter(
            (Game.history_attempted_at.is_(None))
            | (Game.history_attempted_at < utc_now() - timedelta(minutes=30))
        )
    games = (
        query.order_by(Game.history_attempted_at.asc().nullsfirst(), Game.id.asc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    jobs = []
    for game in games:
        full_start = datetime(2005, 1, 1)
        full_needed = (
            not game.history_synced_at
            or not game.history_start_at
            or game.history_start_at > full_start
        )
        since_at = (
            (utc_now() - timedelta(days=days))
            if days
            else (
                full_start
                if full_needed
                else game.history_synced_at - timedelta(days=1)
            )
        )
        jobs.append((game.id, game.itad_id, since_at))

    def fetch(job):
        gid, itad_id, since_at = job
        try:
            response = itad_request(
                "GET",
                "/games/history/v2",
                params={
                    "id": itad_id,
                    "country": country,
                    "since": since_at.replace(tzinfo=timezone.utc)
                    .isoformat()
                    .replace("+00:00", "Z"),
                },
            )
            if not isinstance(response, (list, dict)):
                raise ValueError("잘못된 히스토리 응답")
            if isinstance(response, dict) and not any(
                isinstance(response.get(key), list)
                for key in ["list", "data", "history", "items", "rows"]
            ):
                raise ValueError("이력 목록이 없는 오류 응답")
            return gid, since_at, normalize_history_response(response), None, None
        except Exception as error:
            return gid, since_at, [], collection_error_detail(error), error

    result = {
        "history_rows_imported": 0,
        "updated_count": 0,
        "games_synced": 0,
        "skipped_count": 0,
        "error_count": 0,
        "errors": [],
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        for gid, since_at, rows, error, exception in pool.map(fetch, jobs):
            game = db.query(Game).filter(Game.id == gid).with_for_update().first()
            if game is None:
                continue
            if claim_token and game.history_lease_token != claim_token:
                continue  # 초기화/잠금 만료 뒤 도착한 오래된 응답은 버립니다.
            game.history_attempted_at = utc_now()
            if error:
                game.history_error = f"ITAD 이력 응답 확인 실패: {error}"
                status = getattr(exception, "status_code", None)
                delay = (86400 if status in {400, 404} else
                         max(60, getattr(exception, "retry_after", 0)) if status in {401, 403, 429, 500, 502, 503, 504}
                         else 1800)
                game.history_retry_at = utc_now() + timedelta(seconds=delay)
                result["error_count"] += 1
                result["errors"].append(
                    {"title": game.title, "error": game.history_error}
                )
                continue
            malformed = 0
            seen_events = set()
            rows = sorted(
                rows,
                key=lambda row: (
                    str(row.get("timestamp", "")) if isinstance(row, dict) else ""
                ),
            )
            for row in rows:
                if not isinstance(row, dict):
                    malformed += 1
                    continue
                timestamp = parse_optional_datetime(row.get("timestamp"))
                shop = row.get("shop")
                if timestamp is None or not isinstance(shop, (dict, int)) or not shop:
                    malformed += 1
                    continue
                deal = row.get("deal")
                available = isinstance(deal, dict)
                if available and not deal.get("price"):
                    malformed += 1
                    continue
                platform = get_or_create_platform(db, shop)
                currency = ((deal or {}).get("price") or {}).get("currency") or "KRW"
                excluded = (
                    available
                    and excessive_discount_reason(
                        platform.name,
                        platform.itad_shop_id,
                        money_to_int(deal.get("regular")),
                        money_to_int(deal.get("price")),
                        deal.get("cut"),
                    )
                    is not None
                )
                if excluded:
                    available = False
                    result["skipped_count"] += 1
                if not available and not excluded:
                    previous = (
                        db.query(PriceSnapshot)
                        .filter(
                            PriceSnapshot.game_id == gid,
                            PriceSnapshot.platform_id == platform.id,
                            PriceSnapshot.is_available.is_(True),
                        )
                        .order_by(PriceSnapshot.collected_at.desc())
                        .first()
                    )
                    currency = previous.currency if previous else currency
                price = (
                    money_to_int((deal or {}).get("price")) if available else Decimal(0)
                )
                regular = (
                    money_to_int((deal or {}).get("regular"))
                    if available
                    else Decimal(0)
                )
                # 항상 무료인 상품은 제외하되 유료 게임의 실제 무료 배포 이력은 보존.
                if available and price == 0 and regular == 0:
                    result["skipped_count"] += 1
                    continue
                exists = (
                    db.query(PriceSnapshot.id)
                    .filter(
                        PriceSnapshot.game_id == gid,
                        PriceSnapshot.platform_id == platform.id,
                        PriceSnapshot.collected_at == timestamp,
                        PriceSnapshot.currency == currency,
                        PriceSnapshot.source == "itad-history",
                    )
                    .first()
                )
                event_key = (platform.id, timestamp, currency)
                if exists or event_key in seen_events:
                    continue
                seen_events.add(event_key)
                db.add(
                    PriceSnapshot(
                        game_id=gid,
                        platform_id=platform.id,
                        currency=currency,
                        original_price=regular or price,
                        discounted_price=price,
                        discount_rate=((deal or {}).get("cut") or 0)
                        if available
                        else 0,
                        url=None,
                        collected_at=timestamp,
                        is_current=False,
                        source="itad-history",
                        is_available=available,
                    )
                )
                result["history_rows_imported"] += 1
            if malformed:
                game.history_error = (
                    f"형식이 잘못된 이력 {malformed}건 제외; 다음 갱신에 재시도"
                )
                result["error_count"] += 1
                result["errors"].append(
                    {"title": game.title, "error": game.history_error}
                )
            else:
                game.history_synced_at = utc_now()
                game.history_start_at = (
                    min(game.history_start_at, since_at)
                    if game.history_start_at
                    else since_at
                )
                game.history_error = None
                game.history_retry_at = None
                result["games_synced"] += 1
            db.flush()
    result["updated_count"] = result["history_rows_imported"]
    result["errors"] = result["errors"][:20]
    return result
