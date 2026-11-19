"""관리자가 선택한 본편 한 개의 메타데이터·현재가·과거 이력을 보강합니다."""

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse
from uuid import UUID

import requests
from sqlalchemy import or_

from app.models import Game
from app.services import itad_collect_service as itad
from app.services.catalog_filter_service import non_game_reason
from app.services.catalog_sync_service import link_catalog_to_itad, normalized_title
from app.services.content_filter_service import is_explicit_adult_itad_item
from app.services.history_sync_service import queue_history_sync
from app.services.steam_collect_service import (
    HEADERS,
    fetch_steam_app_details,
    upsert_steam_game,
    steam_request,
)
from app.services.source_request_service import collection_error_detail


def steam_reference(query):
    text = query.strip()
    if text.isdigit():
        appid = int(text)
    elif "://" in text:
        parsed = urlparse(text)
        match = re.match(r"^/app/(\d+)(?:/|$)", parsed.path)
        if (
            parsed.scheme not in {"http", "https"}
            or parsed.hostname != "store.steampowered.com"
            or not match
        ):
            raise ValueError("Steam 상점의 /app/ 링크 또는 게임 이름을 입력해주세요.")
        appid = int(match[1])
    else:
        return None
    if not 0 < appid < 2**31:
        raise ValueError("올바른 Steam 앱 ID를 입력해주세요.")
    return appid


def safe_source_error(source, error):
    return {
        "source": source,
        "message": f"{source} 연결 확인: {collection_error_detail(error)}",
    }


def steam_candidate(data):
    return {
        "title": data["title"],
        "steam_appid": data["steam_appid"],
        "itad_id": None,
        "image": data.get("header_image"),
        "sources": ["Steam"],
        "store_url": data.get("store_url"),
    }


def search_steam_titles(query):
    response = steam_request(
        "https://store.steampowered.com/api/storesearch/",
        params={"term": query, "cc": "kr", "l": "koreana"},
        headers=HEADERS,
        timeout=15,
    )
    response.raise_for_status()
    data = response.json()
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Steam 검색 응답 형식 오류")
    candidates = []
    for item in data["items"][:20]:
        if not isinstance(item, dict) or item.get("type") not in {None, "app"}:
            continue
        title = item.get("name") or ""
        if not str(item.get("id", "")).isdigit() or not title:
            continue
        if non_game_reason(title) or is_explicit_adult_itad_item({"title": title}):
            continue
        price = item.get("price")
        if isinstance(price, dict) and price.get("initial") == 0:
            continue
        appid = int(item["id"])
        candidates.append(
            {
                "title": title,
                "steam_appid": appid,
                "itad_id": None,
                "image": item.get("tiny_image"),
                "sources": ["Steam"],
                "store_url": f"https://store.steampowered.com/app/{appid}/",
            }
        )
    return candidates


def search_itad_title(query):
    if not itad.ITAD_API_KEY:
        return []
    lookup = itad.itad_request("GET", "/games/lookup/v1", params={"title": query})
    candidate = lookup.get("game") or {}
    if not lookup.get("found") or not candidate.get("id"):
        return []
    info = itad.fetch_itad_game_info(candidate["id"])
    title = info.get("title") or ""
    if (
        info.get("type") != "game"
        or non_game_reason(title, info.get("tags") or [], info.get("type"))
        or is_explicit_adult_itad_item(info, info)
    ):
        return []
    if normalized_title(query) not in normalized_title(title):
        return []
    return [
        {
            "title": title,
            "itad_id": info["id"],
            "steam_appid": info.get("appid"),
            "image": itad.get_best_asset(info.get("assets")),
            "sources": ["IsThereAnyDeal"],
            "store_url": f"https://isthereanydeal.com/game/{info.get('slug')}/",
        }
    ]


def search_requested_games(db, query):
    query = query.strip()
    if not query:
        raise ValueError("게임 이름이나 Steam 링크를 입력해주세요.")
    appid = steam_reference(query)
    warnings, candidates = [], []
    if appid:
        data = fetch_steam_app_details(appid, include_reason=True)
        if not data or data.get("_skip_reason"):
            raise ValueError(
                f"수집할 수 없는 상품: {(data or {}).get('_skip_reason', '게임 정보 없음')}"
            )
        candidates = [steam_candidate(data)]
    else:
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [
                ("Steam", pool.submit(search_steam_titles, query)),
                ("IsThereAnyDeal", pool.submit(search_itad_title, query)),
            ]
            for source, job in jobs:
                try:
                    candidates.extend(job.result())
                except Exception as error:
                    warnings.append(safe_source_error(source, error))
    merged = {}
    for candidate in candidates:
        key = (
            ("steam", candidate["steam_appid"])
            if candidate.get("steam_appid")
            else ("itad", candidate["itad_id"])
        )
        if key in merged:
            existing = merged[key]
            existing["sources"] = sorted(
                set(existing["sources"] + candidate["sources"])
            )
            existing["itad_id"] = existing.get("itad_id") or candidate.get("itad_id")
            existing["image"] = existing.get("image") or candidate.get("image")
        else:
            merged[key] = candidate
    candidates = sorted(
        merged.values(),
        key=lambda item: normalized_title(item["title"]) != normalized_title(query),
    )[:10]
    for candidate in candidates:
        matches = []
        if candidate.get("steam_appid"):
            matches.append(Game.steam_appid == int(candidate["steam_appid"]))
        if candidate.get("itad_id"):
            matches.append(Game.itad_id == candidate["itad_id"])
        game = db.query(Game).filter(or_(*matches)).first() if matches else None
        candidate["existing_game_id"] = game.id if game else None
    return {
        "query": query,
        "candidates": candidates,
        "warnings": warnings,
        "itad_available": bool(itad.ITAD_API_KEY),
    }


