from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base
from app.utils.time import utc_now


class Game(Base):
    __tablename__ = "games"

    id = Column(Integer, primary_key=True, index=True)
    itad_id = Column(String(80), unique=True, nullable=True, index=True)
    itad_slug = Column(String(255), nullable=True)
    title = Column(String(255), nullable=False, index=True)
    genre = Column(String(100), nullable=True)
    developer = Column(String(255), nullable=True)
    publisher = Column(String(255), nullable=True)
    release_date = Column(String(30), nullable=True)
    rating = Column(String(100), nullable=True)
    review_count = Column(Integer, default=0)
    korean_support = Column(String(100), nullable=True)
    description = Column(Text, nullable=True)
    image_color = Column(String(30), default="#333333")
    steam_appid = Column(Integer, unique=True, nullable=True, index=True)
    store_url = Column(Text, nullable=True)
    header_image = Column(Text, nullable=True)
    product_type = Column(String(30), nullable=True)
    catalog_source = Column(String(100), nullable=True)
    catalog_rank = Column(Integer, nullable=True)
    epic_namespace = Column(String(120), nullable=True)
    epic_offer_id = Column(String(120), nullable=True)
    epic_slug = Column(String(255), nullable=True)
    itad_lookup_checked_at = Column(DateTime, nullable=True)
    itad_info_checked_at = Column(DateTime, nullable=True)
    history_synced_at = Column(DateTime, nullable=True)
    history_attempted_at = Column(DateTime, nullable=True)
    history_start_at = Column(DateTime, nullable=True)
    history_error = Column(Text, nullable=True)
    history_retry_at = Column(DateTime, nullable=True)
    history_queued_at = Column(DateTime, nullable=True)
    history_priority = Column(Integer, nullable=False, default=0)
    history_lease_token = Column(String(80), nullable=True)
    history_lease_until = Column(DateTime, nullable=True)

    tags = relationship("GameTag", back_populates="game", cascade="all, delete-orphan")
    prices = relationship(
        "PriceSnapshot", back_populates="game", cascade="all, delete-orphan"
    )
    prediction = relationship(
        "PredictionResult",
        back_populates="game",
        uselist=False,
        cascade="all, delete-orphan",
    )
    used_market = relationship(
        "UsedMarketSummary",
        back_populates="game",
        uselist=False,
        cascade="all, delete-orphan",
    )
    wishlist_items = relationship(
        "WishlistItem", back_populates="game", cascade="all, delete-orphan"
    )


class GameTag(Base):
    __tablename__ = "game_tags"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    name = Column(String(100), nullable=False)

    game = relationship("Game", back_populates="tags")


class Platform(Base):
    __tablename__ = "platforms"

    id = Column(Integer, primary_key=True, index=True)
    itad_shop_id = Column(Integer, unique=True, nullable=True, index=True)
    name = Column(String(100), unique=True, nullable=False)
    website = Column(Text, nullable=True)

    prices = relationship("PriceSnapshot", back_populates="platform")
    logs = relationship("CrawlingLog", back_populates="platform")


class PriceSnapshot(Base):
    __tablename__ = "price_snapshots"
    __table_args__ = (
        Index("ix_price_snapshots_game_current", "game_id", "is_current"),
    )

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    platform_id = Column(Integer, ForeignKey("platforms.id"), nullable=False)

    currency = Column(String(10), default="KRW")
    original_price = Column(Numeric(12, 2), nullable=False)
    discounted_price = Column(Numeric(12, 2), nullable=False)
    discount_rate = Column(Numeric(5, 2), nullable=False)
    url = Column(Text, nullable=True)

    collected_at = Column(DateTime, nullable=False)
    sale_end_at = Column(DateTime, nullable=True)
    is_current = Column(Boolean, default=False)
    source = Column(String(30), nullable=False, default="observed")
    is_available = Column(Boolean, nullable=False, default=True)

    game = relationship("Game", back_populates="prices")
    platform = relationship("Platform", back_populates="prices")


class PredictionResult(Base):
    __tablename__ = "prediction_results"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)

    expected_date = Column(String(100), nullable=True)
    expected_event = Column(String(255), nullable=True)
    expected_discount_rate = Column(String(100), nullable=True)
    confidence = Column(String(30), nullable=True)
    reason = Column(Text, nullable=True)

    game = relationship("Game", back_populates="prediction")


