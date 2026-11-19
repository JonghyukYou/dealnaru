"""가격 이력을 전체 수집과 독립적으로 보강하는 DB 대기열.

신규 게임은 자동 보강하고, 보고 있는 게임은 우선 처리합니다. 정기 전체 갱신을
꺼도 최초 보강과 명시적인 요청은 수행하며, 다중 프로세스는 게임별 임대로 중복을 막습니다.
"""

import logging
import os
from datetime import datetime, timedelta
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import or_

from app.database import SessionLocal
from app.models import CrawlingLog, Game, Platform, PriceSnapshot
from app.services import itad_collect_service as itad
from app.services.catalog_sync_service import link_catalog_to_itad
from app.services.source_request_service import collection_error_detail
from app.utils.time import utc_now

logger = logging.getLogger(__name__)
FULL_HISTORY_START = datetime(2005, 1, 1)
_stop = Event()
_wake = Event()
_worker = None


def has_history_key():
    return bool(itad.ITAD_API_KEY or os.getenv("ITAD_API_KEY"))


def queue_history_sync(
    db, game_id=None, *, priority=0, include_due=True, force_full=False
):
    if not has_history_key():
        return {"queued_count": 0, "updated_count": 0, "status": "unconfigured"}
    now = utc_now()
    query = db.query(Game).filter(
        or_(
            Game.itad_id.isnot(None),
            Game.steam_appid.isnot(None),
            Game.epic_offer_id.isnot(None),
        )
    )
    if game_id is not None:
        query = query.filter(Game.id == game_id)
    eligible = or_(
        Game.history_synced_at.is_(None),
        Game.history_start_at.is_(None),
        Game.history_start_at > FULL_HISTORY_START,
    )
    if include_due:
        eligible = or_(eligible, Game.history_synced_at < now - timedelta(hours=20))
    if not force_full:
        query = query.filter(
            eligible,
            or_(
                Game.history_attempted_at.is_(None),
                Game.history_attempted_at < now - timedelta(minutes=30),
            ),
        )
    queued = 0
    for game in query.all():
        if game.history_lease_until and game.history_lease_until > now:
            continue
        if force_full:
            game.history_start_at = None
        if game.history_queued_at is None:
            game.history_queued_at = now
            queued += 1
        game.history_priority = max(game.history_priority or 0, priority)
    # 이미 대기 중인 게임은 중복 생성 없이 우선순위만 올립니다.
    if game_id is not None and priority:
        game = db.get(Game, game_id)
        if game and game.history_queued_at:
            game.history_priority = max(game.history_priority or 0, priority)
    db.flush()
    _wake.set()
    return {
        "queued_count": queued,
        "updated_count": queued,
        "status": "queued" if queued else "up_to_date",
    }


def history_status(game):
    if game.history_lease_until and game.history_lease_until > utc_now():
        return "running"
    if game.history_queued_at:
        return "queued"
    if game.history_error:
        return "needs_check"
    if game.history_synced_at:
        return "synced"
    if not (game.itad_id or game.steam_appid or game.epic_offer_id):
        return "unlinked"
    return "pending" if has_history_key() else "unconfigured"


def claim_next_history_job(db):
    now = utc_now()
    free = or_(Game.history_lease_until.is_(None), Game.history_lease_until <= now)
    candidates = (
        db.query(Game.id)
        .filter(Game.history_queued_at.isnot(None), free)
        .order_by(Game.history_priority.desc(), Game.history_queued_at, Game.id)
        .limit(20)
        .all()
    )
    for (gid,) in candidates:
        token = uuid4().hex
        changed = (
            db.query(Game)
            .filter(Game.id == gid, Game.history_queued_at.isnot(None), free)
            .update(
                {
                    Game.history_lease_token: token,
                    Game.history_lease_until: now + timedelta(minutes=5),
                },
                synchronize_session=False,
            )
        )
        db.commit()
        if changed:
            return gid, token
    return None


