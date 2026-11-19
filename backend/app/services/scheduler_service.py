"""서버 생명주기와 연결된 자동 갱신. 일정·실행 잠금은 DB에 보존합니다."""

import json
import logging
import os
import time
from datetime import timedelta
from threading import Event, Thread
from uuid import uuid4

from sqlalchemy import and_, or_
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.services import itad_collect_service as itad_source
from app.services import steam_collect_service as steam_source
from app.models import AutoRefreshState, CrawlingLog, Game, Platform
from app.services.catalog_filter_service import cleanup_invalid_catalog
from app.services.crawling_log_service import prune_crawling_logs, finish_crawling_log
from app.services.catalog_sync_service import (
    enrich_itad_catalog_metadata,
    link_catalog_to_itad,
)
from app.services.epic_collect_service import (
    collect_epic_popular_games,
    collect_existing_epic_prices,
)
from app.services.history_sync_service import history_worker_active, queue_history_sync
from app.services.itad_collect_service import (
    collect_current_prices_for_existing_itad_games,
    import_itad_deals,
)
from app.services.price_freshness_service import deactivate_stale_current_prices
from app.services.source_request_service import (
    collection_error_detail, collection_result_error, collection_error_status,
    collection_result_status, error_http_status, source_statuses, SourceRequestError,
)
from app.services.steam_collect_service import (
    collect_existing_steam_prices,
    collect_steam_featured_games,
)
from app.services.subscription_service import refresh_subscription_offers
from app.services.used_market_collect_service import delete_stale_used_market_items
from app.utils.time import to_utc_iso_z, utc_now

logger = logging.getLogger(__name__)
DEFAULT_INTERVAL_MINUTES = 360
MIN_INTERVAL_MINUTES = 30
MAX_INTERVAL_MINUTES = 10080
LEASE_MINUTES = 15
_stop = Event()
_wake = Event()
_worker = None


def initialize_state(db):
    state = db.get(AutoRefreshState, 1)
    if state:
        return state
    enabled = os.getenv("AUTO_REFRESH_ENABLED", "true").lower() in {"true", "1", "yes"}
    if os.getenv("APP_ENV", "").lower() == "test":
        enabled = False
    try:
        interval = int(
            os.getenv("AUTO_REFRESH_INTERVAL_MINUTES", str(DEFAULT_INTERVAL_MINUTES))
        )
    except ValueError:
        interval = DEFAULT_INTERVAL_MINUTES
    interval = min(MAX_INTERVAL_MINUTES, max(MIN_INTERVAL_MINUTES, interval))
    state = AutoRefreshState(
        id=1,
        enabled=enabled,
        interval_minutes=interval,
        next_run_at=utc_now() + timedelta(seconds=10) if enabled else None,
    )
    db.add(state)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
    return db.get(AutoRefreshState, 1)


def get_scheduler_status(db):
    state = initialize_state(db)
    running = bool(
        state.lease_token and state.lease_until and state.lease_until > utc_now()
    )
    return {
        "enabled": state.enabled,
        "interval_minutes": state.interval_minutes,
        "include_software": state.include_software,
        "new_games_per_source": state.new_games_per_source,
        "is_collecting": running,
        "run_requested": state.run_requested,
        "next_run_at": to_utc_iso_z(state.next_run_at) if state.enabled else None,
        "last_started_at": to_utc_iso_z(state.last_started_at),
        "last_finished_at": to_utc_iso_z(state.last_finished_at),
        "last_status": "INTERRUPTED"
        if state.last_status == "RUNNING" and not running
        else state.last_status,
        "last_result": json.loads(state.last_result) if state.last_result else None,
        "worker_active": bool(_worker and _worker.is_alive()),
        "catalog_synced_at": to_utc_iso_z(state.catalog_synced_at),
        "catalog_game_count": db.query(Game).count(),
        "itad_linked_count": db.query(Game).filter(Game.itad_id.isnot(None)).count(),
        "history_pending_count": db.query(Game)
        .filter(Game.itad_id.isnot(None), Game.history_synced_at.is_(None))
        .count(),
        "history_failed_count": db.query(Game)
        .filter(Game.history_error.isnot(None))
        .count(),
        "history_queued_count": db.query(Game)
        .filter(Game.history_queued_at.isnot(None))
        .count(),
        "history_running_count": db.query(Game)
        .filter(Game.history_lease_until > utc_now())
        .count(),
        "history_worker_active": history_worker_active(),
        "message": "갱신 중"
        if running
        else "실행 대기"
        if state.run_requested
        else "자동 갱신 켜짐"
        if state.enabled
        else "자동 갱신 꺼짐",
        "source_states": source_statuses(db),
    }


