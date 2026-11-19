import os
import random
import logging
from contextlib import asynccontextmanager
from datetime import timedelta
from typing import Annotated, Optional

from email_validator import EmailNotValidError, validate_email
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_, text
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload, selectinload

from app.database import Base, SessionLocal, engine, get_db
from app.models import (
    AutoRefreshState,
    CrawlingLog,
    Game,
    GamePriceTest,
    GameTag,
    Notification,
    Platform,
    PredictionResult,
    PriceSnapshot,
    Software,
    SoftwarePromotion,
    UsedMarketItem,
    UsedMarketExclusion,
    UsedMarketSummary,
    User,
    WishlistItem,
)
from app.services.auth_service import (
    DUMMY_HASH,
    create_email_verification_token,
    end_session,
    get_current_admin_user,
    get_current_user,
    hash_email_verification_token,
    hash_password,
    password_needs_upgrade,
    start_session,
    verify_password,
)
from app.services.catalog_filter_service import cleanup_invalid_catalog
from app.services.collection_service import update_game_price_and_notify
from app.services.price_test_service import (
    active_price_tests, create_price_test, effective_prices, list_price_tests, stop_price_test,
)
from app.services.used_market_moderation_service import (
    delete_item as delete_market_item, delete_keyword as delete_market_keyword, visible_market_clause,
)
from app.services.email_service import send_verification_email
from app.services.history_sync_service import (
    history_status,
    initialize_history_worker,
    queue_history_sync,
    shutdown_history_worker,
)
from app.services.itad_collect_service import (
    collect_current_prices_for_existing_itad_games,
    collect_history_for_existing_itad_games,
    import_itad_deals,
    import_itad_popular_games,
)
from app.services.prediction_service import predict_discount_from_history
from app.services.price_freshness_service import is_fresh_current_price
from app.services.price_policy_service import (
    cleanup_excessive_discounts,
    excessive_discount_reason,
    snapshot_is_allowed,
)
from app.services.public_security import protect_public_api
from app.services.scheduler_service import (
    configure_scheduler,
    get_scheduler_status,
    initialize_scheduler,
    request_refresh,
    shutdown_scheduler,
)
from app.services.schema_service import upgrade_subscription_schema
from app.services.steam_collect_service import (
    collect_existing_steam_prices,
    collect_steam_featured_games,
)
from app.services.subscription_service import (
    format_subscription_card,
    format_subscription_promotion,
    refresh_subscription_offers,
    seed_subscription_catalog,
)
from app.services.targeted_game_service import (
    collect_requested_game,
    search_requested_games,
)
from app.services.used_market_collect_service import (
    collect_used_market_items,
    delete_stale_used_market_items,
    get_used_market_cutoff,
    build_search_url,
    repair_used_market_titles,
    remove_retired_market_data,
)
from app.utils.time import to_kst_iso, to_utc_iso_z, utc_now
from app.services.crawling_log_service import LOG_RETENTION_DAYS, prune_crawling_logs
from app.services.source_request_service import (
    SourceRequestError, collection_error_status, collection_result_status, collection_error_detail,
)
from app.services import steam_collect_service as steam_source
from app.services import itad_collect_service as itad_source
from app.settings import (
    ALLOWED_ORIGINS,
    EMAIL_VERIFICATION_REQUIRED,
    PRODUCTION,
    TERMS_VERSION,
    USED_MARKET_ENABLED,
    validate_production_settings,
)

logger = logging.getLogger(__name__)


def development_only():
    if PRODUCTION:
        raise HTTPException(status_code=404, detail="페이지를 찾을 수 없습니다.")


def require_source_available(gate):
    try:
        gate.ensure_available()
    except SourceRequestError as error:
        raise HTTPException(status_code=503, detail=str(error)) from error


def require_bulk_collection_idle(db):
    state = db.get(AutoRefreshState, 1)
    if state and (state.run_requested or (state.lease_until and state.lease_until > utc_now())):
        raise HTTPException(status_code=409, detail="전체 갱신이 진행 중이거나 대기 중입니다. 완료 후 수동 수집을 실행해 주세요.")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    validate_production_settings()
    upgrade_subscription_schema(engine, metadata=Base.metadata)

    # 기존 PostgreSQL에도 목록 조회용 인덱스를 안전하게 추가합니다.
    with engine.begin() as connection:
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_crawling_logs_start_time "
                "ON crawling_logs (start_time)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_price_snapshots_game_current "
                "ON price_snapshots (game_id, is_current)"
            )
        )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_used_market_items_collected_at "
                "ON used_market_items (collected_at)"
            )
        )

    cleanup_db = SessionLocal()
    try:
        prune_crawling_logs(cleanup_db)
        delete_stale_used_market_items(cleanup_db)
        remove_retired_market_data(cleanup_db)
        repair_used_market_titles(cleanup_db)
        cleanup_invalid_catalog(cleanup_db, apply=True)
        cleanup_excessive_discounts(cleanup_db, apply=True)
        cleanup_db.commit()
        if os.getenv("APP_ENV", "").lower() != "test":
            seed_subscription_catalog(cleanup_db)
    finally:
        cleanup_db.close()

    initialize_history_worker()
    initialize_scheduler()
    try:
        yield
    finally:
        shutdown_history_worker()
        shutdown_scheduler()


app = FastAPI(
    title="DealNaru API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url=None if PRODUCTION else "/docs",
    redoc_url=None if PRODUCTION else "/redoc",
    openapi_url=None if PRODUCTION else "/openapi.json",
)


@app.exception_handler(RequestValidationError)
async def handle_validation_error(_request: Request, error: RequestValidationError):
    # Do not echo input strings (including passwords and verification tokens).
    return JSONResponse(
        {"detail": [{k: item[k] for k in ("loc", "msg", "type")} for item in error.errors()]},
        status_code=422,
    )


def queue_price_enrichment(db):
    """수동 수집 뒤에는 부족한 이력만 예약하고 전체 갱신을 연쇄 실행하지 않습니다."""
    if os.getenv("APP_ENV", "").lower() != "test":
        queue_history_sync(db, include_due=False)
        db.commit()


@app.get("/api/admin/catalog/cleanup")
def preview_catalog_cleanup(
    _admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)
):
    return cleanup_invalid_catalog(db)


@app.post("/api/admin/catalog/cleanup")
def apply_catalog_cleanup(
    _admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)
):
    result = cleanup_invalid_catalog(db, apply=True)
    db.commit()
    return result


@app.post("/api/admin/catalog/sync", status_code=202)
def request_catalog_sync(
    _admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)
):
    try:
        status = request_refresh(db, force_catalog=True)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "message": "인기 본편·모든 구매처 가격·이력 보강을 예약했습니다. 수집 로그에서 진행 상황을 확인하세요.",
        "status": status,
    }


@app.post("/api/admin/history/sync", status_code=202)
def request_full_history_sync(
    _admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)
):
    result = queue_history_sync(db, force_full=True)
    if result["status"] == "unconfigured":
        raise HTTPException(
            status_code=503,
            detail="backend/.env에 ITAD_API_KEY를 설정하고 서버를 재시작하세요.",
        )
    db.commit()
    return {
        "message": f"{result['queued_count']}개 게임의 전체 과거 이력 보강을 예약했습니다.",
        **result,
        "status": get_scheduler_status(db),
    }


class SteamImportRequest(BaseModel):
    max_items: int = Field(default=30, ge=1, le=100)
    country: str = "kr"
    language: str = "korean"


class TargetedSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=200)


class TargetedImportRequest(BaseModel):
    steam_appid: Optional[int] = Field(default=None, ge=1, le=2**31 - 1)
    itad_id: Optional[str] = Field(default=None, max_length=80)


class AutoRefreshRequest(BaseModel):
    enabled: bool
    interval_minutes: int = Field(default=360, ge=30, le=10080)
    include_software: bool = True
    new_games_per_source: int = Field(default=40, ge=1, le=100)


class RegisterRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(min_length=8, max_length=128)
    nickname: str = Field(max_length=50)
    accept_terms: bool = Field(default=False, strict=True)
    age_confirmed: bool = Field(default=False, strict=True)


class ResendVerificationRequest(BaseModel):
    email: str = Field(max_length=255)


class LoginRequest(BaseModel):
    email: str = Field(max_length=255)
    password: str = Field(max_length=128)


class VerifyEmailRequest(BaseModel):
    token: str = Field(max_length=128)


class WithdrawRequest(BaseModel):
    password: str = Field(min_length=1, max_length=128)


class WishlistCreateRequest(BaseModel):
    target_price: Optional[int] = Field(default=None, strict=True, ge=0, le=2147483647)
    notify_enabled: bool = True


class WishlistUpdateRequest(BaseModel):
    target_price: Optional[int] = Field(default=None, strict=True, ge=0, le=2147483647)
    notify_enabled: bool = True


class PriceSimulationRequest(BaseModel):
    platform: str = "Steam"
    discounted_price: int = Field(ge=0)
    original_price: Optional[int] = Field(default=None, ge=0)


class ManualCollectRequest(BaseModel):
    game_id: int
    platform: str = "Steam"
    discounted_price: int = Field(ge=0)
    original_price: Optional[int] = Field(default=None, ge=0)


class ItadImportRequest(BaseModel):
    limit: int = Field(default=50, ge=1, le=500)
    offset: int = Field(default=0, ge=0)
    country: str = "KR"
    sort: str = "-cut"
    shop_ids: Optional[list[int]] = None


