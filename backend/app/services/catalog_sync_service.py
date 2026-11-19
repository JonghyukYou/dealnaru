"""Steam/Epic 본편 카탈로그를 ITAD에 연결. 중복 게임은 가격/찜/알림을 보존하며 합칩니다."""

import re
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta

from sqlalchemy import or_

from app.models import Game, PriceSnapshot
from app.services import itad_collect_service as itad
from app.services.catalog_filter_service import non_game_reason
from app.services.content_filter_service import is_explicit_adult_itad_item
from app.utils.time import utc_now


def normalized_title(title):
    value = unicodedata.normalize("NFKC", title or "").casefold()
    return re.sub(r"[^\w]", "", value).replace("™", "").replace("®", "")


def merge_game_records(db, target, duplicate):
    if target.id == duplicate.id:
        return target
    for price in list(duplicate.prices):
        price.game = target
    tag_names = {tag.name for tag in target.tags}
    for tag in list(duplicate.tags):
        if tag.name not in tag_names:
            tag.game = target
            tag_names.add(tag.name)
    for item in list(duplicate.wishlist_items):
        existing = next(
            (row for row in target.wishlist_items if row.user_id == item.user_id), None
        )
        if existing:
            for notification in list(item.notifications):
                notification.wishlist_item = existing
            if existing.target_price is None:
                existing.target_price = item.target_price
            existing.notify_enabled = existing.notify_enabled or item.notify_enabled
            item.game = None
            db.delete(item)
        else:
            item.game = target
    for attr in ("prediction", "used_market"):
        row = getattr(duplicate, attr)
        if row is not None and getattr(target, attr) is None:
            row.game = target
    for attr in (
        "itad_slug",
        "epic_namespace",
        "epic_offer_id",
        "epic_slug",
        "header_image",
        "developer",
        "publisher",
        "release_date",
        "genre",
        "history_synced_at",
        "history_start_at",
        "history_queued_at",
    ):
        if not getattr(target, attr):
            setattr(target, attr, getattr(duplicate, attr))
    target.review_count = max(target.review_count or 0, duplicate.review_count or 0)
    target.history_priority = max(
        target.history_priority or 0, duplicate.history_priority or 0
    )
    linked_id, appid = duplicate.itad_id, duplicate.steam_appid
    duplicate.itad_id = None
    duplicate.steam_appid = None
    db.flush()
    target.itad_id = target.itad_id or linked_id
    target.steam_appid = target.steam_appid or appid
    db.flush()
    db.delete(duplicate)
    db.flush()
    # 병합 전 서로 현재였던 행 중 구매처/통화별 최신 관측만 현재로 남깁니다.
    current = (
        db.query(PriceSnapshot)
        .filter(PriceSnapshot.game_id == target.id, PriceSnapshot.is_current.is_(True))
        .order_by(PriceSnapshot.collected_at.desc(), PriceSnapshot.id.desc())
        .all()
    )
    seen = set()
    for row in current:
        key = (row.platform_id, row.currency)
        if key in seen:
            row.is_current = False
        seen.add(key)
    return target