class UsedMarketSummary(Base):
    __tablename__ = "used_market_summaries"

    id = Column(Integer, primary_key=True, index=True)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)

    digital_lowest = Column(Integer, default=0)
    digital_discount_rate = Column(Integer, default=0)
    joonggonara_average = Column(Integer, default=0)
    notice = Column(Text, nullable=True)

    game = relationship("Game", back_populates="used_market")


class UsedMarketItem(Base):
    __tablename__ = "used_market_items"
    __table_args__ = (
        UniqueConstraint("source", "item_url", name="uq_used_market_source_url"),
        Index("ix_used_market_items_collected_at", "collected_at"),
    )

    id = Column(Integer, primary_key=True, index=True)

    source = Column(String(50), default="중고나라", nullable=False)
    keyword = Column(String(100), nullable=False)

    title = Column(String(255), nullable=False)
    price = Column(Integer, nullable=True)

    location = Column(String(100), nullable=True)
    seller = Column(String(100), nullable=True)

    item_url = Column(Text, nullable=False)
    image_url = Column(Text, nullable=True)

    posted_at_text = Column(String(100), nullable=True)
    status = Column(String(50), default="판매중", nullable=False)

    collected_at = Column(DateTime, default=utc_now, nullable=False)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    password_hash = Column(String(255), nullable=False)
    nickname = Column(String(50), unique=True, nullable=False)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=utc_now)
    last_login = Column(DateTime, nullable=True)

    email_verified = Column(Boolean, default=False, nullable=False)
    email_verified_at = Column(DateTime, nullable=True)
    email_verify_token_hash = Column(String(64), nullable=True)
    email_verify_token_expires_at = Column(DateTime, nullable=True)
    email_verify_sent_at = Column(DateTime, nullable=True)
    email_notifications_enabled = Column(Boolean, default=True, nullable=False)
    terms_version = Column(String(20), nullable=True)
    terms_accepted_at = Column(DateTime, nullable=True)

    wishlist_items = relationship(
        "WishlistItem", back_populates="user", cascade="all, delete-orphan"
    )
    notifications = relationship(
        "Notification", back_populates="user", cascade="all, delete-orphan"
    )
    sessions = relationship("UserSession", cascade="all, delete-orphan")


class UserSession(Base):
    __tablename__ = "user_sessions"

    token_hash = Column(String(64), primary_key=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    expires_at = Column(DateTime, nullable=False, index=True)


class WishlistItem(Base):
    __tablename__ = "wishlist_items"
    __table_args__ = (
        UniqueConstraint("user_id", "game_id", name="uq_user_game_wishlist"),
    )

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    game_id = Column(Integer, ForeignKey("games.id"), nullable=False)
    target_price = Column(Integer, nullable=True)
    notify_enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="wishlist_items")
    game = relationship("Game", back_populates="wishlist_items")
    notifications = relationship(
        "Notification", back_populates="wishlist_item", cascade="all, delete-orphan"
    )


class Software(Base):
    __tablename__ = "software"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), nullable=False, index=True)
    category = Column(String(100), nullable=True)
    developer = Column(String(255), nullable=True)
    description = Column(Text, nullable=True)
    image_color = Column(String(30), default="#333333")
    official_url = Column(Text, nullable=True)
    pricing_type = Column(String(50), nullable=True)
    rating_label = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=utc_now)
    source_key = Column(String(80), nullable=True, unique=True)
    kind = Column(String(20), default="software", nullable=False)
    logo_url = Column(Text, nullable=True)

    promotions = relationship(
        "SoftwarePromotion", back_populates="software", cascade="all, delete-orphan"
    )
    plans = relationship(
        "SoftwarePlan", back_populates="software", cascade="all, delete-orphan"
    )


class SoftwarePlan(Base):
    __tablename__ = "software_plans"

    id = Column(Integer, primary_key=True)
    software_id = Column(Integer, ForeignKey("software.id"), nullable=False, index=True)
    source_key = Column(String(120), nullable=False, unique=True)
    name = Column(String(150), nullable=False)
    regular_price = Column(Numeric(12, 2), nullable=True)
    current_price = Column(Numeric(12, 2), nullable=True)
    currency = Column(String(10), nullable=False, default="USD")
    period = Column(String(20), nullable=False, default="month")
    billing_period = Column(String(100), nullable=True)
    commitment = Column(Text, nullable=True)
    eligibility = Column(Text, nullable=True)
    region = Column(String(120), nullable=True)
    unit = Column(String(30), nullable=False, default="계정")
    features = Column(Text, nullable=True)
    url = Column(Text, nullable=False)
    checked_at = Column(DateTime, nullable=True)
    source_status = Column(String(30), nullable=False, default="reference")
    last_error = Column(Text, nullable=True)
    is_current = Column(Boolean, nullable=False, default=True)

    software = relationship("Software", back_populates="plans")