class ItadCollectRequest(BaseModel):
    limit: int = Field(default=100, ge=1, le=500)
    country: str = "KR"


class ItadHistoryRequest(BaseModel):
    limit: int = Field(default=30, ge=1, le=500)
    country: str = "KR"
    days: Optional[int] = Field(default=None, ge=1, le=7300)
    offset: int = Field(default=0, ge=0)


class UsedMarketCollectRequest(BaseModel):
    model_config = {"extra": "forbid"}
    limit_per_keyword: int = Field(default=5, ge=1, le=20)
    keywords: Optional[list[Annotated[str, Field(min_length=1, max_length=100)]]] = Field(default=None, max_length=10)


class AdminPriceTestRequest(BaseModel):
    model_config = {"extra": "forbid"}
    game_id: int = Field(strict=True, ge=1)
    platform: str = Field(min_length=1, max_length=100)
    discounted_price: int = Field(strict=True, ge=0, le=2147483647)
    original_price: Optional[int] = Field(default=None, strict=True, ge=0, le=2147483647)
    duration_minutes: int = Field(default=60, strict=True, ge=5, le=120)
    recipient_email: Optional[str] = Field(default=None, max_length=255)
    send_email: bool = False


app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(protect_public_api)


def format_user(user: User):
    return {
        "id": user.id,
        "email": user.email,
        "nickname": user.nickname,
        "is_admin": user.is_admin,
        "email_verified": user.email_verified,
        "email_notifications_enabled": user.email_notifications_enabled,
        "created_at": user.created_at.isoformat() if user.created_at else None,
        "last_login": user.last_login.isoformat() if user.last_login else None,
    }


def to_int(value):
    if value is None:
        return 0

    return int(value)


def calculate_discount_rate(original_price: int, discounted_price: int) -> float:
    if original_price <= 0:
        return 0

    return round((1 - discounted_price / original_price) * 100, 2)


def format_notification(notification: Notification):
    game = None

    if notification.wishlist_item and notification.wishlist_item.game:
        game = notification.wishlist_item.game

    return {
        "id": notification.id,
        "type": notification.type,
        "title": notification.title,
        "message": notification.message,
        "is_read": notification.is_read,
        "created_at": notification.created_at.isoformat()
        if notification.created_at
        else None,
        "email_sent_at": notification.email_sent_at.isoformat()
        if notification.email_sent_at
        else None,
        "email_send_error": notification.email_send_error,
        "game_id": game.id if game else None,
        "game_title": game.title if game else None,
    }


def format_crawling_log(log: CrawlingLog):
    duration_seconds = None

    if log.start_time and log.end_time and (log.repeat_count or 1) == 1:
        duration_seconds = round((log.end_time - log.start_time).total_seconds(), 2)

    platform_name = log.platform.name if log.platform else None

    # Earlier store-specific ITAD logs were saved without platform_id even though
    # their messages included the selected store. Keep those existing rows useful
    # without requiring a database migration.
    if platform_name is None:
        message = log.message or ""

        if "Fanatical" in message:
            platform_name = "Fanatical"
        elif "GOG" in message:
            platform_name = "GOG"
        elif "ITAD" in message or "IsThereAnyDeal" in message:
            platform_name = "IsThereAnyDeal"

    return {
        "id": log.id,
        "platform": platform_name or "알 수 없음",
        "status": log.status,
        "display_status": collection_error_status(log.message) if log.status == "FAIL" else log.status,
        "repeat_count": log.repeat_count or 1,
        "last_event_at": to_kst_iso(log.last_event_at),
        "start_time": to_kst_iso(log.start_time),
        "end_time": to_kst_iso(log.end_time),
        "duration_seconds": duration_seconds,
        "rows_count": log.rows_count,
        "message": log.message,
    }


def get_or_create_log_platform(db, name: str):
    platform = db.query(Platform).filter(Platform.name == name).first()

    if platform is None:
        platform = Platform(name=name)
        db.add(platform)
        db.flush()

    return platform


def format_used_market_item(item: UsedMarketItem):
    return {
        "id": item.id,
        "source": item.source,
        "keyword": item.keyword,
        "title": item.title,
        "price": item.price,
        "location": item.location,
        "item_url": item.item_url,
        "image_url": item.image_url,
        "posted_at_text": item.posted_at_text,
        "status": item.status,
        "collected_at": to_kst_iso(item.collected_at),
    }


def get_current_prices(game, *, include_tests=True):
    observed = [
        price
        for price in game.prices
        if price.is_available
        and is_fresh_current_price(price)
        and snapshot_is_allowed(price)
    ]
    return effective_prices(game, observed) if include_tests else observed


def get_lowest_current_price(game, *, include_tests=True):
    current_prices = get_current_prices(game, include_tests=include_tests)

    if not current_prices:
        return None

    return min(
        current_prices,
        key=lambda price: (
            price.currency != "KRW",
            price.currency != "USD",
            price.currency,
            price.discounted_price,
        ),
    )


def format_software_card(software):
    return format_subscription_card(software)


def format_software_detail(software):
    return {
        **format_subscription_card(software),
        "promotions": [
            format_subscription_promotion(p)
            for p in software.promotions
            if p.source_status != "demo"
        ],
    }


GENRE_ALIASES = {
    "전체": [],
    "액션": ["액션", "action"],
    "RPG": ["rpg", "롤플레잉", "role-playing", "role playing"],
    "어드벤쳐": ["어드벤쳐", "어드벤처", "adventure"],
    "전략": ["전략", "strategy"],
    "시뮬": ["시뮬", "시뮬레이션", "simulation"],
    "인디": ["인디", "indie"],
    "공포": ["공포", "horror"],
    "퍼즐": ["퍼즐", "puzzle"],
    "스포츠": ["스포츠", "sports"],
    "레이싱": ["레이싱", "racing"],
    "캐주얼": ["캐주얼", "casual"],
    "기타": ["기타"],
}


def normalize_text(value):
    if value is None:
        return ""

    return str(value).lower().replace(" ", "").replace("-", "").replace("_", "")


def normalize_platform_group(platform_name):
    value = normalize_text(platform_name)

    if "steam" in value:
        return "Steam"

    if "epic" in value:
        return "Epic Store"

    return "Other"


def get_current_platform_groups(game):
    groups = set()
    if game.steam_appid:
        groups.add("Steam")
    if game.epic_slug:
        groups.add("Epic Store")

    for price in get_current_prices(game):
        if price.platform:
            groups.add(normalize_platform_group(price.platform.name))

    return sorted(groups)


def game_matches_genre(game, selected_genre):
    if selected_genre == "전체":
        return True

    aliases = GENRE_ALIASES.get(selected_genre, [selected_genre])
    normalized_aliases = [normalize_text(alias) for alias in aliases]

    values = []

    if game.genre:
        values.append(game.genre)

    for tag in game.tags:
        values.append(tag.name)

    normalized_values = [normalize_text(value) for value in values]

    for value in normalized_values:
        for alias in normalized_aliases:
            if alias and (alias in value or value in alias):
                return True

    return False


def format_game_card(game, selected_price=None):
    lowest_price = selected_price or get_lowest_current_price(game)
    current_prices = get_current_prices(game)

    base_data = {
        "id": game.id,
        "steam_appid": game.steam_appid,
        "title": game.title,
        "genre": game.genre,
        "tags": [tag.name for tag in game.tags],
        "developer": game.developer,
        "publisher": game.publisher,
        "review_count": game.review_count or 0,
        "image_color": game.image_color,
        "store_url": game.store_url,
        "header_image": game.header_image,
        "catalog_source": game.catalog_source,
        "catalog_rank": game.catalog_rank,
        "platforms": [
            price.platform.name for price in current_prices if price.platform
        ],
        "platform_groups": get_current_platform_groups(game),
        "is_test_price": bool(lowest_price and lowest_price.source == "admin-test"),
        "test_price_expires_at": to_utc_iso_z(lowest_price.expires_at) if lowest_price and lowest_price.source == "admin-test" else None,
    }

    if lowest_price is None:
        return {
            **base_data,
            "platform": "가격 정보 없음",
            "original_price": None,
            "discounted_price": None,
            "discount_rate": 0,
            "has_price": False,
            "currency": "KRW",
            "price_status": "가격 확인 중",
            "sale_end_at": None,
        }

    platform_name = (
        lowest_price.platform.name if lowest_price.platform else "플랫폼 정보 없음"
    )

    return {
        **base_data,
        "platform": platform_name,
        "original_price": float(lowest_price.original_price),
        "discounted_price": float(lowest_price.discounted_price),
        "discount_rate": to_int(lowest_price.discount_rate),
        "has_price": True,
        "currency": lowest_price.currency,
        "price_status": "할인 중" if lowest_price.discount_rate > 0 else "정상가",
        "sale_end_at": to_utc_iso_z(lowest_price.sale_end_at),
    }


def load_games_query(db: Session):
    return db.query(Game).options(
        selectinload(Game.tags),
        selectinload(Game.price_tests).joinedload(GamePriceTest.platform),
        selectinload(Game.prices.and_(PriceSnapshot.is_current.is_(True))).joinedload(
            PriceSnapshot.platform
        ),
    )


