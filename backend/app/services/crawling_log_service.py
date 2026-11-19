"""수집 로그만 최근 72시간 보관합니다. 수집 데이터에는 접근하지 않습니다."""

from datetime import timedelta

from app.models import CrawlingLog
from app.utils.time import utc_now

LOG_RETENTION_DAYS = 3


def finish_crawling_log(db, log, status, message, *, group_key=None):
    now = utc_now()
    log.status = status
    log.message = message
    log.end_time = now
    log.last_event_at = now
    if group_key and status in {"WAITING", "BLOCKED", "WARNING"}:
        existing = (db.query(CrawlingLog).filter(
            CrawlingLog.group_key == group_key,
            CrawlingLog.platform_id == log.platform_id,
            CrawlingLog.start_time >= now - timedelta(hours=1),
            CrawlingLog.id != log.id,
        ).order_by(CrawlingLog.id.desc()).with_for_update().first())
        if existing:
            existing.repeat_count = (existing.repeat_count or 1) + 1
            existing.last_event_at = now
            existing.end_time = now
            existing.message = message
            db.delete(log)
            return existing
        log.group_key = group_key
    return log


def prune_crawling_logs(db, *, now=None):
    cutoff = (now or utc_now()) - timedelta(days=LOG_RETENTION_DAYS)
    return (
        db.query(CrawlingLog)
        .filter(CrawlingLog.start_time < cutoff, CrawlingLog.status != "RUNNING")
        .delete(synchronize_session=False)
    )
