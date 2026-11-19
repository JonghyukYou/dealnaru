import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal

import requests
from bs4 import BeautifulSoup
from sqlalchemy import func

from app.models import Game, GameTag, Platform, PriceSnapshot
from app.services.catalog_filter_service import non_game_reason
from app.services.collection_service import update_game_price_and_notify
from app.services.content_filter_service import is_explicit_adult_steam_game

STEAM_FEATURED_URL = "https://store.steampowered.com/api/featuredcategories"
STEAM_APPDETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_SEARCH_URL = "https://store.steampowered.com/search/results/"


HEADERS = {
    "User-Agent": "GameDiscountPredictor-Capstone/1.0",
    "Accept": "application/json",
}


def steam_price_to_krw(value):
    """
    Steam Store API의 price_overview initial/final 값은 보통 100을 곱한 정수 형태입니다.
    예: 6600000 -> 66000원
    """
    if value is None:
        return 0

    return int(round(int(value) / 100))


def get_or_create_steam_platform(db):
    platform = db.query(Platform).filter(Platform.name == "Steam").first()

    if platform is None:
        platform = Platform(
            name="Steam",
            website="https://store.steampowered.com",
        )
        db.add(platform)
        db.flush()

    return platform


def fetch_steam_featured_appids(country="kr", language="korean"):
    response = requests.get(
        STEAM_FEATURED_URL,
        params={
            "cc": country,
            "l": language,
        },
        headers=HEADERS,
        timeout=15,
    )

    response.raise_for_status()
    data = response.json()

    appids = []

    category_names = [
        "specials",
        "top_sellers",
        "new_releases",
    ]

    for category_name in category_names:
        category = data.get(category_name) or {}
        items = category.get("items") or []

        for item in items:
            appid = item.get("id") or item.get("appid")

            if appid and appid not in appids:
                appids.append(appid)

    return appids


def extract_steam_search_appids(data):
    """Steam의 HTML 응답과 새 items JSON 응답을 모두 처리합니다."""
    appids = []
    items = data.get("items") or []
    for item in items:
        appid = item.get("appid") or item.get("id")
        if not appid:
            # 새 검색 JSON은 ID 대신 공식 앱 썸네일 주소를 반환합니다.
            match = re.search(r"/steam/apps/(\d+)(?:/|$)", item.get("logo") or "")
            appid = int(match.group(1)) if match else None
        if appid and str(appid).isdigit():
            appids.append(int(appid))
    rows = BeautifulSoup(data.get("results_html") or "", "html.parser").select("a[data-ds-appid]")
    for row in rows:
        value = row.get("data-ds-appid", "")
        if value.isdigit() and "/app/" in row.get("href", ""):
            appids.append(int(value))
    return list(dict.fromkeys(appids)), max(len(items), len(rows))


def fetch_steam_search_appids(country="kr", language="korean", count=100):
    """추천 목록의 적은 후보 수를 Steam 할인 검색으로 보완합니다."""
    appids = []
    for start in range(0, min(count, 400), 100):
        response = requests.get(
            STEAM_SEARCH_URL,
            params={"specials": 1, "category1": 998, "start": start,
                    "count": 100, "json": 1, "cc": country, "l": language, "ndl": 1},
            headers=HEADERS, timeout=15,
        )
        response.raise_for_status()
        page_ids, row_count = extract_steam_search_appids(response.json())
        appids.extend(page_ids)
        if row_count < 100:
            break
    return list(dict.fromkeys(appids))


def fetch_steam_popular_appids(country="kr", language="korean", count=100):
    """공식 인기 판매 검색. specials 조건을 넣지 않아 정상가 게임도 포함합니다."""
    appids = []
    for start in range(0, min(count, 400), 100):
        response = requests.get(STEAM_SEARCH_URL,
            params={"filter": "topsellers", "category1": 998, "start": start, "count": 100,
                    "json": 1, "cc": country, "l": language, "ndl": 1}, headers=HEADERS, timeout=15)
        response.raise_for_status()
        page_ids, row_count = extract_steam_search_appids(response.json())
        appids.extend(page_ids)
        if row_count < 100:
            break
    if not appids:
        response = requests.get("https://api.steampowered.com/ISteamChartsService/GetMostPlayedGames/v1/", headers=HEADERS, timeout=15)
        response.raise_for_status()
        appids = [int(row["appid"]) for row in (response.json().get("response") or {}).get("ranks") or [] if row.get("appid")]
    return list(dict.fromkeys(appids))