def run_next_history_job():
    if not has_history_key() or itad.request_gate.retry_after():
        return None
    with SessionLocal() as db:
        job = claim_next_history_job(db)
        if not job:
            return None
        gid, token = job
        platform = db.query(Platform).filter(Platform.name == "IsThereAnyDeal").first()
        if platform is None:
            platform = Platform(name="IsThereAnyDeal")
            db.add(platform)
            db.flush()
        game = db.get(Game, gid)
        title = game.title
        log = CrawlingLog(
            platform_id=platform.id,
            status="RUNNING",
            message=f"가격 이력 우선 보강: {title}",
        )
        db.add(log)
        db.commit()
        log_id = log.id
        try:
            if not game.itad_id:
                link_catalog_to_itad(db, limit=1, game_ids=[gid])
                db.commit()
                game = db.get(Game, gid)
                if game is None:
                    result = {
                        "history_rows_imported": 0,
                        "error_count": 0,
                        "merged": True,
                    }
                elif not game.itad_id:
                    game.history_attempted_at = utc_now()
                    game.history_error = "ITAD에 대응되는 본편 이력을 찾지 못했습니다. 다음 요청에서 재확인합니다."
                    result = {"history_rows_imported": 0, "error_count": 1}
                else:
                    result = None
            else:
                result = None
            if result is None:
                result = itad.collect_history_for_existing_itad_games(
                    db, limit=1, game_ids=[gid], claim_token=token
                )
                db.commit()
                # 가격만 초기화한 DB도 정상가/할인가를 다시 확인할 수 있도록 복구합니다.
                if (
                    not db.query(PriceSnapshot.id)
                    .filter(
                        PriceSnapshot.game_id == gid, PriceSnapshot.is_current.is_(True)
                    )
                    .first()
                ):
                    itad.collect_current_prices_for_existing_itad_games(
                        db, limit=1, game_ids=[gid]
                    )
            log = db.get(CrawlingLog, log_id)
            log.rows_count = result.get("history_rows_imported", 0)
            log.status = (
                ("PARTIAL" if log.rows_count else "FAIL")
                if result.get("error_count")
                else "SUCCESS"
            )
            log.end_time = utc_now()
            log.message = f"가격 이력 우선 보강: {title} · 실제 변경 이력 {log.rows_count}건 · 오류 {result.get('error_count', 0)}개"
            if result.get("error_count") and game is not None and game.history_error:
                log.message += f" · {game.history_error}"
            db.commit()
        except Exception as error:
            db.rollback()
            game = db.get(Game, gid)
            if game and game.history_lease_token == token:
                game.history_attempted_at = utc_now()
                game.history_error = f"가격 이력 보강 실패: {collection_error_detail(error)}"
            log = db.get(CrawlingLog, log_id)
            if log:
                log.status = "FAIL"
                log.end_time = utc_now()
                log.message = (
                    f"가격 이력 우선 보강 실패: {title} · {collection_error_detail(error)}"
                )
            db.commit()
            result = {"history_rows_imported": 0, "error_count": 1}
            logger.exception("가격 이력 우선 보강 실패: game_id=%s", gid)
        finally:
            db.query(Game).filter(
                Game.id == gid, Game.history_lease_token == token
            ).update(
                {
                    Game.history_lease_token: None,
                    Game.history_lease_until: None,
                    Game.history_queued_at: None,
                    Game.history_priority: 0,
                },
                synchronize_session=False,
            )
            db.commit()
    return result


def history_worker_active():
    return bool(_worker and _worker.is_alive())


def _loop():
    while not _stop.is_set():
        try:
            retry_after = itad.request_gate.retry_after()
            if retry_after:
                # 제한 중에는 다음 게임을 꺼내지 않아 동일 오류가 수백 줄 쌓이지 않습니다.
                _wake.wait(min(30, retry_after))
                _wake.clear()
                continue
            with SessionLocal() as db:
                queue_history_sync(db, include_due=False)
                db.commit()
            if run_next_history_job():
                continue
        except Exception:
            logger.exception("가격 이력 대기열 확인 실패; 다음 확인에서 재시도")
        _wake.wait(5)
        _wake.clear()


def initialize_history_worker():
    global _worker
    if os.getenv("APP_ENV", "").lower() == "test" or history_worker_active():
        return
    _stop.clear()
    _wake.clear()
    _worker = Thread(target=_loop, name="gamesoft-history-backfill", daemon=True)
    _worker.start()


def shutdown_history_worker():
    _stop.set()
    _wake.set()
    if _worker:
        _worker.join(timeout=3)