def format_game_detail(game):
    current_prices = sorted(
        get_current_prices(game),
        key=lambda p: (
            p.currency != "KRW",
            p.currency != "USD",
            p.currency,
            p.discounted_price,
        ),
    )
    history_response = build_history_response(game)
    observed_current = get_lowest_current_price(game, include_tests=False)
    prediction_platform = observed_current.platform.name if observed_current and observed_current.platform else history_response["history_platform"]
    prediction_currency = observed_current.currency if observed_current else history_response["currency"]
    raw_history = [
        p
        for p in game.prices
        if p.platform
        and p.platform.name == prediction_platform
        and p.currency == prediction_currency
        and p.is_available
        and snapshot_is_allowed(p)
    ]
    raw_history.sort(key=lambda p: p.collected_at)
    used = game.used_market
    return {
        **format_game_card(game),
        "itad_id": game.itad_id,
        "itad_slug": game.itad_slug,
        "release_date": game.release_date,
        "rating": game.rating,
        "korean_support": game.korean_support,
        "description": game.description,
        "catalog_source": game.catalog_source,
        "prices": [
            {
                "platform": p.platform.name,
                "original_price": float(p.original_price),
                "discounted_price": float(p.discounted_price),
                "discount_rate": float(p.discount_rate),
                "currency": p.currency,
                "url": p.url,
                "collected_at": to_utc_iso_z(p.collected_at),
                "is_test_price": p.source == "admin-test",
                "test_price_expires_at": to_utc_iso_z(p.expires_at) if p.source == "admin-test" else None,
            }
            for p in current_prices
            if p.platform
        ],
        **history_response,
        "active_price_tests": [{"platform": p.platform.name, "discounted_price": int(p.discounted_price),
                                "expires_at": to_utc_iso_z(p.expires_at)} for p in active_price_tests(game)],
        "prediction": predict_discount_from_history(raw_history),
        "used_market": {
            "digital_lowest": used.digital_lowest,
            "digital_discount_rate": used.digital_discount_rate,
            "joonggonara_average": used.joonggonara_average,
            "notice": used.notice,
        }
        if used
        else {
            "digital_lowest": 0,
            "digital_discount_rate": 0,
            "joonggonara_average": 0,
            "notice": "중고 시세 데이터가 없습니다.",
        },
    }


def get_period_start(period: str):
    now = utc_now()

    if period == "1m":
        return now - timedelta(days=31)

    if period == "3m":
        return now - timedelta(days=93)

    if period == "6m":
        return now - timedelta(days=186)

    if period == "1y":
        return now - timedelta(days=365)

    return None


def build_history_response(
    game: Game, platform_name: str = "최저가", period: str = "1y", currency=None, *, include_test_current=True
):
    current = get_lowest_current_price(game, include_tests=include_test_current)
    available_platforms = sorted(
        {
            p.platform.name
            for p in game.prices
            if p.platform and p.is_available and snapshot_is_allowed(p)
        }
        | {p.platform.name for p in game.price_tests if p.platform}
    )
    selected = platform_name
    if selected == "최저가":
        selected = (
            current.platform.name
            if current and current.platform
            else (
                "Steam"
                if "Steam" in available_platforms
                else available_platforms[0]
                if available_platforms
                else "Steam"
            )
        )
    platform_prices = [
        p for p in game.prices if p.platform and p.platform.name == selected
    ]
    available_currencies = sorted(
        {
            p.currency
            for p in platform_prices
            if p.is_available and snapshot_is_allowed(p)
        }
        | {p.currency for p in game.price_tests if p.platform and p.platform.name == selected}
    )
    currency = currency or (
        "KRW"
        if "KRW" in available_currencies
        else available_currencies[0]
        if available_currencies
        else "KRW"
    )
    raw = sorted(
        [p for p in platform_prices if p.currency == currency or not p.is_available],
        key=lambda p: (p.collected_at, p.id),
    )
    # 관측 + ITAD 사건이 겹친 시각은 마지막 값을 사용합니다.
    raw = list({p.collected_at: p for p in raw}.values())
    start = get_period_start(period)
    before = [p for p in raw if start and p.collected_at < start]
    selected_prices = [p for p in raw if not start or p.collected_at >= start]
    history = []
    if before and start and before[-1].is_available and snapshot_is_allowed(before[-1]):
        anchor = before[-1]
        history.append(
            {
                "date": start.strftime("%Y-%m-%d"),
                "datetime": to_utc_iso_z(start),
                "price": float(anchor.discounted_price),
                "original_price": float(anchor.original_price),
                "discount_rate": float(anchor.discount_rate),
                "platform": selected,
                "currency": currency,
                "source": anchor.source,
                "is_anchor": True,
            }
        )
    for p in selected_prices:
        available = p.is_available and snapshot_is_allowed(p)
        history.append(
            {
                "date": p.collected_at.strftime("%Y-%m-%d"),
                "datetime": to_utc_iso_z(p.collected_at),
                "price": float(p.discounted_price) if available else None,
                "original_price": float(p.original_price) if available else None,
                "discount_rate": float(p.discount_rate) if available else None,
                "platform": selected,
                "currency": currency,
                "source": p.source,
                "is_anchor": False,
            }
        )
    priced = [row for row in history if row["price"] is not None]
    lowest = min(priced, key=lambda row: row["price"]) if priced else None
    events = []
    previous_cut = (
        float(before[-1].discount_rate)
        if before and before[-1].is_available and snapshot_is_allowed(before[-1])
        else 0
    )
    for p in selected_prices:
        cut = float(p.discount_rate) if p.is_available and snapshot_is_allowed(p) else 0
        if cut > 0 and previous_cut <= 0:
            events.append(cut)
        elif cut > 0 and events:
            events[-1] = max(events[-1], cut)
        previous_cut = cut
    stats = {
        "period": period,
        "lowest_price": lowest["price"] if lowest else None,
        "lowest_discount_rate": lowest["discount_rate"] if lowest else None,
        "discount_count": len(events),
        "average_discount_rate": round(sum(events) / len(events), 1) if events else 0,
    }
    return {
        "history_platform": selected,
        "available_platforms": available_platforms,
        "period": period,
        "currency": currency,
        "available_currencies": available_currencies,
        "history": history,
        "test_history": [{"id": p.id, "date": p.collected_at.strftime("%Y-%m-%d"),
                          "datetime": to_utc_iso_z(p.collected_at), "price": float(p.discounted_price),
                          "original_price": float(p.original_price), "platform": selected, "currency": currency,
                          "source": "admin-test", "is_test_price": True, "is_active": p.is_current,
                          "expires_at": to_utc_iso_z(p.expires_at), "ended_at": to_utc_iso_z(p.ended_at)}
                         for p in game.price_tests if p.platform and p.platform.name == selected
                         and p.currency == currency and (not start or p.collected_at >= start)],
        "stats": stats,
        "history_synced_at": to_utc_iso_z(game.history_synced_at),
        "history_error": game.history_error,
        "history_status": history_status(game),
        "external_history_count": sum(
            p.source == "itad-history" and p.is_available and snapshot_is_allowed(p)
            for p in raw
        ),
        "external_history_total": sum(
            p.source == "itad-history" and p.is_available and snapshot_is_allowed(p)
            for p in game.prices
        ),
        "price_filter_notice": "주요 공식 스토어 외 90% 초과 할인 가격은 현재가·이력·예측에서 제외합니다.",
        "history_source": "IsThereAnyDeal 가격 변경 이력 + 직접 수집 관측값",
        "history_start_at": to_utc_iso_z(
            min((p.collected_at for p in raw), default=None)
        ),
    }


@app.get("/")
def root():
    return {
        "name": "DealNaru API",
        "version": app.version,
        "docs": "/docs",
        "health": "/api/health",
    }


@app.get("/api/health", tags=["system"])
def health_check(db: Session = Depends(get_db)):
    try:
        db.execute(text("SELECT 1"))
    except SQLAlchemyError as error:
        raise HTTPException(
            status_code=503,
            detail="데이터베이스 연결을 확인할 수 없습니다.",
        ) from error

    return {
        "status": "ok",
        "database": "connected",
    }


@app.get("/api/home")
def get_home_data(db: Session = Depends(get_db)):
    games = load_games_query(db).all()
    cards = [format_game_card(game) for game in games]
    # 할인율은 순위에 사용하지 않습니다. 평가 수가 같은 게임은 카탈로그 순위를 참고합니다.
    popular = sorted(
        cards,
        key=lambda card: (
            -card["review_count"],
            -int(bool(card["catalog_source"])),
            card["catalog_rank"] if card["catalog_rank"] is not None else 10**9,
            card["title"].casefold(),
            card["id"],
        ),
    )[:12]
    cards_with_price = [
        card for card in cards if card["has_price"] and card["discounted_price"] > 0
    ]
    discounted = sorted(
        [card for card in cards_with_price if card["discount_rate"] > 0 and not card["is_test_price"]],
        key=lambda card: card["discount_rate"],
        reverse=True,
    )
    steam_sales = [
        (game, price)
        for game in games
        for price in get_current_prices(game, include_tests=False)
        if price.platform and price.platform.name == "Steam" and price.discount_rate > 0
    ]
    steam_sales.sort(
        key=lambda pair: (
            float(pair[1].discount_rate),
            -float(pair[1].discounted_price),
        ),
        reverse=True,
    )
    today = (
        format_game_card(steam_sales[0][0], selected_price=steam_sales[0][1])
        if steam_sales
        else (discounted[0] if discounted else None)
    )
    candidates = [
        card
        for card in (discounted or cards_with_price or cards)
        if today is None or card["id"] != today["id"]
    ]
    if not candidates:
        candidates = [
            card
            for card in cards_with_price
            if today is None or card["id"] != today["id"]
        ]
    if not candidates:
        candidates = [
            card for card in cards if today is None or card["id"] != today["id"]
        ]
    random_game = random.choice(candidates) if candidates else today
    return {
        "today_deal": today,
        "random_game": random_game,
        "popular_games": popular,
        "popularity_basis": "review_count",
        "stats": {
            "games": len(cards),
            "software": db.query(Software).count(),
            "used_items": db.query(UsedMarketItem)
            .filter(UsedMarketItem.source == "중고나라", UsedMarketItem.collected_at >= get_used_market_cutoff(), visible_market_clause())
            .count(),
        },
    }


