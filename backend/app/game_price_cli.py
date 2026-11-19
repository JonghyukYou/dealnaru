"""가격만 초기화/재수집: python -m app.game_price_cli reset --apply | collect"""

import argparse
import json

from app.database import Base, SessionLocal, engine
from app.models import Game
from app.services import itad_collect_service as itad
from app.services.catalog_sync_service import link_catalog_to_itad
from app.services.epic_collect_service import collect_existing_epic_prices
from app.services.game_price_reset_service import reset_game_prices
from app.services.price_policy_service import cleanup_excessive_discounts
from app.services.scheduler_service import run_logged_collection
from app.services.schema_service import upgrade_subscription_schema
from app.services.steam_collect_service import collect_existing_steam_prices


def collect_existing_game_prices(country="KR", full_history=False):
    """기존 게임에만 가격을 붙입니다. 신규 인기 게임/프로그램/중고 매물을 수집하지 않습니다."""
    results = []

    def step(platform, label, callback):
        print(f"수집 중: {label}", flush=True)
        result = run_logged_collection(platform, label, callback)
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    with SessionLocal() as db:
        cleanup_excessive_discounts(db, apply=True)
        db.commit()
        steam_count = db.query(Game).filter(Game.steam_appid.isnot(None)).count()
        epic_count = db.query(Game).filter(Game.epic_offer_id.isnot(None)).count()
        unmatched = [
            g.id
            for g in db.query(Game).filter(Game.itad_id.is_(None)).order_by(Game.id)
        ]
    for offset in range(0, steam_count, 100):
        step(
            "Steam",
            f"기존 Steam 가격 {offset + 1}~",
            lambda db, offset=offset: collect_existing_steam_prices(
                db,
                max_items=100,
                offset=offset,
                country=country.lower(),
                delay_seconds=0.1,
            ),
        )
    for offset in range(0, epic_count, 80):
        step(
            "Epic Game Store",
            f"기존 Epic 가격 {offset + 1}~",
            lambda db, offset=offset: collect_existing_epic_prices(
                db, limit=80, offset=offset, country=country
            ),
        )
    if not itad.ITAD_API_KEY:
        print(
            "ITAD_API_KEY 미설정: 다른 구매처 가격과 과거 이력은 수집하지 못했습니다.",
            flush=True,
        )
        return {"steps": results, "history_available": False}
    for offset in range(0, len(unmatched), 80):
        ids = unmatched[offset : offset + 80]
        step(
            "IsThereAnyDeal",
            f"기존 게임 ITAD 연결 {offset + 1}~",
            lambda db, ids=ids: link_catalog_to_itad(db, limit=80, game_ids=ids),
        )
    with SessionLocal() as db:
        ids = [
            g.id
            for g in db.query(Game).filter(Game.itad_id.isnot(None)).order_by(Game.id)
        ]
        if full_history:
            db.query(Game).filter(Game.id.in_(ids)).update(
                {Game.history_start_at: None}, synchronize_session=False
            )
            db.commit()
    for offset in range(0, len(ids), 200):
        batch = ids[offset : offset + 200]
        step(
            "IsThereAnyDeal",
            f"기존 게임 모든 구매처 현재가 {offset + 1}~",
            lambda db, batch=batch: itad.collect_current_prices_for_existing_itad_games(
                db, limit=200, country=country, game_ids=batch
            ),
        )
    for offset in range(0, len(ids), 20):
        batch = ids[offset : offset + 20]
        step(
            "IsThereAnyDeal",
            f"기존 게임 실제 과거 이력 {offset + 1}~",
            lambda db, batch=batch: itad.collect_history_for_existing_itad_games(
                db, limit=20, country=country, game_ids=batch
            ),
        )
    return {"steps": results, "history_available": True}


def main():
    parser = argparse.ArgumentParser(
        description="게임 가격만 초기화하거나 기존 게임의 현재가·과거 이력을 재수집합니다."
    )
    parser.add_argument("action", choices=["reset", "collect"])
    parser.add_argument(
        "--apply", action="store_true", help="reset 미리보기를 실제 적용"
    )
    parser.add_argument("--country", default="KR", help="수집 국가 코드 (기본 KR)")
    parser.add_argument(
        "--full-history",
        action="store_true",
        help="과거 이력을 최초 시점부터 다시 보강",
    )
    args = parser.parse_args()
    if args.apply and args.action != "reset":
        parser.error("--apply는 reset에서만 사용합니다.")
    upgrade_subscription_schema(engine, metadata=Base.metadata)
    if args.action == "reset":
        with SessionLocal() as db:
            result = reset_game_prices(db, apply=args.apply)
            if args.apply:
                db.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        print(
            "가격 초기화 완료. 게임·계정·찜·알림·프로그램·중고 매물은 유지했습니다."
            if args.apply
            else "미리보기입니다. 실제 초기화는 reset --apply로 실행하세요."
        )
    else:
        result = collect_existing_game_prices(args.country.upper(), args.full_history)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