def configure_scheduler(
    db, enabled, interval_minutes, include_software, new_games_per_source
):
    initialize_state(db)
    db.query(AutoRefreshState).filter(AutoRefreshState.id == 1).update(
        {
            AutoRefreshState.enabled: enabled,
            AutoRefreshState.interval_minutes: interval_minutes,
            AutoRefreshState.include_software: include_software,
            AutoRefreshState.new_games_per_source: new_games_per_source,
            AutoRefreshState.next_run_at: utc_now()
            + timedelta(minutes=interval_minutes)
            if enabled
            else None,
        },
        synchronize_session=False,
    )
    db.commit()
    db.expire_all()
    _wake.set()
    return get_scheduler_status(db)


def request_refresh(db, force_catalog=False):
    initialize_state(db)
    now = utc_now()
    updates = {AutoRefreshState.run_requested: True}
    if force_catalog:
        updates[AutoRefreshState.catalog_synced_at] = None
    changed = (
        db.query(AutoRefreshState)
        .filter(
            AutoRefreshState.id == 1,
            AutoRefreshState.run_requested.is_(False),
            or_(
                AutoRefreshState.lease_until.is_(None),
                AutoRefreshState.lease_until <= now,
            ),
        )
        .update(updates, synchronize_session=False)
    )
    db.commit()
    db.expire_all()
    if not changed:
        raise ValueError("이미 갱신 중이거나 실행 요청이 대기 중입니다.")
    _wake.set()
    return get_scheduler_status(db)


