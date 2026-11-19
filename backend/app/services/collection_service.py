from datetime import datetime, timedelta

from app.models import (
    Game,
    Notification,
    Platform,
    PriceSnapshot,
    WishlistItem,
)
from app.services.email_service import send_target_price_email
from app.services.price_policy_service import excessive_discount_reason
from app.utils.time import utc_now


def to_int(value):
    if value is None:
        return 0

    return int(value)


def calculate_discount_rate(original_price: int, discounted_price: int) -> float:
    if original_price <= 0:
        return 0

    return round((1 - discounted_price / original_price) * 100, 2)


def update_game_price_and_notify(
    db,
    game_id: int,
    platform_name: str,
    discounted_price: int,
    original_price: int | None = None,
    url: str = "#",
    currency: str = "KRW",
    collected_at: datetime | None = None,
    itad_shop_id: int | None = None,
    sale_end_at: datetime | None = None,
    source: str = "observed",
):
    # 동시 갱신 시 현재가와 알림을 같은 게임 행 잠금으로 직렬화합니다.
    game = db.query(Game).filter(Game.id == game_id).with_for_update().first()

    if game is None:
        raise ValueError("게임을 찾을 수 없습니다.")

    platform = (
        db.query(Platform).filter(Platform.itad_shop_id == itad_shop_id).first()
        if itad_shop_id is not None
        else None
    )
    if platform is None:
        platform = db.query(Platform).filter(Platform.name == platform_name).first()

    if platform is None:
        platform = Platform(
            name=platform_name,
            website=None,
            itad_shop_id=itad_shop_id,
        )
        db.add(platform)
        db.flush()
    else:
        if itad_shop_id is not None and platform.itad_shop_id is None:
            platform.itad_shop_id = itad_shop_id

    current_price = (
        db.query(PriceSnapshot)
        .filter(
            PriceSnapshot.game_id == game_id,
            PriceSnapshot.platform_id == platform.id,
            PriceSnapshot.currency == currency,
            PriceSnapshot.is_current.is_(True),
        )
        .first()
    )

    if current_price:
        final_original_price = original_price or current_price.original_price
        current_price.is_current = False
    else:
        final_original_price = original_price or discounted_price

    discount_rate = calculate_discount_rate(
        final_original_price,
        discounted_price,
    )
    if reason := excessive_discount_reason(
        platform.name, platform.itad_shop_id, final_original_price, discounted_price
    ):
        raise ValueError(reason)

    new_price = PriceSnapshot(
        game_id=game.id,
        platform_id=platform.id,
        currency=currency or "KRW",
        original_price=final_original_price,
        discounted_price=discounted_price,
        discount_rate=discount_rate,
        url=url or "#",
        collected_at=collected_at or utc_now(),
        sale_end_at=sale_end_at,
        is_current=True,
        source=source,
        is_available=True,
    )

    db.add(new_price)
    db.flush()

    wishlist_items = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.game_id == game.id,
            WishlistItem.notify_enabled.is_(True),
        )
        .all()
    )

    notifications_created = 0
    emails_sent = 0
    email_errors = 0

    recent_threshold = utc_now() - timedelta(hours=24)

    for item in wishlist_items:
        # 위시리스트 목표가는 KRW. 외화의 숫자를 원화 목표가와 비교하지 않습니다.
        if currency != "KRW":
            continue
        if item.target_price is None:
            continue

        if discounted_price > item.target_price:
            continue

        recent_notification = (
            db.query(Notification)
            .filter(
                Notification.wishlist_item_id == item.id,
                Notification.type == "TARGET_PRICE",
                Notification.created_at >= recent_threshold,
            )
            .first()
        )

        if recent_notification:
            continue

        notification = Notification(
            user_id=item.user_id,
            wishlist_item_id=item.id,
            type="TARGET_PRICE",
            title=f"{game.title} 목표가 도달!",
            message=(
                f"{game.title}의 {platform.name} 가격이 "
                f"{discounted_price:,}원으로 내려갔습니다. "
                f"설정한 목표가 {item.target_price:,}원 이하입니다."
            ),
            is_read=False,
        )

        db.add(notification)
        db.flush()

        notifications_created += 1

        user = item.user

        if user and user.email_verified and user.email_notifications_enabled:
            try:
                send_target_price_email(
                    to=user.email,
                    nickname=user.nickname,
                    game_title=game.title,
                    platform_name=platform.name,
                    discounted_price=discounted_price,
                    target_price=item.target_price,
                    game_url=url,
                )

                notification.email_sent_at = utc_now()
                emails_sent += 1

            except Exception as error:
                notification.email_send_error = f"메일 전송 실패 ({type(error).__name__})"
                email_errors += 1

    return {
        "game_id": game.id,
        "game_title": game.title,
        "platform": platform.name,
        "original_price": final_original_price,
        "discounted_price": discounted_price,
        "discount_rate": discount_rate,
        "notifications_created": notifications_created,
        "emails_sent": emails_sent,
        "email_errors": email_errors,
    }