def collect_requested_game(db, steam_appid=None, itad_id=None):
    if not steam_appid and not itad_id:
        raise ValueError("수집할 검색 결과를 선택해주세요.")
    warnings = []
    requested_info = None
    if not steam_appid:
        if not itad.ITAD_API_KEY:
            raise ValueError(
                "ITAD 전용 게임을 수집하려면 ITAD_API_KEY 설정이 필요합니다."
            )
        try:
            itad_id = str(UUID(itad_id))
        except (ValueError, TypeError, AttributeError) as error:
            raise ValueError("올바른 ITAD 게임 ID가 아닙니다.") from error
        requested_info = itad.fetch_itad_game_info(itad_id)
        if (
            requested_info.get("type") != "game"
            or non_game_reason(
                requested_info.get("title"),
                requested_info.get("tags") or [],
                requested_info.get("type"),
            )
            or is_explicit_adult_itad_item(requested_info, requested_info)
        ):
            raise ValueError(
                "번들·추가 콘텐츠·노골적인 성인용 상품은 수집하지 않습니다."
            )
        steam_appid = requested_info.get("appid")
    before = db.query(Game).count()
    if steam_appid:
        data = fetch_steam_app_details(int(steam_appid), include_reason=True)
        if not data or data.get("_skip_reason"):
            raise ValueError(
                f"수집할 수 없는 상품: {(data or {}).get('_skip_reason', '게임 정보 없음')}"
            )
        row = upsert_steam_game(db, data)
        game = db.get(Game, row["game_id"])
    else:
        game = itad.upsert_game_from_itad_item(db, requested_info, info=requested_info)
        if game is None:
            raise ValueError("본편 게임 정보를 확인할 수 없습니다.")
    if not game.catalog_source:
        game.catalog_source = "관리자 지정 수집"
    db.flush()
    created = db.query(Game).count() > before
    # Steam 단계는 독립적으로 보존. ITAD 장애 때문에 방금 수집한 게임을 잃지 않습니다.
    db.commit()
    gid = game.id
    price_rows = 1 if steam_appid else 0
    if itad.ITAD_API_KEY:
        try:
            if not game.itad_id:
                game.itad_lookup_checked_at = None
                db.flush()
                linked = link_catalog_to_itad(db, limit=1, game_ids=[gid])
                db.commit()
                if linked.get("error_count"):
                    warnings.append(
                        {
                            "source": "IsThereAnyDeal",
                            "message": "게임 연결을 다시 확인해야 합니다.",
                        }
                    )
            db.expire_all()
            game = (
                db.query(Game).filter(Game.steam_appid == steam_appid).first()
                if steam_appid
                else db.get(Game, gid)
            )
            if game is None:
                raise ValueError("게임 병합 결과를 확인할 수 없습니다.")
            gid = game.id
            if game.itad_id:
                prices = itad.collect_current_prices_for_existing_itad_games(
                    db, limit=1, game_ids=[gid]
                )
                price_rows += prices["updated_count"]
                queued = queue_history_sync(db, gid, priority=2)
                db.commit()
            else:
                queued = {"queued_count": 0}
                warnings.append(
                    {
                        "source": "IsThereAnyDeal",
                        "message": "이 게임에 대응하는 가격 이력을 찾지 못했습니다. Steam 정보는 등록했습니다.",
                    }
                )
        except Exception as error:
            db.rollback()
            warnings.append(safe_source_error("IsThereAnyDeal", error))
            queued = {"queued_count": 0}
    else:
        queued = {"queued_count": 0}
        warnings.append(
            {
                "source": "IsThereAnyDeal",
                "message": "ITAD 연결 설정이 없어 Steam 정보만 등록했습니다.",
            }
        )
    db.expire_all()
    game = db.get(Game, gid)
    if game is None:
        raise ValueError(
            "본편 게임의 메타데이터를 확인할 수 없습니다. 해당 상품은 등록하지 않았습니다."
        )
    return {
        "game_id": gid,
        "title": game.title,
        "steam_appid": game.steam_appid,
        "itad_id": game.itad_id,
        "created": created,
        "updated_count": price_rows,
        "history_queued": bool(queued["queued_count"] or game.history_queued_at),
        "warnings": warnings,
    }