def claim_due_refresh(db, now=None):
    now = now or utc_now()
    initialize_state(db)
    token = uuid4().hex
    changed = (
        db.query(AutoRefreshState)
        .filter(
            AutoRefreshState.id == 1,
            or_(
                AutoRefreshState.lease_until.is_(None),
                AutoRefreshState.lease_until <= now,
            ),
            or_(
                AutoRefreshState.run_requested.is_(True),
                and_(
                    AutoRefreshState.enabled.is_(True),
                    AutoRefreshState.next_run_at <= now,
                ),
            ),
        )
        .update(
            {
                AutoRefreshState.lease_token: token,
                AutoRefreshState.lease_until: now + timedelta(minutes=LEASE_MINUTES),
                AutoRefreshState.last_started_at: now,
                AutoRefreshState.last_status: "RUNNING",
                AutoRefreshState.run_requested: False,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    return token if changed else None


def heartbeat(token):
    with SessionLocal() as db:
        changed = (
            db.query(AutoRefreshState)
            .filter(AutoRefreshState.id == 1, AutoRefreshState.lease_token == token)
            .update(
                {
                    AutoRefreshState.lease_until: utc_now()
                    + timedelta(minutes=LEASE_MINUTES)
                },
                synchronize_session=False,
            )
        )
        db.commit()
    if not changed or _stop.is_set():
        raise RuntimeError("갱신 작업이 중단되었습니다.")


def finish_refresh(token, result):
    with SessionLocal() as db:
        state = db.get(AutoRefreshState, 1)
        if state is None or state.lease_token != token:
            return
        failed = any(step.get("status") == "FAIL" for step in result["steps"])
        attention = any(step.get("status") in {"PARTIAL", "WAITING", "BLOCKED", "WARNING"}
                        or step.get("error_count", 0) for step in result["steps"])
        succeeded = any(step.get("updated_count", 0) for step in result["steps"])
        state.last_status = (
            ("PARTIAL" if succeeded else "FAIL") if failed else
            ("PARTIAL" if succeeded else "WAITING") if attention else "SUCCESS"
        )
        state.last_result = json.dumps(result, ensure_ascii=False, default=str)
        state.last_finished_at = utc_now()
        state.next_run_at = (
            utc_now() + timedelta(minutes=state.interval_minutes)
            if state.enabled
            else None
        )
        state.lease_token = None
        state.lease_until = None
        db.commit()


def run_logged_collection(platform_name, label, callback):
    """원본별 독립 트랜잭션. 한 출처가 실패해도 다른 출처는 계속합니다."""
    with SessionLocal() as db:
        platform = db.query(Platform).filter(Platform.name == platform_name).first()
        if platform is None:
            platform = Platform(name=platform_name)
            db.add(platform)
            db.flush()
        log = CrawlingLog(
            platform_id=platform.id, status="RUNNING", message=f"자동 갱신: {label}"
        )
        db.add(log)
        db.commit()
        log_id = log.id
        try:
            result = callback(db)
            rows = int(
                result.get(
                    "updated_count",
                    result.get("imported_count", result.get("removed_count", 0)),
                )
            )
            error_count = int(result.get("error_count", 0))
            log = db.get(CrawlingLog, log_id)
            status = collection_result_status(result, rows)
            log.rows_count = rows
            message = f"자동 갱신: {label} · 갱신 {rows}개 · 제외 {result.get('skipped_count', 0)}개 · 오류 {error_count}개"
            reason = collection_result_error(result) or result.get("message")
            if reason:
                message += f" · {reason}"
            log = finish_crawling_log(db, log, status, message,
                group_key=f"auto:{error_http_status(reason)}" if status in {"WAITING", "BLOCKED", "WARNING"} else None)
            db.commit()
            return {
                "source": label,
                "status": log.status,
                "updated_count": rows,
                "error_count": error_count,
                "skipped_count": result.get("skipped_count", 0),
                "games_synced": result.get("games_synced", 0),
                "skip_reasons": result.get("skip_reasons", {}),
                "message": reason,
            }
        except Exception as error:
            db.rollback()
            status = collection_error_status(error)
            if status == "FAIL":
                logger.exception("자동 갱신 출처 실패: %s", label)
            log = db.get(CrawlingLog, log_id)
            reason = collection_error_detail(error)
            log = finish_crawling_log(db, log, status, f"자동 갱신: {label} · {reason}",
                group_key=f"auto:{error_http_status(error)}" if status in {"WAITING", "BLOCKED", "WARNING"} else None)
            db.commit()
            return {
                "source": label,
                "status": status,
                "error_count": 0 if getattr(error, "deferred", False) else 1,
                "updated_count": 0,
                "message": reason,
            }


def run_auto_collect_once():
    with SessionLocal() as db:
        token = claim_due_refresh(db)
        if not token:
            return None
        state = db.get(AutoRefreshState, 1)
        db.refresh(state)
        new_count, include_software = state.new_games_per_source, state.include_software
        bootstrap = state.catalog_synced_at is None
        sync_catalog = bootstrap or state.catalog_synced_at < utc_now() - timedelta(
            hours=20
        )
    result = {"steps": []}
    paused_sources = {}
    known_itad_ids = set()

    def step(platform, label, callback):
        heartbeat(token)
        gate = (itad_source.request_gate if platform in {"IsThereAnyDeal", "GOG", "Fanatical"}
                and label not in {"번들·비게임 정리", "기존 게임 이력 우선 수집 예약", "신규 게임 이력 수집 예약"}
                else steam_source.request_gate if platform == "Steam" else None)
        if gate:
            try:
                gate.ensure_available()
            except SourceRequestError as error:
                if gate.source in paused_sources:
                    row = paused_sources[gate.source]
                    row["deferred_steps"] = row.get("deferred_steps", 0) + 1
                    return row
                def deferred(_db):
                    raise error
                row = run_logged_collection(platform, f"{gate.source} 수집 대기", deferred)
                paused_sources[gate.source] = row
                result["steps"].append(row)
                return row
        row = run_logged_collection(platform, label, callback)
        result["steps"].append(row)
        if gate and gate.retry_after():
            paused_sources[gate.source] = row
        return row

    try:
        step(
            "IsThereAnyDeal",
            "번들·비게임 정리",
            lambda db: cleanup_invalid_catalog(db, apply=True),
        )
        if os.getenv("ITAD_API_KEY"):
            step("IsThereAnyDeal", "기존 게임 이력 우선 수집 예약", queue_history_sync)
            with SessionLocal() as db:
                current_ids = [gid for (gid,) in db.query(Game.id).filter(Game.itad_id.isnot(None)).order_by(Game.id).all()]
            known_itad_ids = set(current_ids)
            # 신규 게임의 메타데이터보다 이미 등록한 게임의 현재 가격을 먼저 확인합니다.
            for start in range(0, len(current_ids), 200):
                ids = current_ids[start:start + 200]
                step("IsThereAnyDeal", f"기존 구매처 현재가 {start + 1}~",
                     lambda db, ids=ids: collect_current_prices_for_existing_itad_games(db, limit=200, game_ids=ids))
        if sync_catalog:
            steam = step(
                "Steam",
                "Steam 인기 본편",
                lambda db: collect_steam_featured_games(
                    db, max_items=new_count, catalog_mode=True
                ),
            )
            epic = step(
                "Epic Game Store",
                "Epic 인기 본편",
                lambda db: collect_epic_popular_games(db, max_items=new_count),
            )
            if steam["status"] == "SUCCESS" and epic["status"] == "SUCCESS":
                with SessionLocal() as db:
                    db.query(AutoRefreshState).filter(
                        AutoRefreshState.id == 1, AutoRefreshState.lease_token == token
                    ).update(
                        {AutoRefreshState.catalog_synced_at: utc_now()},
                        synchronize_session=False,
                    )
                    db.commit()
        step(
            "Steam",
            "Steam 신규 할인",
            lambda db: collect_steam_featured_games(db, max_items=new_count),
        )
        if os.getenv("ITAD_API_KEY"):
            for shop_id, name in ((35, "GOG"), (6, "Fanatical")):
                step(
                    name,
                    f"{name} 신규 할인",
                    lambda db, shop_id=shop_id: import_itad_deals(
                        db, limit=new_count, shop_ids=[shop_id]
                    ),
                )
        with SessionLocal() as db:
            steam_count = db.query(Game).filter(Game.steam_appid.isnot(None)).count()
            epic_count = db.query(Game).filter(Game.epic_offer_id.isnot(None)).count()
        for offset in range(0, steam_count, 100):
            step(
                "Steam",
                f"Steam 기존 가격 {offset + 1}~",
                lambda db, offset=offset: collect_existing_steam_prices(
                    db, max_items=100, offset=offset, delay_seconds=0.1
                ),
            )
        for offset in range(0, epic_count, 80):
            step(
                "Epic Game Store",
                f"Epic 기존 가격 {offset + 1}~",
                lambda db, offset=offset: collect_existing_epic_prices(
                    db, limit=80, offset=offset
                ),
            )
        if os.getenv("ITAD_API_KEY"):
            with SessionLocal() as db:
                unmatched = db.query(Game).filter(Game.itad_id.is_(None)).count()
            # 최초/명시적 카탈로그 보강은 전체 후보, 이후에는 80개씩 순환 연결.
            for _ in range(max(1, (unmatched + 79) // 80) if bootstrap else 1):
                step("IsThereAnyDeal", "Steam·Epic ITAD 연결", link_catalog_to_itad)
            with SessionLocal() as db:
                unverified = (
                    db.query(Game)
                    .filter(
                        Game.itad_id.isnot(None), Game.itad_info_checked_at.is_(None)
                    )
                    .count()
                )
            for _ in range(max(1, (unverified + 79) // 80) if bootstrap else 1):
                step(
                    "IsThereAnyDeal",
                    "ITAD 본편·메타데이터 확인",
                    enrich_itad_catalog_metadata,
                )
            with SessionLocal() as db:
                new_ids = [gid for (gid,) in db.query(Game.id).filter(Game.itad_id.isnot(None)).order_by(Game.id).all()
                           if gid not in known_itad_ids]
            for offset in range(0, len(new_ids), 200):
                step(
                    "IsThereAnyDeal",
                    f"신규 구매처 현재가 {offset + 1}~",
                    lambda db, ids=new_ids[offset:offset + 200]: (
                        collect_current_prices_for_existing_itad_games(
                            db, limit=200, game_ids=ids
                        )
                    ),
                )
            step("IsThereAnyDeal", "신규 게임 이력 수집 예약", queue_history_sync)
        else:
            result["steps"].append(
                {
                    "source": "ITAD 가격·이력",
                    "status": "SKIPPED",
                    "message": "ITAD_API_KEY 미설정",
                }
            )
        if include_software:
            step("프로그램·AI", "프로그램·AI 공식 혜택", refresh_subscription_offers)
        with SessionLocal() as db:
            result["stale_prices"] = len(
                deactivate_stale_current_prices(db, commit=True)
            )
            result["deleted_used_items"] = delete_stale_used_market_items(
                db, commit=True
            )
    except Exception:
        logger.exception("자동 갱신 실행 중단")
        result["steps"].append(
            {"source": "자동 갱신", "status": "FAIL", "error_count": 1}
        )
    finally:
        finish_refresh(token, result)
    return result


def _loop():
    next_log_cleanup = 0.0
    while not _stop.is_set():
        try:
            if time.monotonic() >= next_log_cleanup:
                with SessionLocal() as db:
                    prune_crawling_logs(db)
                    db.commit()
                next_log_cleanup = time.monotonic() + 3600
            run_auto_collect_once()
        except Exception:
            logger.exception("자동 갱신 스케줄 확인 실패; 다음 확인에서 재시도")
        _wake.wait(5)
        _wake.clear()


def initialize_scheduler():
    global _worker
    with SessionLocal() as db:
        state = initialize_state(db)
        if (
            state.enabled
            and state.catalog_synced_at is None
            and "Steam 인기 본편" not in (state.last_result or "")
        ):
            state.next_run_at = utc_now() + timedelta(seconds=10)
            db.commit()
    if os.getenv("APP_ENV", "").lower() == "test" or (_worker and _worker.is_alive()):
        return
    _stop.clear()
    _wake.clear()
    _worker = Thread(target=_loop, name="gamesoft-auto-refresh", daemon=True)
    _worker.start()


def shutdown_scheduler():
    if not _worker:
        return
    _stop.set()
    _wake.set()
    if _worker:
        _worker.join(timeout=3)