class SoftwarePromotion(Base):
    __tablename__ = "software_promotions"

    id = Column(Integer, primary_key=True, index=True)
    software_id = Column(Integer, ForeignKey("software.id"), nullable=False)

    title = Column(String(255), nullable=False)
    platform = Column(String(100), nullable=True)
    promo_type = Column(String(100), nullable=True)
    original_price = Column(Numeric(12, 2), nullable=True)
    discounted_price = Column(Numeric(12, 2), nullable=True)
    discount_rate = Column(Numeric(5, 2), nullable=True)
    start_date = Column(String(30), nullable=True)
    end_date = Column(String(30), nullable=True)
    coupon_code = Column(String(100), nullable=True)
    url = Column(Text, nullable=True)
    description = Column(Text, nullable=True)
    is_current = Column(Boolean, default=True)
    created_at = Column(DateTime, default=utc_now)
    source_key = Column(String(120), nullable=True, unique=True)
    currency = Column(String(10), default="KRW")
    billing_period = Column(String(60), nullable=True)
    eligibility = Column(Text, nullable=True)
    region = Column(String(120), nullable=True)
    renewal_notice = Column(Text, nullable=True)
    checked_at = Column(DateTime, nullable=True)
    source_status = Column(String(30), default="manual")
    last_error = Column(Text, nullable=True)
    is_offer = Column(Boolean, default=True, nullable=False)

    software = relationship("Software", back_populates="promotions")


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    wishlist_item_id = Column(Integer, ForeignKey("wishlist_items.id"), nullable=True)

    type = Column(String(30), default="DISCOUNT")
    title = Column(String(255), nullable=False)
    message = Column(Text, nullable=False)
    is_read = Column(Boolean, default=False)

    email_sent_at = Column(DateTime, nullable=True)
    email_send_error = Column(Text, nullable=True)
    created_at = Column(DateTime, default=utc_now)

    user = relationship("User", back_populates="notifications")
    wishlist_item = relationship("WishlistItem", back_populates="notifications")


class CrawlingLog(Base):
    __tablename__ = "crawling_logs"

    id = Column(Integer, primary_key=True, index=True)
    platform_id = Column(Integer, ForeignKey("platforms.id"), nullable=True)

    status = Column(String(30), nullable=False, default="SUCCESS")
    start_time = Column(DateTime, nullable=False, default=utc_now)
    end_time = Column(DateTime, nullable=True)
    rows_count = Column(Integer, default=0)
    message = Column(Text, nullable=True)
    group_key = Column(String(120), nullable=True)
    repeat_count = Column(Integer, nullable=False, default=1)
    last_event_at = Column(DateTime, nullable=True)

    platform = relationship("Platform", back_populates="logs")


class CollectionSourceState(Base):
    """원본의 대기 시간을 DB에 보관해 서버 재시작으로 제한을 무시하지 않습니다."""

    __tablename__ = "collection_source_states"

    source = Column(String(80), primary_key=True)
    paused_until = Column(DateTime, nullable=True)
    http_status = Column(Integer, nullable=True)
    detail = Column(Text, nullable=True)
    min_interval = Column(Numeric(10, 2), nullable=True)
    updated_at = Column(DateTime, nullable=False, default=utc_now)


class AutoRefreshState(Base):
    """DB에 남는 일정과 임대 잠금. 여러 worker도 한 번만 수집합니다."""

    __tablename__ = "auto_refresh_state"

    id = Column(Integer, primary_key=True)
    enabled = Column(Boolean, nullable=False, default=True)
    interval_minutes = Column(Integer, nullable=False, default=360)
    include_software = Column(Boolean, nullable=False, default=True)
    new_games_per_source = Column(Integer, nullable=False, default=40)
    next_run_at = Column(DateTime, nullable=True)
    run_requested = Column(Boolean, nullable=False, default=False)
    last_started_at = Column(DateTime, nullable=True)
    last_finished_at = Column(DateTime, nullable=True)
    last_status = Column(String(30), nullable=False, default="IDLE")
    last_result = Column(Text, nullable=True)
    lease_token = Column(String(64), nullable=True)
    lease_until = Column(DateTime, nullable=True)
    catalog_synced_at = Column(DateTime, nullable=True)
