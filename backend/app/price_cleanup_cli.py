import argparse

from app.database import SessionLocal
from app.services.price_freshness_service import (
    deactivate_stale_current_prices,
    find_stale_current_prices,
)


def format_price(price) -> str:
    game_title = price.game.title if price.game else "게임 정보 없음"
    platform_name = price.platform.name if price.platform else "플랫폼 정보 없음"
    collected_at = price.collected_at.strftime("%Y-%m-%d %H:%M")
    discount_rate = int(price.discount_rate or 0)

    return (
        f"[{price.id}] {game_title} / {platform_name} / "
        f"-{discount_rate}% / 수집 {collected_at} UTC"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "오래된 현재가를 가격 이력으로 보존하면서 현재 할인 목록에서 제외합니다."
        )
    )
    parser.add_argument(
        "--days",
        type=int,
        default=7,
        help=(
            "할인 종료 시각이 없는 가격에서, 마지막 수집 후 며칠이 지나면 "
            "오래된 것으로 볼지 지정합니다."
        ),
    )
    parser.add_argument(
        "--title",
        default="",
        help="특정 게임 제목만 확인할 때 사용합니다. 예: Detroit",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="미리보기 결과를 실제로 현재 할인 목록에서 제외합니다.",
    )
    args = parser.parse_args()

    if args.days < 1:
        parser.error("--days는 1 이상이어야 합니다.")

    db = SessionLocal()
    max_age_hours = args.days * 24

    try:
        stale_prices = find_stale_current_prices(
            db,
            max_age_hours=max_age_hours,
            title=args.title,
        )

        if not stale_prices:
            print(
                f"종료된 할인 또는 종료 시각 없이 {args.days}일보다 오래된 "
                "현재가가 없습니다."
            )
            return

        print(f"제외 대상 현재가: {len(stale_prices)}건")
        for price in stale_prices:
            print(f"- {format_price(price)}")

        if not args.apply:
            print("\n미리보기만 수행했습니다. DB는 변경하지 않았습니다.")
            print("확인 후 같은 명령 끝에 --apply를 추가하세요.")
            return

        deactivate_stale_current_prices(
            db,
            max_age_hours=max_age_hours,
            title=args.title,
            commit=True,
        )
        print(
            f"\n{len(stale_prices)}건을 현재 할인에서 제외했습니다. "
            "가격 이력은 삭제하지 않았습니다."
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
