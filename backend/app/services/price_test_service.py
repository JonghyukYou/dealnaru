"""실제 수집값을 보존하면서 화면과 찜 알림을 시험합니다."""
from datetime import timedelta

from sqlalchemy.orm import joinedload

from app.models import CrawlingLog, Game, GamePriceTest, Platform, PriceSnapshot, User, WishlistItem
from app.services.collection_service import calculate_discount_rate, notify_wishlist_targets
from app.services.price_freshness_service import is_fresh_current_price
from app.services.price_policy_service import snapshot_is_allowed
from app.settings import FRONTEND_BASE_URL
from app.utils.time import to_utc_iso_z, utc_now


def active_price_tests(game):
    return [row for row in game.price_tests if row.is_current]


def effective_prices(game, observed):
    tests = active_price_tests(game)
    replaced = {(row.platform_id, row.currency) for row in tests}
    return [row for row in observed if (row.platform_id, row.currency) not in replaced] + tests


def format_price_test(row):
    return {"id": row.id, "game_id": row.game_id, "game_title": row.game.title,
            "platform": row.platform.name, "discounted_price": int(row.discounted_price),
            "original_price": int(row.original_price), "currency": row.currency,
            "collected_at": to_utc_iso_z(row.collected_at), "expires_at": to_utc_iso_z(row.expires_at),
            "ended_at": to_utc_iso_z(row.ended_at), "active": row.is_current, "is_test_price": True}


def create_price_test(db, *, admin, game_id, platform_name, discounted_price,
                      original_price=None, duration_minutes=60, recipient_email=None, send_email=False):
    game = db.query(Game).filter(Game.id == game_id).with_for_update().first()
    if game is None:
        raise LookupError("게임을 찾을 수 없습니다.")
    platform = db.query(Platform).filter_by(name=platform_name).first()
    if platform is None:
        raise ValueError("등록된 구매처를 선택해 주세요.")
    base = db.query(PriceSnapshot).filter_by(game_id=game_id, platform_id=platform.id,
        currency="KRW", is_current=True, is_available=True).order_by(PriceSnapshot.collected_at.desc()).first()
    if base is None or not is_fresh_current_price(base) or not snapshot_is_allowed(base):
        raise ValueError("이 구매처의 원화 관측 가격이 없습니다. 원화 가격이 있는 구매처를 선택해 주세요.")
    original = int(base.original_price) if original_price is None else original_price
    if discounted_price > original:
        raise ValueError("테스트 가격은 원가 이하여야 합니다.")
    recipient = admin
    if recipient_email:
        recipient = db.query(User).filter(User.email == recipient_email.strip().lower()).first()
        if recipient is None:
            raise ValueError("알림을 받을 테스트 계정이 없습니다. 가입한 계정의 이메일을 입력해 주세요.")
    now = utc_now()
    for old in db.query(GamePriceTest).filter_by(game_id=game_id, platform_id=platform.id, active=True).all():
        old.active = False
        old.ended_at = now
    row = GamePriceTest(game_id=game_id, platform_id=platform.id, created_by_id=admin.id,
        recipient_user_id=recipient.id, original_price=original, discounted_price=discounted_price,
        discount_rate=calculate_discount_rate(original, discounted_price), currency="KRW", url=base.url,
        collected_at=now, expires_at=now + timedelta(minutes=duration_minutes), active=True)
    db.add(row)
    db.flush()
    notification = notify_wishlist_targets(db, game=game, platform=platform,
        discounted_price=discounted_price, recipient_user_id=recipient.id, is_test=True, send_mail=send_email,
        url=f"{FRONTEND_BASE_URL}/games/{game.id}")
    item = db.query(WishlistItem).filter_by(game_id=game_id, user_id=recipient.id).first()
    if item is None:
        reason = "선택 계정의 찜 목록에 이 게임이 없어 알림을 만들지 않았습니다."
    elif not item.notify_enabled:
        reason = "선택 계정의 찜 알림이 꺼져 있습니다."
    elif item.target_price is None:
        reason = "선택 계정의 찜 목표 가격을 먼저 저장해 주세요."
    elif discounted_price > item.target_price:
        reason = f"테스트 가격이 목표가 {item.target_price:,}원보다 높아 알림 조건을 충족하지 않았습니다."
    else:
        reason = "선택 계정의 찜 목표가 조건을 충족했습니다."
    if send_email and notification["notifications_created"] and not notification["emails_sent"] and not notification["email_errors"]:
        reason += " 이메일은 계정의 인증·메일 수신 설정을 확인해 주세요."
    db.add(CrawlingLog(status="SUCCESS", end_time=utc_now(), rows_count=1,
        message=f"관리자 테스트용 가격: {game.title} · {platform.name} · {discounted_price:,}원 · 알림 {notification['notifications_created']}개"))
    db.flush()
    return {"test": format_price_test(row), **notification, "notification_reason": reason,
            "recipient_nickname": recipient.nickname, "message": "테스트용 가격을 적용했습니다. 실제 관측 가격은 보존됩니다."}


def stop_price_test(db, test_id):
    # 생성과 동일하게 게임 행부터 잠가 생성/해제가 서로 뒤엉키지 않게 합니다.
    row = db.get(GamePriceTest, test_id)
    if row is None:
        raise LookupError("가격 테스트를 찾을 수 없습니다.")
    db.query(Game).filter_by(id=row.game_id).with_for_update().one()
    row.active = False
    row.ended_at = row.ended_at or utc_now()
    db.flush()
    return {"message": "테스트 가격을 해제했습니다. 최신 실제 수집 가격을 표시합니다.", "test": format_price_test(row)}


def list_price_tests(db):
    return [format_price_test(row) for row in db.query(GamePriceTest)
            .options(joinedload(GamePriceTest.game), joinedload(GamePriceTest.platform))
            .order_by((GamePriceTest.active & (GamePriceTest.expires_at > utc_now())).desc(),
                      GamePriceTest.collected_at.desc(), GamePriceTest.id.desc()).limit(50)]