def fetch_steam_app_details(appid, country="kr", language="korean", include_reason=False):
    def skip(reason):
        return {"_skip_reason": reason} if include_reason else None

    response = requests.get(
        STEAM_APPDETAILS_URL,
        params={
            "appids": appid,
            "cc": country,
            "l": language,
        },
        headers=HEADERS,
        timeout=12,
    )

    response.raise_for_status()
    data = response.json()

    wrapper = data.get(str(appid))

    if not wrapper or not wrapper.get("success"):
        return skip("앱 정보 없음")

    details = wrapper.get("data") or {}

    if details.get("type") != "game":
        return skip("게임 타입이 아님")
    if non_game_reason(details.get("name"), [g.get("description") for g in details.get("genres") or []]):
        return skip("번들·비게임")

    if is_explicit_adult_steam_game(details):
        return skip("노골적인 성인 콘텐츠")

    if details.get("is_free"):
        return skip("무료 게임")

    price = details.get("price_overview")

    if not price:
        return skip("가격 정보 없음")

    discount_expiration = price.get("discount_expiration")
    sale_end_at = None

    if discount_expiration:
        sale_end_at = datetime.fromtimestamp(
            int(discount_expiration),
            tz=timezone.utc,
        ).replace(tzinfo=None)

    currency = price.get("currency") or "KRW"
    original_price = steam_price_to_krw(price.get("initial")) if currency == "KRW" else Decimal(price.get("initial") or 0) / 100
    discounted_price = steam_price_to_krw(price.get("final")) if currency == "KRW" else Decimal(price.get("final") or 0) / 100
    discount_rate = int(price.get("discount_percent") or 0)

    if original_price <= 0 or discounted_price <= 0:
        return skip("유효한 유료 가격 없음")

    name = details.get("name") or f"Steam App {appid}"

    genres = details.get("genres") or []
    genre_names = [
        genre.get("description") for genre in genres if genre.get("description")
    ]

    developers = details.get("developers") or []
    publishers = details.get("publishers") or []

    release_date = details.get("release_date") or {}

    return {
        "steam_appid": int(appid),
        "title": name,
        "genre": genre_names[0] if genre_names else "기타",
        "tags": genre_names[:4] if genre_names else ["기타"],
        "developer": ", ".join(developers) if developers else None,
        "publisher": ", ".join(publishers) if publishers else None,
        "release_date": release_date.get("date"),
        "description": details.get("short_description"),
        "header_image": details.get("header_image"),
        "store_url": f"https://store.steampowered.com/app/{appid}",
        "original_price": original_price,
        "discounted_price": discounted_price,
        "discount_rate": discount_rate,
        "currency": price.get("currency") or "KRW",
        "review_count": (details.get("recommendations") or {}).get("total", 0),
        "sale_end_at": sale_end_at,
    }


def upsert_steam_game(db, steam_data):
    """
    Steam appid 기준으로 게임을 찾고,
    없으면 제목 기준으로 기존 샘플 게임과 매칭합니다.
    그래도 없으면 새 게임을 만듭니다.
    """
    steam_appid = steam_data["steam_appid"]
    title = steam_data["title"]

    game = db.query(Game).filter(Game.steam_appid == steam_appid).first()

    if game is None:
        game = db.query(Game).filter(func.lower(Game.title) == title.lower()).first()

    if game is None:
        game = Game(
            title=title,
            image_color="#1f2933",
            rating="Steam 수집",
            review_count=0,
            korean_support="정보 확인 필요",
        )
        db.add(game)
        db.flush()

    game.steam_appid = steam_appid
    game.product_type = "game"
    game.review_count = steam_data.get("review_count") or game.review_count or 0
    game.title = title
    game.genre = steam_data["genre"]
    game.developer = steam_data["developer"]
    game.publisher = steam_data["publisher"]
    game.release_date = steam_data["release_date"]
    game.description = steam_data["description"]
    game.header_image = steam_data["header_image"]
    game.store_url = steam_data["store_url"]

    existing_tags = {tag.name for tag in game.tags}

    for tag_name in steam_data["tags"]:
        if tag_name not in existing_tags:
            db.add(GameTag(game=game, name=tag_name))
            existing_tags.add(tag_name)

    db.flush()

    update_result = update_game_price_and_notify(
        db=db,
        game_id=game.id,
        platform_name="Steam",
        original_price=steam_data["original_price"],
        discounted_price=steam_data["discounted_price"],
        url=steam_data["store_url"],
        sale_end_at=steam_data.get("sale_end_at"),
        currency=steam_data.get("currency") or "KRW",
        source="steam",
    )

    return {
        "game_id": game.id,
        "steam_appid": steam_appid,
        "title": title,
        "discounted_price": steam_data["discounted_price"],
        "discount_rate": steam_data["discount_rate"],
        "notifications_created": update_result["notifications_created"],
    }