def link_catalog_to_itad(db, limit=80, game_ids=None):
    cutoff = utc_now() - timedelta(days=1)
    query = db.query(Game).filter(
        Game.itad_id.is_(None),
        or_(
            Game.itad_lookup_checked_at.is_(None),
            Game.itad_lookup_checked_at < cutoff,
        ),
    )
    if game_ids is not None:
        query = query.filter(Game.id.in_(game_ids))
    games = (
        query.order_by(Game.itad_lookup_checked_at.asc().nullsfirst(), Game.id)
        .limit(limit)
        .all()
    )
    if not games:
        return {"updated_count": 0, "skipped_count": 0, "error_count": 0}
    steam_games = [game for game in games if game.steam_appid]
    mapping = (
        itad.itad_request(
            "POST",
            "/lookup/id/shop/61/v1",
            json_body=[f"app/{game.steam_appid}" for game in steam_games],
        )
        if steam_games
        else {}
    )
    if not isinstance(mapping, dict):
        raise ValueError("ITAD Steam ID 연결 응답 형식 오류")
    jobs = [(game.id, game.title, game.steam_appid) for game in games]

    def fetch(job):
        gid, title, appid = job
        try:
            if appid:
                itad_id = mapping.get(f"app/{appid}")
                if not itad_id:
                    return gid, None, None
                info = itad.fetch_itad_game_info(itad_id)
            else:
                lookup = itad.itad_request(
                    "GET", "/games/lookup/v1", params={"title": title}
                )
                candidate = lookup.get("game") or {}
                if not lookup.get("found") or normalized_title(
                    candidate.get("title")
                ) != normalized_title(title):
                    return gid, None, None
                info = itad.fetch_itad_game_info(candidate["id"])
            return gid, info, None
        except Exception as error:
            return gid, None, type(error).__name__

    result = {"updated_count": 0, "skipped_count": 0, "error_count": 0, "errors": []}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for gid, info, error in pool.map(fetch, jobs):
            game = db.get(Game, gid)
            if game is None:
                continue
            game.itad_lookup_checked_at = utc_now()
            if error:
                result["error_count"] += 1
                result["errors"].append({"title": game.title, "error": error})
                continue
            if not info:
                result["skipped_count"] += 1
                continue
            if non_game_reason(
                info.get("title"), info.get("tags") or [], info.get("type")
            ) or is_explicit_adult_itad_item(info, info):
                db.delete(game)
                result["skipped_count"] += 1
                continue
            if not info.get("id"):
                result["error_count"] += 1
                continue
            with db.begin_nested():
                if info.get("appid"):
                    steam_match = (
                        db.query(Game)
                        .filter(Game.steam_appid == info["appid"], Game.id != game.id)
                        .first()
                    )
                    if steam_match:
                        game = merge_game_records(db, steam_match, game)
                duplicate = (
                    db.query(Game)
                    .filter(Game.itad_id == info["id"], Game.id != gid)
                    .first()
                )
                if duplicate and duplicate.id != game.id:
                    merge_game_records(db, game, duplicate)
                # 메타데이터 함수가 현재 게임을 찾도록 먼저 연결합니다.
                game.itad_id = info["id"]
                db.flush()
                itad.upsert_game_from_itad_item(db, info, info=info)
                result["updated_count"] += 1
    db.flush()
    return result


def enrich_itad_catalog_metadata(db, limit=80):
    cutoff = utc_now() - timedelta(days=1)
    games = (
        db.query(Game)
        .filter(
            Game.itad_id.isnot(None),
            Game.itad_info_checked_at.is_(None),
            or_(
                Game.itad_lookup_checked_at.is_(None),
                Game.itad_lookup_checked_at < cutoff,
            ),
        )
        .order_by(Game.id)
        .limit(limit)
        .all()
    )

    def fetch(game):
        try:
            return game.id, itad.fetch_itad_game_info(game.itad_id), None
        except Exception as error:
            return game.id, None, type(error).__name__

    result = {"updated_count": 0, "skipped_count": 0, "error_count": 0}
    with ThreadPoolExecutor(max_workers=4) as pool:
        for gid, info, error in pool.map(fetch, games):
            game = db.get(Game, gid)
            if not game:
                continue
            game.itad_lookup_checked_at = utc_now()
            if error or not info or not info.get("id"):
                result["error_count"] += 1
                continue
            if (
                info.get("type") != "game"
                or non_game_reason(
                    info.get("title"), info.get("tags") or [], info.get("type")
                )
                or is_explicit_adult_itad_item(info, info)
            ):
                db.delete(game)
                result["skipped_count"] += 1
                continue
            with db.begin_nested():
                if info.get("appid"):
                    existing = (
                        db.query(Game)
                        .filter(Game.steam_appid == info["appid"], Game.id != gid)
                        .first()
                    )
                    if existing:
                        game = merge_game_records(db, existing, game)
                itad.upsert_game_from_itad_item(db, info, info=info)
                result["updated_count"] += 1
    db.flush()
    return result
