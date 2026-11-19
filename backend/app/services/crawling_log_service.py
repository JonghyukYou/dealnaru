"""수집 로그만 최근 72시간 보관합니다. 수집 데이터에는 접근하지 않습니다."""

from datetime import timedelta

from app.models import CrawlingLog
from app.utils.time import utc_now

LOG_RETENTION_DAYS = 3


def prune_crawling_logs(db, *, now=None):
    cutoff = (now or utc_now()) - timedelta(days=LOG_RETENTION_DAYS)
    return (
        db.query(CrawlingLog)
        .filter(CrawlingLog.start_time < cutoff, CrawlingLog.status != "RUNNING")
        .delete(synchronize_session=False)
    )