def collect_steam_featured_games(
    db,
    max_items=30,
    country="kr",
    language="korean",
    delay_seconds=0.1,
    catalog_mode=False,
):
    get_or_create_steam_platform(db)

    imported = []
    skipped = []
    errors = []
    source_warnings = []
    appids = []
    if catalog_mode:
        # 사용자가 요청한 Isaac 원작/리버스는 현재 할인 여부와 무관하게 먼저 확인.
        appids.extend([250900, 113200])
        try:
            appids.extend(fetch_steam_popular_appids(country, language, count=max_items * 3))
        except requests.RequestException:
            source_warnings.append("Steam 인기 판매 목록 접속 실패")
    else:
        try:
            appids.extend(fetch_steam_featured_appids(country=country, language=language))
        except requests.RequestException:
            source_warnings.append("Steam 추천 목록 실패; 할인 검색으로 대체")
        try:
            appids.extend(fetch_steam_search_appids(country, language, count=max_items * 3))
        except requests.RequestException:
            source_warnings.append("Steam 할인 검색 실패; 추천 후보만 검사")
    appids = list(dict.fromkeys(appids))[:max(50, max_items * 4)]
    if not appids:
        raise RuntimeError("Steam에서 게임 후보를 받지 못했습니다. 잠시 뒤 다시 시도해주세요.")

    def fetch(appid):
        try:
            return appid, fetch_steam_app_details(appid, country, language, include_reason=True), None
        except Exception as error:
            return appid, None, str(error)

    scanned = 0
    deadline = time.monotonic() + 100
    # HTTP만 최대 4개 병렬 실행. SQLAlchemy 세션 쓰기는 현재 스레드에서 수행.
    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(appids), 4):
            if len(imported) >= max_items or time.monotonic() >= deadline:
                break
            for appid, steam_data, error in pool.map(fetch, appids[start:start + 4]):
                if len(imported) >= max_items:
                    break
                scanned += 1
                if error:
                    errors.append({"appid": appid, "error": error})
                    continue
                if steam_data is None or "_skip_reason" in steam_data:
                    skipped.append({"appid": appid, "reason": (steam_data or {}).get("_skip_reason", "게임 정보 없음")})
                    continue
                try:
                    with db.begin_nested():
                        imported.append(upsert_steam_game(db, steam_data))
                        if catalog_mode:
                            game = db.get(Game, imported[-1]["game_id"])
                            game.catalog_source = "요청 게임" if appid in {250900, 113200} else "Steam 인기 게임"
                            game.catalog_rank = appids.index(appid) - 1 if appid not in {250900, 113200} else None
                except Exception as error:
                    errors.append({"appid": appid, "error": str(error)})
            if delay_seconds:
                time.sleep(delay_seconds)

    return {
        "imported_count": len(imported),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "imported": imported,
        "skipped": skipped[:10],
        "errors": errors[:10],
        "scanned_count": scanned,
        "requested_count": max_items,
        "skip_reasons": dict(Counter(item["reason"] for item in skipped)),
        "source_warnings": source_warnings,
    }


def collect_existing_steam_prices(
    db,
    max_items=50,
    country="kr",
    language="korean",
    delay_seconds=0.4,
    offset=0,
):
    get_or_create_steam_platform(db)

    games = (
        db.query(Game)
        .filter(Game.steam_appid.isnot(None))
        .order_by(Game.id.asc())
        .offset(offset)
        .limit(max_items)
        .all()
    )

    updated = []
    skipped = []
    errors = []

    def fetch(game):
        try:
            return game, fetch_steam_app_details(game.steam_appid, country, language, include_reason=True), None
        except Exception as error:
            return game, None, str(error)

    with ThreadPoolExecutor(max_workers=4) as pool:
        for start in range(0, len(games), 4):
            for game, steam_data, error in pool.map(fetch, games[start:start + 4]):
                if error:
                    errors.append({"game_id": game.id, "error": error})
                    continue
                if steam_data is None or "_skip_reason" in steam_data:
                    reason = (steam_data or {}).get("_skip_reason", "게임 정보 없음")
                    skipped.append({"game_id": game.id, "steam_appid": game.steam_appid, "reason": reason})
                    if reason in {"무료 게임", "게임 타입이 아님", "노골적인 성인 콘텐츠"}:
                        db.query(PriceSnapshot).filter(
                            PriceSnapshot.game_id == game.id, PriceSnapshot.is_current.is_(True),
                            PriceSnapshot.platform.has(name="Steam"),
                        ).update({PriceSnapshot.is_current: False}, synchronize_session=False)
                    continue
                try:
                    with db.begin_nested():
                        updated.append(upsert_steam_game(db, steam_data))
                except Exception as error:
                    errors.append({"game_id": game.id, "error": str(error)})
            if delay_seconds:
                time.sleep(delay_seconds)

    return {
        "updated_count": len(updated),
        "skipped_count": len(skipped),
        "error_count": len(errors),
        "updated": updated,
        "skipped": skipped[:10],
        "errors": errors[:10],
    }