@app.get("/api/games/search")
def search_games(keyword: str = Query(max_length=200), db: Session = Depends(get_db)):
    games = load_games_query(db).filter(Game.title.ilike(f"%{keyword}%")).order_by(Game.id).limit(100).all()

    return [format_game_card(game) for game in games]


@app.get("/api/games")
def list_games(
    keyword: str = Query(""),
    genre: str = Query("전체"),
    min_discount: int = Query(0),
    min_price: int = Query(0),
    max_price: Optional[int] = Query(None),
    platform: str = Query("전체 플랫폼"),
    sort: str = Query("popular"),
    page: int = Query(1),
    page_size: int = Query(10),
    paginate: bool = Query(False),
    db: Session = Depends(get_db),
):
    if page < 1:
        page = 1

    if page_size < 1:
        page_size = 10

    if page_size > 50:
        page_size = 50

    games_query = load_games_query(db)

    if keyword.strip():
        games_query = games_query.filter(Game.title.ilike(f"%{keyword.strip()}%"))

    uses_database_pagination = (
        genre == "전체"
        and min_discount <= 0
        and min_price <= 0
        and max_price is None
        and platform == "전체 플랫폼"
        and sort == "popular"
    )

    if uses_database_pagination:
        total = games_query.count()
        total_pages = max(1, (total + page_size - 1) // page_size)

        if page > total_pages:
            page = total_pages

        games = (
            games_query.order_by(Game.review_count.desc(), Game.id.desc())
            .offset((page - 1) * page_size if paginate else 0)
            .limit(page_size if paginate else total or 1)
            .all()
        )
        cards = [format_game_card(game) for game in games]

        if paginate:
            return {
                "items": cards,
                "total": total,
                "page": page,
                "page_size": page_size,
                "total_pages": total_pages,
            }

        return cards

    games = games_query.all()

    if keyword.strip():
        normalized_keyword = normalize_text(keyword)

        games = [
            game for game in games if normalized_keyword in normalize_text(game.title)
        ]

    if genre != "전체":
        games = [game for game in games if game_matches_genre(game, genre)]

    cards = [format_game_card(game) for game in games]

    if min_discount > 0:
        cards = [game for game in cards if game["discount_rate"] >= min_discount]

    if min_price > 0:
        cards = [
            game
            for game in cards
            if game["has_price"]
            and game["currency"] == "KRW"
            and game["discounted_price"] >= min_price
        ]

    if max_price is not None:
        cards = [
            game
            for game in cards
            if game["has_price"]
            and game["currency"] == "KRW"
            and game["discounted_price"] <= max_price
        ]

    if platform != "전체 플랫폼":
        cards = [game for game in cards if platform in game.get("platform_groups", [])]

    if sort == "price":
        cards = sorted(
            cards,
            key=lambda game: (
                not game["has_price"],
                game["currency"] != "KRW",
                game["discounted_price"] or 0,
            ),
        )
    elif sort == "discount":
        cards = sorted(cards, key=lambda game: game["discount_rate"], reverse=True)
    else:
        cards = sorted(
            cards,
            key=lambda game: (game.get("review_count", 0), game["id"]),
            reverse=True,
        )

    total = len(cards)
    total_pages = max(1, (total + page_size - 1) // page_size)

    if page > total_pages:
        page = total_pages

    start_index = (page - 1) * page_size
    end_index = start_index + page_size
    paged_items = cards[start_index:end_index]

    if paginate:
        return {
            "items": paged_items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
        }

    return cards


@app.get("/api/games/{game_id}")
def get_game_detail(game_id: int, db: Session = Depends(get_db)):
    game = (
        db.query(Game)
        .options(
            joinedload(Game.tags),
            joinedload(Game.prediction),
            joinedload(Game.used_market),
            joinedload(Game.prices).joinedload(PriceSnapshot.platform),
            selectinload(Game.price_tests).joinedload(GamePriceTest.platform),
        )
        .filter(Game.id == game_id)
        .first()
    )

    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    return format_game_detail(game)


@app.get("/api/games/{game_id}/prediction")
def get_game_prediction(game_id: int, db: Session = Depends(get_db)):
    game = (
        db.query(Game)
        .options(
            joinedload(Game.prices).joinedload(PriceSnapshot.platform),
        )
        .filter(Game.id == game_id)
        .first()
    )

    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    selected_history = build_history_response(game, include_test_current=False)
    history_prices = [
        price
        for price in game.prices
        if price.platform.name == selected_history["history_platform"]
        and price.is_available
        and price.currency == "KRW"
        and snapshot_is_allowed(price)
    ]

    history_prices = sorted(
        history_prices,
        key=lambda price: price.collected_at,
    )

    return predict_discount_from_history(history_prices)


def normalize_registration_email(value: str):
    try:
        return validate_email(value.strip(), check_deliverability=False,
                              test_environment=not PRODUCTION, allow_smtputf8=False).normalized.lower()
    except EmailNotValidError as error:
        raise HTTPException(status_code=400, detail="올바른 이메일 주소를 입력해 주세요.") from error


@app.post("/api/auth/register")
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = normalize_registration_email(payload.email)
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=400, detail="닉네임을 입력해 주세요.")
    if PRODUCTION and not payload.accept_terms:
        raise HTTPException(status_code=400, detail="가입하려면 이용약관에 동의해 주세요.")
    if PRODUCTION and not payload.age_confirmed:
        raise HTTPException(status_code=400, detail="회원가입은 만 14세 이상만 가능합니다. 연령 확인 항목을 체크해 주세요.")
    if db.query(User.id).filter(or_(User.email == email, User.nickname == nickname)).first():
        raise HTTPException(status_code=400, detail="이미 사용 중인 이메일 또는 닉네임입니다.")

    token, token_hash, expires_at = create_email_verification_token()
    now = utc_now()
    db.add(User(
        email=email, nickname=nickname, password_hash=hash_password(payload.password),
        email_verified=False, email_verify_token_hash=token_hash,
        email_verify_token_expires_at=expires_at, email_verify_sent_at=now,
        email_notifications_enabled=True,
        terms_version=TERMS_VERSION if payload.accept_terms else None,
        terms_accepted_at=now if payload.accept_terms else None,
    ))
    try:
        db.commit()
    except IntegrityError as error:
        db.rollback()
        raise HTTPException(status_code=400, detail="이미 사용 중인 이메일 또는 닉네임입니다.") from error
    try:
        mail = send_verification_email(to=email, nickname=nickname, token=token)
    except Exception as error:
        logger.warning("가입 인증 메일 발송 실패: %s", type(error).__name__)
        return {"message": "계정을 만들었지만 인증 메일을 보내지 못했습니다. 로그인 화면에서 인증 메일을 다시 요청해 주세요.",
                "email": email, "mail_sent": False}
    result = {"message": "계정을 만들었습니다. 이메일 인증 후 로그인해 주세요.",
              "email": email, "mail_sent": True}
    if not PRODUCTION and mail.get("dev_verify_url"):
        result["dev_verify_url"] = mail["dev_verify_url"]
    return result


@app.post("/api/auth/login")
def login(payload: LoginRequest, request: Request, response: Response, db: Session = Depends(get_db)):
    email = payload.email.strip().lower()
    user = db.query(User).filter(User.email == email).first()
    if not verify_password(payload.password, user.password_hash if user else DUMMY_HASH) or user is None:
        raise HTTPException(status_code=401, detail="이메일 또는 비밀번호가 올바르지 않습니다.")
    if EMAIL_VERIFICATION_REQUIRED and not user.email_verified:
        raise HTTPException(status_code=403, detail="이메일 인증 후 로그인할 수 있습니다. 메일함을 확인해 주세요.")
    if password_needs_upgrade(payload.password, user.password_hash):
        user.password_hash = hash_password(payload.password)
    user.last_login = utc_now()
    token = start_session(db, request, response, user)
    result = {"user": format_user(user)}
    if not PRODUCTION:
        result.update(access_token=token, token_type="bearer")
    return result


@app.post("/api/auth/logout", status_code=204)
def logout(request: Request, response: Response, db: Session = Depends(get_db)):
    end_session(db, request, response)


@app.post("/api/auth/withdraw", status_code=204)
def withdraw_account(
    payload: WithdrawRequest, request: Request, response: Response,
    current_user: User = Depends(get_current_user), db: Session = Depends(get_db),
):
    if not verify_password(payload.password, current_user.password_hash):
        raise HTTPException(status_code=400, detail="비밀번호가 올바르지 않습니다.")
    # 사용자 관계에 설정된 cascade로 찜·알림·모든 로그인 세션을 함께 제거합니다.
    db.delete(current_user)
    db.commit()
    end_session(db, request, response)


@app.post("/api/auth/verify-email")
def verify_email(payload: VerifyEmailRequest, db: Session = Depends(get_db)):
    token_hash = hash_email_verification_token(payload.token.strip())
    user = db.query(User).filter(User.email_verify_token_hash == token_hash).with_for_update().first()
    if user is None or user.email_verified:
        raise HTTPException(status_code=400, detail="유효하지 않거나 이미 사용한 링크입니다. 인증을 완료했다면 로그인해 주세요.")
    if user.email_verify_token_expires_at is None or user.email_verify_token_expires_at < utc_now():
        raise HTTPException(status_code=400, detail="인증 링크가 만료되었습니다. 인증 메일을 다시 요청해 주세요.")
    user.email_verified = True
    user.email_verified_at = utc_now()
    user.email_verify_token_hash = None
    user.email_verify_token_expires_at = None
    db.commit()
    return {"message": "이메일 인증이 끝났습니다. 이제 로그인할 수 있습니다.", "already_verified": False}


@app.post("/api/auth/resend-verification")
def resend_verification_email(payload: ResendVerificationRequest, db: Session = Depends(get_db)):
    email = normalize_registration_email(payload.email)
    result = {"message": "인증이 필요한 계정이라면 메일을 보냅니다. 최근에 요청했다면 잠시 뒤 메일함을 확인해 주세요."}
    user = db.query(User).filter(User.email == email).with_for_update().first()
    now = utc_now()
    if user is None or user.email_verified or (
        user.email_verify_sent_at and user.email_verify_sent_at > now - timedelta(seconds=90)
    ):
        return result
    token, token_hash, expires_at = create_email_verification_token()
    user.email_verify_token_hash = token_hash
    user.email_verify_token_expires_at = expires_at
    user.email_verify_sent_at = now
    nickname = user.nickname
    db.commit()
    try:
        mail = send_verification_email(to=email, nickname=nickname, token=token)
    except Exception as error:
        logger.warning("인증 메일 재전송 실패: %s", type(error).__name__)
        return result
    if not PRODUCTION and mail.get("dev_verify_url"):
        result["dev_verify_url"] = mail["dev_verify_url"]
    return result


@app.get("/api/auth/me")
def get_me(current_user: User = Depends(get_current_user)):
    return format_user(current_user)


@app.get("/api/wishlist")
def get_wishlist(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    items = (
        db.query(WishlistItem)
        .options(
            joinedload(WishlistItem.game)
            .selectinload(Game.prices.and_(PriceSnapshot.is_current.is_(True)))
            .joinedload(PriceSnapshot.platform),
            joinedload(WishlistItem.game).selectinload(Game.price_tests).joinedload(GamePriceTest.platform),
        )
        .filter(WishlistItem.user_id == current_user.id)
        .order_by(WishlistItem.created_at.desc())
        .all()
    )

    result = []

    for item in items:
        game_card = format_game_card(item.game)
        game_card["wishlist_id"] = item.id
        game_card["target_price"] = item.target_price
        game_card["notify_enabled"] = item.notify_enabled
        game_card["wishlist_created_at"] = (
            item.created_at.isoformat() if item.created_at else None
        )
        game_card["is_target_reached"] = (
            item.target_price is not None
            and game_card["has_price"]
            and game_card["currency"] == "KRW"
            and game_card["discounted_price"] <= item.target_price
        )
        result.append(game_card)

    return result


@app.get("/api/wishlist/check/{game_id}")
def check_wishlist(
    game_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.game_id == game_id,
        )
        .first()
    )

    return {"is_wishlisted": item is not None}


@app.post("/api/wishlist/games/{game_id}")
def add_wishlist(
    game_id: int,
    payload: WishlistCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    game = db.query(Game).filter(Game.id == game_id).first()

    if game is None:
        raise HTTPException(status_code=404, detail="게임을 찾을 수 없습니다.")

    if payload.target_price is not None and payload.target_price < 0:
        raise HTTPException(status_code=400, detail="목표 가격은 0 이상이어야 합니다.")

    item = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.game_id == game_id,
        )
        .first()
    )

    if item:
        item.target_price = payload.target_price
        item.notify_enabled = payload.notify_enabled
    else:
        item = WishlistItem(
            user_id=current_user.id,
            game_id=game_id,
            target_price=payload.target_price,
            notify_enabled=payload.notify_enabled,
        )
        db.add(item)

    db.commit()

    return {"message": "위시리스트에 저장했습니다."}


