import os
from datetime import datetime, timedelta

from sqlalchemy import and_, or_
from sqlalchemy.orm import joinedload

from app.models import Game, PriceSnapshot
from app.utils.time import utc_now

DEFAULT_CURRENT_PRICE_MAX_AGE_HOURS = 168


def get_current_price_max_age_hours() -> int:
    raw_value = os.getenv(
        "CURRENT_PRICE_MAX_AGE_HOURS",
        str(DEFAULT_CURRENT_PRICE_MAX_AGE_HOURS),
    )

    try:
        return max(1, int(raw_value))
    except (TypeError, ValueError):
        return DEFAULT_CURRENT_PRICE_MAX_AGE_HOURS


def is_fresh_current_price(
    price: PriceSnapshot,
    *,
    now: datetime | None = None,
    max_age_hours: int | None = None,
) -> bool:
    if not price.is_current or price.collected_at is None:
        return False

    reference_time = now or utc_now()
    freshness_hours = max_age_hours or get_current_price_max_age_hours()
    cutoff = reference_time - timedelta(hours=freshness_hours)

    # 할인 종료 시각을 제공하는 스토어는 그 시각을 우선합니다. 따라서 한 달짜리
    # 할인은 중간에 재수집되지 않았더라도 종료 전까지 유효하게 표시됩니다.
    if price.sale_end_at is not None:
        return price.sale_end_at >= reference_time

    # 종료 시각을 알 수 없는 가격만 마지막 수집 시각을 안전장치로 사용합니다.
    if price.collected_at < cutoff:
        return False

    return True


def find_stale_current_prices(
    db,
    *,
    max_age_hours: int | None = None,
    title: str | None = None,
    now: datetime | None = None,
):
    reference_time = now or utc_now()
    freshness_hours = max_age_hours or get_current_price_max_age_hours()
    cutoff = reference_time - timedelta(hours=freshness_hours)

    query = (
        db.query(PriceSnapshot)
        .options(
            joinedload(PriceSnapshot.game),
            joinedload(PriceSnapshot.platform),
        )
        .filter(
            PriceSnapshot.is_current.is_(True),
            or_(
                and_(
                    PriceSnapshot.sale_end_at.is_(None),
                    PriceSnapshot.collected_at < cutoff,
                ),
                and_(
                    PriceSnapshot.sale_end_at.isnot(None),
                    PriceSnapshot.sale_end_at < reference_time,
                ),
            ),
        )
    )

    if title and title.strip():
        query = query.join(PriceSnapshot.game).filter(
            Game.title.ilike(f"%{title.strip()}%")
        )

    return query.order_by(PriceSnapshot.collected_at.asc()).all()


def deactivate_stale_current_prices(
    db,
    *,
    max_age_hours: int | None = None,
    title: str | None = None,
    now: datetime | None = None,
    commit: bool = False,
):
    stale_prices = find_stale_current_prices(
        db,
        max_age_hours=max_age_hours,
        title=title,
        now=now,
    )

    for price in stale_prices:
        price.is_current = False

    if commit:
        db.commit()
    else:
        db.flush()

    return stale_prices
