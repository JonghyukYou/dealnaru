"""할인율과 무관하게 본편 게임 / 번들·강좌·DLC를 구분합니다."""

import re
import unicodedata

from sqlalchemy import text
from sqlalchemy.orm import selectinload

from app.models import CrawlingLog, Game, Platform
from app.utils.time import utc_now

BUNDLE = re.compile(
    r"\bbundles?\b|\b(?:[2-9]|\d{2,})\s*[- ]?pack\b|번들|묶음\s*(?:상품|팩)|합본\s*팩",
    re.I,
)
NON_GAME = re.compile(
    r"\b(?:e-?books?|audiobooks?|masterclass|certification|training\s+course|"
    r"online\s+course|learning\s+collection|soundtrack|art\s*book|season\s+pass)\b|"
    r"\bDLC\b|전자책|강좌\s*(?:패키지|모음)|사운드트랙",
    re.I,
)
NON_GAME_TAGS = {
    "software",
    "ebook",
    "ebooks",
    "audio production",
    "video production",
    "photo editing",
}


def non_game_reason(title, tags=(), product_type=None, url=None):
    title = unicodedata.normalize("NFKC", title or "")
    kind = str(product_type or "").lower().strip()
    if kind and kind != "game":
        return f"본편 게임이 아닌 상품 타입: {kind}"
    if BUNDLE.search(title):
        return "번들·멀티팩"
    if NON_GAME.search(title):
        return "강좌·전자책·추가 콘텐츠"
    if any(str(tag).lower().strip() in NON_GAME_TAGS for tag in tags or []):
        return "프로그램·교육 상품 태그"
    if re.search(r"store\.steampowered\.com/(?:bundle|sub)/", url or "", re.I):
        return "Steam 번들·패키지 링크"
    return None


def cleanup_invalid_catalog(db, apply=False):
    """일치하는 비게임만 삭제. ORM cascade로 가격/태그/찜/연결 알림도 정리합니다."""
    if apply and db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(5092028)"))
    candidates = []
    for game in db.query(Game).options(selectinload(Game.tags)).order_by(Game.id).all():
        reason = non_game_reason(
            game.title,
            [tag.name for tag in game.tags],
            game.product_type,
            game.store_url,
        )
        if reason:
            candidates.append((game, reason))
    items = [
        {"id": game.id, "title": game.title, "reason": reason}
        for game, reason in candidates
    ]
    if apply and candidates:
        platform = db.query(Platform).filter(Platform.name == "IsThereAnyDeal").first()
        if platform is None:
            platform = Platform(name="IsThereAnyDeal")
            db.add(platform)
            db.flush()
        for game, _ in candidates:
            db.delete(game)
        db.add(
            CrawlingLog(
                platform_id=platform.id,
                status="SUCCESS",
                end_time=utc_now(),
                rows_count=len(items),
                message=f"비게임 정리: 번들·강좌·DLC {len(items)}개 삭제 · "
                + ", ".join(item["title"] for item in items[:8]),
            )
        )
        db.flush()
    return {
        "removed_count": len(items) if apply else 0,
        "matched_count": len(items),
        "items": items,
        "applied": apply,
    }