@app.delete("/api/wishlist/games/{game_id}")
def remove_wishlist(
    game_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.game_id == game_id,
        )
        .first()
    )

    if item is None:
        raise HTTPException(status_code=404, detail="위시리스트에 없는 게임입니다.")

    db.delete(item)
    db.commit()

    return {"message": "위시리스트에서 삭제했습니다."}


@app.put("/api/wishlist/games/{game_id}")
def update_wishlist(
    game_id: int,
    payload: WishlistUpdateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    item = (
        db.query(WishlistItem)
        .filter(
            WishlistItem.user_id == current_user.id,
            WishlistItem.game_id == game_id,
        )
        .first()
    )

    if item is None:
        raise HTTPException(status_code=404, detail="위시리스트에 없는 게임입니다.")

    if payload.target_price is not None and payload.target_price < 0:
        raise HTTPException(status_code=400, detail="목표 가격은 0 이상이어야 합니다.")

    item.target_price = payload.target_price
    item.notify_enabled = payload.notify_enabled

    db.commit()

    return {"message": "위시리스트 설정을 저장했습니다."}


@app.get("/api/software")
def list_software(
    keyword: str = Query(""),
    category: str = Query("전체"),
    kind: str = Query("all", pattern="^(all|software|ai)$"),
    offers_only: bool = Query(False),
    db: Session = Depends(get_db),
):
    query = db.query(Software).options(
        selectinload(Software.promotions), selectinload(Software.plans)
    )

    if keyword.strip():
        query = query.filter(or_(Software.name.icontains(keyword.strip(), autoescape=True),
                                 Software.description.icontains(keyword.strip(), autoescape=True)))

    if category != "전체":
        query = query.filter(Software.category == category)
    if kind != "all":
        query = query.filter(Software.kind == kind)

    software_list = query.all()

    cards = [
        format_software_card(software)
        for software in software_list
        if not software.promotions
        or any(p.source_status != "demo" for p in software.promotions)
    ]
    return [card for card in cards if card["offer_count"] > 0] if offers_only else cards


@app.get("/api/software/{software_id}")
def get_software_detail(
    software_id: int,
    db: Session = Depends(get_db),
):
    software = (
        db.query(Software)
        .options(selectinload(Software.promotions), selectinload(Software.plans))
        .filter(Software.id == software_id)
        .first()
    )

    if software is None:
        raise HTTPException(status_code=404, detail="소프트웨어를 찾을 수 없습니다.")

    return format_software_detail(software)


@app.get("/api/used-market")
def list_used_market_items(
    keyword: str = Query("전체", max_length=100),
    sort: str = Query("recent", pattern="^(recent|price)$"),
    page: int = Query(1),
    size: int = Query(12),
    db: Session = Depends(get_db),
):
    if page < 1:
        page = 1

    if size < 1:
        size = 12

    if size > 50:
        size = 50

    query = db.query(UsedMarketItem).filter(
        UsedMarketItem.source == "중고나라",
        UsedMarketItem.collected_at >= get_used_market_cutoff(),
        UsedMarketItem.status == "판매중",
        visible_market_clause(),
    )

    keyword = keyword.strip()
    if keyword and keyword != "전체":
        query = query.filter(
            or_(
                UsedMarketItem.keyword.icontains(keyword, autoescape=True),
                UsedMarketItem.title.icontains(keyword, autoescape=True),
            )
        )

    eligible = query.filter(UsedMarketItem.status == "판매중", UsedMarketItem.price > 0)
    lowest_price = eligible.with_entities(func.min(UsedMarketItem.price)).scalar() if keyword and keyword != "전체" else None
    lowest_count = eligible.filter(UsedMarketItem.price == lowest_price).count() if lowest_price is not None else 0
    total = query.count()
    total_pages = max(1, (total + size - 1) // size)

    if page > total_pages:
        page = total_pages

    ordering = [UsedMarketItem.collected_at.desc(), UsedMarketItem.id.desc()]
    if sort == "price":
        ordering = [case((UsedMarketItem.price > 0, 0), else_=1), UsedMarketItem.price.asc().nullslast(), *ordering]
    elif lowest_price is not None:
        ordering = [case(((UsedMarketItem.price == lowest_price) & (UsedMarketItem.status == "판매중"), 0), else_=1), *ordering]
    items = (
        query.order_by(*ordering)
        .offset((page - 1) * size)
        .limit(size)
        .all()
    )

    keywords = [
        row[0]
        for row in db.query(UsedMarketItem.keyword)
        .filter(UsedMarketItem.source == "중고나라", UsedMarketItem.collected_at >= get_used_market_cutoff(), UsedMarketItem.status == "판매중", visible_market_clause())
        .distinct()
        .order_by(UsedMarketItem.keyword.asc())
        .all()
    ]

    return {
        "items": [{**format_used_market_item(item), "is_lowest": lowest_price is not None and item.price == lowest_price and item.status == "판매중"} for item in items],
        "keywords": ["전체"] + keywords,
        "total": total,
        "page": page,
        "size": size,
        "total_pages": total_pages,
        "lowest_price": lowest_price,
        "lowest_count": lowest_count,
        "comparison_scope": "검색 조건에 맞는 수집 매물 전체 · 배송비 제외 · 상품 상태/구성 차이 있음",
        "search_links": [{"source": "중고나라", "url": build_search_url("joongna", keyword if keyword != "전체" else "게임CD")}],
    }


@app.get("/api/notifications")
def list_notifications(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifications = (
        db.query(Notification)
        .options(joinedload(Notification.wishlist_item).joinedload(WishlistItem.game))
        .filter(Notification.user_id == current_user.id)
        .order_by(Notification.created_at.desc())
        .all()
    )

    return [format_notification(notification) for notification in notifications]


@app.get("/api/notifications/unread-count")
def get_unread_notification_count(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    count = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
        .count()
    )

    return {"count": count}


@app.get("/api/games/{game_id}/history")
def get_game_history(
    game_id: int,
    platform: str = Query("최저가"),
    period: str = Query("1y", pattern="^(1m|3m|6m|1y|all)$"),
    currency: Optional[str] = Query(None),
    db: Session = Depends(get_db),
):
    game = (
        db.query(Game)
        .options(
            joinedload(Game.prices).joinedload(PriceSnapshot.platform),
        )
        .filter(Game.id == game_id)
        .first()
    )

    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")

    return build_history_response(
        game=game,
        platform_name=platform,
        period=period,
        currency=currency,
    )


@app.post("/api/games/{game_id}/history/sync", status_code=202)
def prioritize_game_history(game_id: int, db: Session = Depends(get_db)):
    game = db.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="Game not found")
    result = queue_history_sync(db, game_id, priority=1)
    db.commit()
    db.refresh(game)
    return {**result, "history_status": history_status(game), "game_id": game.id}


@app.patch("/api/notifications/read-all")
def mark_all_notifications_as_read(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notifications = (
        db.query(Notification)
        .filter(
            Notification.user_id == current_user.id,
            Notification.is_read.is_(False),
        )
        .all()
    )

    for notification in notifications:
        notification.is_read = True

    db.commit()

    return {"message": "모든 알림을 읽음 처리했습니다."}


@app.patch("/api/notifications/{notification_id}/read")
def mark_notification_as_read(
    notification_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    notification = (
        db.query(Notification)
        .filter(
            Notification.id == notification_id,
            Notification.user_id == current_user.id,
        )
        .first()
    )

    if notification is None:
        raise HTTPException(status_code=404, detail="알림을 찾을 수 없습니다.")

    notification.is_read = True
    db.commit()

    return {"message": "알림을 읽음 처리했습니다."}


@app.post("/api/dev/games/{game_id}/simulate-price", dependencies=[Depends(development_only)])
def simulate_game_price_update(
    game_id: int,
    payload: PriceSimulationRequest,
    _current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.discounted_price < 0:
        raise HTTPException(status_code=400, detail="할인가는 0 이상이어야 합니다.")

    game = (
        db.query(Game)
        .options(
            joinedload(Game.prices).joinedload(PriceSnapshot.platform),
            joinedload(Game.wishlist_items).joinedload(WishlistItem.user),
        )
        .filter(Game.id == game_id)
        .first()
    )

    if game is None:
        raise HTTPException(status_code=404, detail="게임을 찾을 수 없습니다.")

    platform = db.query(Platform).filter(Platform.name == payload.platform).first()

    if platform is None:
        raise HTTPException(status_code=404, detail="플랫폼을 찾을 수 없습니다.")

    current_price = None

    for price in game.prices:
        if price.is_current and price.platform_id == platform.id:
            current_price = price
            break

    if current_price:
        original_price = payload.original_price or to_int(current_price.original_price)
        current_price.is_current = False
    else:
        original_price = payload.original_price or payload.discounted_price

    discount_rate = calculate_discount_rate(original_price, payload.discounted_price)
    if reason := excessive_discount_reason(
        platform.name, platform.itad_shop_id, original_price, payload.discounted_price
    ):
        raise HTTPException(status_code=400, detail=reason)

    new_price = PriceSnapshot(
        game_id=game.id,
        platform_id=platform.id,
        currency="KRW",
        original_price=original_price,
        discounted_price=payload.discounted_price,
        discount_rate=discount_rate,
        url="#",
        collected_at=utc_now(),
        is_current=True,
    )

    db.add(new_price)
    db.flush()

    notifications_created = 0

    wishlist_items = (
        db.query(WishlistItem)
        .options(joinedload(WishlistItem.game))
        .filter(
            WishlistItem.game_id == game.id,
            WishlistItem.notify_enabled.is_(True),
        )
        .all()
    )

    for item in wishlist_items:
        if item.target_price is None:
            continue

        if payload.discounted_price <= item.target_price:
            notification = Notification(
                user_id=item.user_id,
                wishlist_item_id=item.id,
                type="TARGET_PRICE",
                title=f"{game.title} 목표가 도달!",
                message=(
                    f"{game.title}의 {platform.name} 가격이 "
                    f"{payload.discounted_price:,}원으로 내려갔습니다. "
                    f"설정한 목표가 {item.target_price:,}원 이하입니다."
                ),
                is_read=False,
            )

            db.add(notification)
            notifications_created += 1

    db.commit()

    return {
        "message": "가격 갱신 시뮬레이션을 완료했습니다.",
        "game_id": game.id,
        "game_title": game.title,
        "platform": platform.name,
        "discounted_price": payload.discounted_price,
        "discount_rate": round(discount_rate, 2),
        "notifications_created": notifications_created,
    }


@app.get("/api/admin/summary")
def get_admin_summary(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    return {
        "games_count": db.query(Game).count(),
        "software_count": db.query(Software).count(),
        "used_market_count": db.query(UsedMarketItem).filter(UsedMarketItem.source == "중고나라").count(),
        "wishlist_count": db.query(WishlistItem).count(),
        "notifications_count": db.query(Notification).count(),
        "crawling_logs_count": db.query(CrawlingLog).count(),
    }


@app.post("/api/admin/games/find")
def find_requested_game(
    payload: TargetedSearchRequest,
    _admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    try:
        return search_requested_games(db, payload.query)
    except ValueError as error:
        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error)) from error
    except Exception as error:
        raise HTTPException(
            status_code=502,
            detail="게임 검색 출처에 연결하지 못했습니다. 잠시 뒤 다시 시도해주세요.",
        ) from error


@app.post("/api/admin/games/import")
def import_requested_game(
    payload: TargetedImportRequest,
    _admin: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    platform = get_or_create_log_platform(
        db, "Steam" if payload.steam_appid else "IsThereAnyDeal"
    )
    log = CrawlingLog(
        platform_id=platform.id, status="RUNNING", message="관리자 지정 게임 수집 중"
    )
    db.add(log)
    db.commit()
    log_id = log.id
    try:
        result = collect_requested_game(
            db, steam_appid=payload.steam_appid, itad_id=payload.itad_id
        )
        log = db.get(CrawlingLog, log_id)
        log.status = "PARTIAL" if result["warnings"] else "SUCCESS"
        log.rows_count = result["updated_count"]
        log.end_time = utc_now()
        log.message = f"관리자 지정 수집: {result['title']} · 가격 {result['updated_count']}건 · 이력 {'예약' if result['history_queued'] else '확인 필요'}"
        db.commit()
        return {
            "message": f"{result['title']} {'등록' if result['created'] else '갱신'} 완료",
            "result": result,
            "log": format_crawling_log(log),
        }
    except Exception as error:
        db.rollback()
        log = db.get(CrawlingLog, log_id)
        if log:
            log.status = "FAIL"
            log.end_time = utc_now()
            log.message = f"관리자 지정 게임 수집 실패: {type(error).__name__}"
            db.commit()
        detail = (
            str(error)
            if isinstance(error, ValueError)
            else "지정 게임을 수집하지 못했습니다. 연결을 확인하고 다시 시도해주세요."
        )
        raise HTTPException(
            status_code=400 if isinstance(error, ValueError) else 502, detail=detail
        ) from error


@app.get("/api/admin/game-options")
def list_admin_game_options(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    games = db.query(Game.id, Game.title).order_by(Game.title.asc()).all()
    return [{"id": game.id, "title": game.title} for game in games]


@app.get("/api/admin/price-tests")
def admin_price_tests(_admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    games = load_games_query(db).order_by(Game.title, Game.id).all()
    return {"items": list_price_tests(db), "games": [
        {"id": game.id, "title": game.title, "platforms": [
            {"name": p.platform.name, "original_price": int(p.original_price),
             "discounted_price": int(p.discounted_price)}
            for p in get_current_prices(game, include_tests=False) if p.platform and p.currency == "KRW"
        ]} for game in games if any(p.currency == "KRW" for p in get_current_prices(game, include_tests=False))
    ]}


@app.post("/api/admin/price-tests", status_code=201)
def admin_create_price_test(payload: AdminPriceTestRequest,
                            admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    try:
        result = create_price_test(db, admin=admin, game_id=payload.game_id, platform_name=payload.platform,
            discounted_price=payload.discounted_price, original_price=payload.original_price,
            duration_minutes=payload.duration_minutes, recipient_email=payload.recipient_email,
            send_email=payload.send_email)
        db.commit()
        return result
    except (LookupError, ValueError) as error:
        db.rollback()
        raise HTTPException(status_code=404 if isinstance(error, LookupError) else 400, detail=str(error)) from error


@app.delete("/api/admin/price-tests/{test_id}")
def admin_stop_price_test(test_id: int, _admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    try:
        result = stop_price_test(db, test_id)
        db.commit()
        return result
    except LookupError as error:
        db.rollback()
        raise HTTPException(status_code=404, detail=str(error)) from error


@app.get("/api/admin/used-market/exclusions")
def admin_market_exclusions(_admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    return [{"id": row.id, "kind": row.kind, "label": row.label, "created_at": to_utc_iso_z(row.created_at)}
            for row in db.query(UsedMarketExclusion).order_by(UsedMarketExclusion.created_at.desc(), UsedMarketExclusion.id.desc()).all()]


@app.delete("/api/admin/used-market/keyword")
def admin_delete_market_keyword(keyword: str = Query(min_length=1, max_length=100),
                                admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    try:
        count = delete_market_keyword(db, keyword, admin_id=admin.id)
        db.commit()
        return {"message": f"{keyword.strip()} 키워드와 매물 {count}개를 삭제했습니다. 재수집에서 제외합니다.", "deleted_count": count}
    except ValueError as error:
        db.rollback()
        raise HTTPException(status_code=400, detail=str(error)) from error


@app.delete("/api/admin/used-market/items/{item_id}")
def admin_delete_market_item(item_id: int, admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    item = db.query(UsedMarketItem).filter_by(id=item_id, source="중고나라").first()
    if item is None:
        raise HTTPException(status_code=404, detail="수집 매물을 찾을 수 없습니다.")
    delete_market_item(db, item, admin_id=admin.id)
    db.commit()
    return {"message": "수집 매물을 삭제했습니다. 같은 매물은 재수집에서 제외합니다."}


@app.delete("/api/admin/used-market/exclusions/{exclusion_id}")
def admin_restore_market_exclusion(exclusion_id: int, _admin: User = Depends(get_current_admin_user), db: Session = Depends(get_db)):
    row = db.get(UsedMarketExclusion, exclusion_id)
    if row is None:
        raise HTTPException(status_code=404, detail="재수집 제외 항목을 찾을 수 없습니다.")
    db.delete(row)
    db.commit()
    return {"message": "재수집을 허용했습니다. 삭제한 매물은 다시 수집해야 표시됩니다."}


@app.get("/api/admin/auto-refresh")
def auto_refresh_status(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    return get_scheduler_status(db)


@app.put("/api/admin/auto-refresh")
def update_auto_refresh(
    payload: AutoRefreshRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    return configure_scheduler(db, **payload.model_dump())


@app.post("/api/admin/auto-refresh/run", status_code=202)
def queue_auto_refresh(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    try:
        status = request_refresh(db)
    except ValueError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    return {
        "message": "갱신 요청을 등록했습니다. 아래 상태와 로그에서 결과를 확인하세요.",
        "status": status,
    }


@app.post("/api/admin/software/refresh")
def collect_official_subscription_offers(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    platform = get_or_create_log_platform(db, "프로그램·AI")
    log = CrawlingLog(
        platform_id=platform.id,
        status="RUNNING",
        message="프로그램·AI 공식 혜택 확인 중",
    )
    db.add(log)
    db.commit()
    log_id = log.id
    try:
        result = refresh_subscription_offers(db)
    except Exception as error:
        db.rollback()
        log = db.get(CrawlingLog, log_id)
        log.status = "FAIL"
        log.end_time = utc_now()
        log.message = "프로그램·AI 혜택 확인에 실패했습니다. 다시 시도해주세요."
        db.commit()
        raise HTTPException(status_code=503, detail=log.message) from error
    log.status = (
        ("PARTIAL" if result["updated_count"] else "FAIL")
        if result["error_count"]
        else "SUCCESS"
    )
    log.end_time = utc_now()
    log.rows_count = result["updated_count"]
    log.message = f"프로그램·AI 공식 혜택 {result['updated_count']}개 확인 · 확인 필요 {result['error_count']}개"
    db.commit()
    return {"message": log.message, "result": result, "log": format_crawling_log(log)}


@app.get("/api/admin/crawling-logs")
def list_crawling_logs(
    page: int = Query(1),
    page_size: int = Query(10),
    view: str = Query("all", pattern="^(all|summary)$"),
    filter: str = Query("all", pattern="^(all|issues)$"),
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if page < 1:
        page = 1

    if page_size < 1:
        page_size = 10

    if page_size > 50:
        page_size = 50

    if prune_crawling_logs(db):
        db.commit()
    query = db.query(CrawlingLog)
    if view == "summary":
        query = query.filter(or_(
            CrawlingLog.message.is_(None),
            ~CrawlingLog.message.startswith("가격 이력 우선 보강"),
            CrawlingLog.group_key.isnot(None),
        ))
    if filter == "issues":
        query = query.filter(CrawlingLog.status.notin_(["SUCCESS", "RUNNING"]))
    total = query.count()
    total_pages = max(1, (total + page_size - 1) // page_size)

    if page > total_pages:
        page = total_pages

    logs = (
        query
        .options(joinedload(CrawlingLog.platform))
        .order_by(func.coalesce(CrawlingLog.last_event_at, CrawlingLog.start_time).desc(), CrawlingLog.id.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
        .all()
    )

    return {
        "items": [format_crawling_log(log) for log in logs],
        "total": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "retention_days": LOG_RETENTION_DAYS,
        "view": view,
        "filter": filter,
    }


@app.post("/api/admin/collect-price", dependencies=[Depends(development_only)])
def collect_price_manually(
    payload: ManualCollectRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    start_time = utc_now()

    platform = get_or_create_log_platform(db, payload.platform)

    log = CrawlingLog(
        platform_id=platform.id if platform else None,
        status="RUNNING",
        start_time=start_time,
        rows_count=0,
        message="수동 가격 변경을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    if payload.discounted_price < 0:
        log.status = "FAIL"
        log.end_time = utc_now()
        log.message = "할인가는 0 이상이어야 합니다."
        db.commit()

        raise HTTPException(status_code=400, detail=log.message)

    try:
        result = update_game_price_and_notify(
            db=db,
            game_id=payload.game_id,
            platform_name=payload.platform,
            discounted_price=payload.discounted_price,
            original_price=payload.original_price,
        )

        log.status = "SUCCESS"
        log.end_time = utc_now()
        log.rows_count = 1
        log.message = (
            f"{result['game_title']} 가격을 {result['discounted_price']:,}원으로 갱신했습니다. "
            f"생성된 알림: {result['notifications_created']}개"
        )

        db.commit()

        return {
            "message": "수동 가격 변경을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.post("/api/admin/steam/import-featured")
def import_steam_featured_games(
    payload: SteamImportRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.max_items < 1:
        raise HTTPException(
            status_code=400, detail="수집 개수는 1개 이상이어야 합니다."
        )

    if payload.max_items > 100:
        raise HTTPException(
            status_code=400, detail="한 번에 최대 100개까지만 수집할 수 있습니다."
        )

    require_source_available(steam_source.request_gate)
    require_bulk_collection_idle(db)
    steam_platform = db.query(Platform).filter(Platform.name == "Steam").first()

    log = CrawlingLog(
        platform_id=steam_platform.id if steam_platform else None,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message="Steam 인기/할인 게임 수집을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = collect_steam_featured_games(
            db=db,
            max_items=payload.max_items,
            country=payload.country,
            language=payload.language,
        )

        log.status = collection_result_status(result, result["imported_count"])
        log.end_time = utc_now()
        log.rows_count = result["imported_count"]
        log.message = (
            f"Steam 게임 {result['imported_count']}개 수집 완료, "
            f"스킵 {result['skipped_count']}개, 오류 {result['error_count']}개"
        )

        db.commit()
        queue_price_enrichment(db)

        return {
            "message": "Steam 인기/할인 게임 수집을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.post("/api/admin/steam/collect-existing")
def collect_existing_steam_games(
    payload: SteamImportRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.max_items < 1:
        raise HTTPException(
            status_code=400, detail="수집 개수는 1개 이상이어야 합니다."
        )

    if payload.max_items > 200:
        raise HTTPException(
            status_code=400, detail="한 번에 최대 200개까지만 갱신할 수 있습니다."
        )

    require_source_available(steam_source.request_gate)
    require_bulk_collection_idle(db)
    steam_platform = db.query(Platform).filter(Platform.name == "Steam").first()

    log = CrawlingLog(
        platform_id=steam_platform.id if steam_platform else None,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message="기존 Steam 게임 가격 갱신을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = collect_existing_steam_prices(
            db=db,
            max_items=payload.max_items,
            country=payload.country,
            language=payload.language,
        )

        log.status = collection_result_status(result, result["updated_count"])
        log.end_time = utc_now()
        log.rows_count = result["updated_count"]
        log.message = (
            f"기존 Steam 게임 {result['updated_count']}개 가격 갱신 완료, "
            f"스킵 {result['skipped_count']}개, 오류 {result['error_count']}개"
        )

        db.commit()

        return {
            "message": "기존 Steam 게임 가격 갱신을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.post("/api/admin/itad/import-deals")
def import_itad_deals_api(
    payload: ItadImportRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.limit < 1 or payload.limit > 200:
        raise HTTPException(
            status_code=400, detail="수집 개수는 1~200 사이여야 합니다."
        )

    require_source_available(itad_source.request_gate)
    require_bulk_collection_idle(db)
    shop_names = {
        61: "Steam",
        16: "Epic Game Store",
        35: "GOG",
        6: "Fanatical",
        36: "Green Man Gaming",
        37: "Humble Store",
    }
    selected_shop_label = "/".join(
        shop_names.get(shop_id, f"Shop {shop_id}")
        for shop_id in (payload.shop_ids or [])
    )
    source_label = selected_shop_label or "ITAD"
    selected_shop_ids = payload.shop_ids or []
    log_platform_name = (
        shop_names.get(selected_shop_ids[0], f"Shop {selected_shop_ids[0]}")
        if len(selected_shop_ids) == 1
        else "IsThereAnyDeal"
    )
    log_platform = get_or_create_log_platform(db, log_platform_name)

    log = CrawlingLog(
        platform_id=log_platform.id,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message=f"{source_label} 할인 게임 수집을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = import_itad_deals(
            db=db,
            limit=payload.limit,
            offset=payload.offset,
            country=payload.country,
            sort=payload.sort,
            shop_ids=payload.shop_ids,
        )

        error_count = result["error_count"]
        imported_count = result["imported_count"]

        if imported_count == 0 and error_count > 0:
            log.status = "FAIL"
        elif error_count > 0:
            log.status = "PARTIAL"
        else:
            log.status = "SUCCESS"

        log.end_time = utc_now()
        log.rows_count = imported_count
        log.message = (
            f"{source_label} 할인 게임 {imported_count}개 수집 완료, "
            f"스킵 {result['skipped_count']}개, 오류 {error_count}개"
        )

        db.commit()
        queue_price_enrichment(db)

        return {
            "message": f"{source_label} 할인 게임 수집을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.post("/api/admin/itad/collect-current")
def collect_itad_current_prices_api(
    payload: ItadCollectRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.limit < 1 or payload.limit > 200:
        raise HTTPException(
            status_code=400, detail="갱신 개수는 1~200 사이여야 합니다."
        )

    platform = get_or_create_log_platform(db, "IsThereAnyDeal")
    log = CrawlingLog(
        platform_id=platform.id,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message="ITAD 현재가 갱신을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = collect_current_prices_for_existing_itad_games(
            db=db,
            limit=payload.limit,
            country=payload.country,
        )

        log.status = "SUCCESS"
        log.end_time = utc_now()
        log.rows_count = result["updated_count"]
        log.message = (
            f"ITAD 현재가 {result['updated_count']}건 갱신 완료, "
            f"스킵 {result['skipped_count']}건"
        )

        db.commit()

        return {
            "message": "ITAD 현재가 갱신을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.post("/api/admin/itad/collect-history")
def collect_itad_history_api(
    payload: ItadHistoryRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.limit < 1 or payload.limit > 100:
        raise HTTPException(
            status_code=400, detail="히스토리 수집 게임 수는 1~100 사이여야 합니다."
        )

    require_source_available(itad_source.request_gate)
    require_bulk_collection_idle(db)
    platform = get_or_create_log_platform(db, "IsThereAnyDeal")
    log = CrawlingLog(
        platform_id=platform.id,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message="ITAD 가격 변경 이력 수집을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = collect_history_for_existing_itad_games(
            db=db,
            limit=payload.limit,
            country=payload.country,
            days=payload.days,
            offset=payload.offset,
        )

        imported_count = result["history_rows_imported"]
        error_count = result["error_count"]
        skipped_count = result["skipped_count"]

        if imported_count == 0 and error_count > 0:
            log.status = "FAIL"
        elif error_count > 0:
            log.status = "PARTIAL"
        else:
            log.status = "SUCCESS"

        log.end_time = utc_now()
        log.rows_count = imported_count

        first_error_text = ""

        if result.get("errors"):
            first_error = result["errors"][0]
            first_error_text = (
                f" / 첫 오류: {first_error.get('title')} - {first_error.get('error')}"
            )

        log.message = (
            f"ITAD 가격 히스토리 {imported_count}건 수집 완료, "
            f"스킵 {skipped_count}개, 오류 {error_count}개"
            f"{first_error_text}"
        )

        db.commit()

        return {
            "message": "ITAD 가격 히스토리 수집을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.delete("/api/admin/dev/remove-sample-games", dependencies=[Depends(development_only)])
def remove_sample_games(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    sample_games = (
        db.query(Game)
        .filter(
            Game.itad_id.is_(None),
            Game.steam_appid.is_(None),
            Game.epic_offer_id.is_(None),
            Game.catalog_source.is_(None),
        )
        .all()
    )

    deleted_count = len(sample_games)

    for game in sample_games:
        db.delete(game)

    db.commit()

    return {
        "message": f"샘플 게임 {deleted_count}개를 삭제했습니다.",
        "deleted_count": deleted_count,
    }


@app.post("/api/admin/itad/import-popular")
def import_itad_popular_games_api(
    payload: ItadImportRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if payload.limit < 1 or payload.limit > 500:
        raise HTTPException(
            status_code=400, detail="수집 개수는 1~500 사이여야 합니다."
        )
    platform = get_or_create_log_platform(db, "IsThereAnyDeal")
    log = CrawlingLog(
        platform_id=platform.id,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message="ITAD 인기 게임 수집을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = import_itad_popular_games(
            db=db,
            limit=payload.limit,
            offset=payload.offset,
            country=payload.country,
        )

        error_count = result["error_count"]
        imported_count = result["imported_count"]

        if imported_count == 0 and error_count > 0:
            log.status = "FAIL"
        elif error_count > 0:
            log.status = "PARTIAL"
        else:
            log.status = "SUCCESS"

        log.end_time = utc_now()
        log.rows_count = imported_count
        log.message = (
            f"ITAD 인기 게임 {imported_count}개 수집 완료, "
            f"소프트웨어 이동 {result['software_count']}개, "
            f"스킵 {result['skipped_count']}개, 오류 {error_count}개"
        )

        db.commit()
        queue_price_enrichment(db)

        return {
            "message": "ITAD 인기 게임 수집을 완료했습니다.",
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.post("/api/admin/used-market/collect")
def admin_collect_used_market(
    payload: UsedMarketCollectRequest,
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    if not USED_MARKET_ENABLED:
        raise HTTPException(status_code=503, detail="중고마켓 공개 수집은 운영 설정에서 꺼져 있습니다. USED_MARKET_ENABLED를 확인하세요.")
    if payload.limit_per_keyword < 1:
        raise HTTPException(
            status_code=400, detail="키워드당 수집 개수는 1개 이상이어야 합니다."
        )

    if payload.limit_per_keyword > 20:
        raise HTTPException(
            status_code=400, detail="키워드당 최대 20개까지만 수집할 수 있습니다."
        )

    platform = get_or_create_log_platform(db, "중고마켓")
    log = CrawlingLog(
        platform_id=platform.id,
        status="RUNNING",
        start_time=utc_now(),
        rows_count=0,
        message="중고마켓 매물 수집을 시작했습니다.",
    )

    db.add(log)
    db.commit()
    db.refresh(log)

    try:
        result = collect_used_market_items(
            db=db,
            keywords=payload.keywords,
            limit_per_keyword=payload.limit_per_keyword,
        )

        total_count = result["imported_count"] + result["updated_count"]

        log.status = "SUCCESS" if result["error_count"] == 0 else "PARTIAL"
        log.end_time = utc_now()
        log.rows_count = total_count
        log.message = (
            f"중고마켓 {result['imported_count']}개 신규 수집, "
            f"{result['updated_count']}개 갱신, "
            f"14일 초과 {result['deleted_stale_count']}개 삭제, "
            f"스킵 {result['skipped_count']}개, "
            f"오류 {result['error_count']}개"
        )
        log.message += " · " + " / ".join(f"{row['source']}: {row['collected_count']}개 ({row['status']}) {row.get('message', '')}" for row in result["source_results"])

        db.commit()

        return {
            "message": log.message,
            "result": result,
            "log": format_crawling_log(log),
        }

    except Exception as error:
        db.rollback()

        fail_log = db.query(CrawlingLog).filter(CrawlingLog.id == log.id).first()

        if fail_log:
            fail_log.status = collection_error_status(error)
            fail_log.end_time = utc_now()
            fail_log.rows_count = 0
            fail_log.message = str(error)
            db.commit()

        raise HTTPException(status_code=503 if isinstance(error, SourceRequestError) else 400, detail=str(error))


@app.delete("/api/admin/dev/reset-collected-data", dependencies=[Depends(development_only)])
def reset_collected_data(
    current_user: User = Depends(get_current_admin_user),
    db: Session = Depends(get_db),
):
    try:
        # 사용자 계정은 유지하고, 수집/표시 데이터만 초기화합니다.
        deleted_counts = {}

        deleted_counts["notifications"] = db.query(Notification).delete()
        deleted_counts["wishlist_items"] = db.query(WishlistItem).delete()

        deleted_counts["prediction_results"] = db.query(PredictionResult).delete()
        deleted_counts["price_tests"] = db.query(GamePriceTest).delete()
        deleted_counts["price_snapshots"] = db.query(PriceSnapshot).delete()
        deleted_counts["game_tags"] = db.query(GameTag).delete()
        deleted_counts["used_market_summaries"] = db.query(UsedMarketSummary).delete()
        deleted_counts["games"] = db.query(Game).delete()

        deleted_counts["used_market_items"] = db.query(UsedMarketItem).delete()

        deleted_counts["software_promotions"] = db.query(SoftwarePromotion).delete()
        deleted_counts["software"] = db.query(Software).delete()

        deleted_counts["crawling_logs"] = db.query(CrawlingLog).delete()

        # 수집 로그와 가격 스냅샷에서만 쓰던 플랫폼도 초기화합니다.
        # User는 절대 삭제하지 않습니다.
        deleted_counts["platforms"] = db.query(Platform).delete()

        db.commit()

        return {
            "message": "게임, 소프트웨어, 중고마켓, 수집 로그 데이터를 모두 초기화했습니다.",
            "deleted_counts": deleted_counts,
        }

    except Exception as error:
        db.rollback()
        raise HTTPException(
            status_code=400,
            detail=f"수집 데이터 초기화에 실패했습니다: {str(error)}",
        )
