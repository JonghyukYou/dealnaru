"""구매처별 할인 제외 정책. 할인율을 판매처의 피싱 여부로 해석하지 않습니다."""

from collections import Counter
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import joinedload

from app.models import CrawlingLog, Platform, PredictionResult, PriceSnapshot
from app.utils.time import utc_now

MAX_RESELLER_DISCOUNT = Decimal("90")
OFFICIAL_SHOP_IDS = {4, 16, 35, 48, 52, 61, 62}
OFFICIAL_NAMES = {
    "steam",
    "epicgamestore",
    "epicgamesstore",
    "epicstore",
    "gog",
    "gog.com",
    "eastore",
    "eaapp",
    "origin",
    "ubisoftstore",
    "ubisoftconnect",
    "ubisoft",
    "blizzard",
    "battle.net",
    "microsoftstore",
    "xbox",
    "playstationstore",
    "nintendoeshop",
}


def decimal_value(value):
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def is_official_store(name, shop_id=None):
    if shop_id is not None:
        return shop_id in OFFICIAL_SHOP_IDS
    return (
        str(name or "").casefold().replace(" ", "").replace("-", "") in OFFICIAL_NAMES
    )


def excessive_discount_reason(
    name, shop_id=None, original_price=None, discounted_price=None, cut=None
):
    if is_official_store(name, shop_id):
        return None
    reported = decimal_value(cut)
    regular, price = decimal_value(original_price), decimal_value(discounted_price)
    # 보고 할인율과 실제 금액 비율을 모두 확인. 반올림 때문에 90% 경계를 바꾸지 않음.
    excessive = reported is not None and reported > MAX_RESELLER_DISCOUNT
    if regular is not None and regular > 0 and price is not None and price >= 0:
        excessive = excessive or price * 10 < regular
    return "주요 공식 스토어 외 90% 초과 할인" if excessive else None


def snapshot_is_allowed(price):
    platform = price.platform
    return (
        excessive_discount_reason(
            platform.name if platform else None,
            platform.itad_shop_id if platform else None,
            price.original_price,
            price.discounted_price,
            price.discount_rate,
        )
        is None
    )


def cleanup_excessive_discounts(db, apply=False):
    matches = [
        p
        for p in db.query(PriceSnapshot)
        .options(joinedload(PriceSnapshot.platform))
        .all()
        if p.is_available and not snapshot_is_allowed(p)
    ]
    by_store = dict(
        Counter(p.platform.name if p.platform else "구매처 미확인" for p in matches)
    )
    if apply and matches:
        affected = {p.game_id for p in matches}
        for price in matches:
            if price.source == "itad-history":
                # 제외 기간을 정상가/무료로 오해하지 않도록 값 대신 차트의 공백 사건만 유지.
                price.original_price = price.discounted_price = price.discount_rate = 0
                price.is_available = price.is_current = False
                price.url = None
            else:
                db.delete(price)
        db.query(PredictionResult).filter(
            PredictionResult.game_id.in_(affected)
        ).delete(synchronize_session=False)
        platform = db.query(Platform).filter(Platform.name == "IsThereAnyDeal").first()
        if platform is None:
            platform = Platform(name="IsThereAnyDeal")
            db.add(platform)
            db.flush()
        db.add(
            CrawlingLog(
                platform_id=platform.id,
                status="SUCCESS",
                end_time=utc_now(),
                rows_count=len(matches),
                message=f"과도한 할인 가격 {len(matches)}건 제외: {by_store}",
            )
        )
        db.flush()
    return {
        "matched_count": len(matches),
        "removed_count": len(matches) if apply else 0,
        "by_store": by_store,
        "applied": apply,
    }
