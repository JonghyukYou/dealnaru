"""외부 수집 요청의 간격과 서버가 지정한 재시도 대기 시간을 지킵니다."""

import math
import re
import time
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from threading import Lock

import requests
from sqlalchemy.exc import IntegrityError

from app.database import SessionLocal
from app.models import CollectionSourceState
from app.utils.time import to_utc_iso_z, utc_now


class SourceRequestError(RuntimeError):
    def __init__(self, source, status_code, retry_after=0, *, detail=None, deferred=False):
        self.source = source
        self.status_code = status_code
        self.retry_after = max(0, math.ceil(retry_after))
        self.deferred = deferred
        self.detail = safe_source_text(detail) if detail else None
        description = {
            400: "요청 정보 확인이 필요합니다",
            401: "API 키 인증을 확인해 주세요",
            403: "API 접근 권한 또는 원본의 접속 제한을 확인해 주세요",
            404: "해당 게임 정보를 다시 확인해 주세요",
            429: "요청 한도에 도달했습니다",
        }.get(status_code, "원본 서버 응답을 확인해 주세요")
        wait = f" · {self.retry_after}초 후 재시도" if self.retry_after else ""
        extra = f" · {self.detail}" if self.detail else ""
        super().__init__(f"{source} HTTP {status_code}: {description}{wait}{extra}")


def safe_source_text(value, *, secrets=()):
    text = str(value or "")
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[숨김]")
    text = re.sub(r"https?://\S+", "[원본 주소 생략]", text)
    text = re.sub(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", "[이메일 숨김]", text, flags=re.IGNORECASE)
    text = re.sub(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[숨김]", text)
    return " ".join(text.split())[:200]


def response_error_detail(response, *, secrets=()):
    try:
        data = response.json()
    except (ValueError, TypeError, AttributeError):
        return None  # HTML 차단 페이지나 전체 본문은 공개하지 않습니다.
    parts = []
    def collect(value, depth=0):
        if depth > 4:
            return
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, dict):
            for key in ("message", "reason", "error", "detail", "errors", "code"):
                if key in value:
                    collect(value[key], depth + 1)
        elif isinstance(value, list):
            for item in value[:3]:
                collect(item, depth + 1)
    collect(data)
    return safe_source_text(" / ".join(parts), secrets=secrets) or None


def source_pause(source):
    with SessionLocal() as db:
        state = db.get(CollectionSourceState, source)
        if not state:
            return None
        return {"source": source, "paused_until": state.paused_until,
                "http_status": state.http_status, "detail": state.detail,
                "min_interval": float(state.min_interval or 0)}


def save_source_pause(source, seconds, status_code, *, detail=None, min_interval=None):
    until = utc_now() + timedelta(seconds=seconds)
    with SessionLocal() as db:
        row = db.query(CollectionSourceState).filter_by(source=source).with_for_update().first()
        if row is None:
            row = CollectionSourceState(source=source)
            db.add(row)
            try:
                db.flush()
            except IntegrityError:
                db.rollback()
                row = db.query(CollectionSourceState).filter_by(source=source).with_for_update().one()
        if not row.paused_until or until >= row.paused_until:
            row.paused_until = until
            row.http_status = status_code
            row.detail = safe_source_text(detail)
        if min_interval is not None:
            row.min_interval = max(float(row.min_interval or 0), min_interval)
        row.updated_at = utc_now()
        db.commit()


def source_statuses(db):
    return [{"source": row.source, "http_status": row.http_status,
             "retry_at": to_utc_iso_z(row.paused_until), "message": row.detail,
             "status": "BLOCKED" if row.http_status in {401, 403} else "WAITING"}
            for row in db.query(CollectionSourceState).filter(CollectionSourceState.paused_until > utc_now()).all()
            if row.source in {"Steam", "ITAD"}]


def retry_after_seconds(value, default=60):
    try:
        seconds = float(value)
        if math.isfinite(seconds):
            return max(1, math.ceil(seconds))
    except (TypeError, ValueError, OverflowError):
        pass
    try:
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)
        return max(1, math.ceil((when - datetime.now(timezone.utc)).total_seconds()))
    except (TypeError, ValueError, OverflowError, IndexError):
        return default


class SourceRequestGate:
    """한 프로세스의 여러 수집 스레드가 같은 요청 한도를 공유합니다."""

    def __init__(self, source, interval, *, persistent=False):
        self.source = source
        self.interval = interval
        self._lock = Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0
        self._blocked_status = 429
        self.persistent = persistent
        self._state_check_at = 0.0

    def _refresh_state(self):
        if not self.persistent or time.monotonic() < self._state_check_at:
            return
        state = source_pause(self.source)
        with self._lock:
            now = time.monotonic()
            if state:
                self.interval = max(self.interval, state["min_interval"])
                if state["paused_until"]:
                    remaining = (state["paused_until"] - utc_now()).total_seconds()
                    if remaining > 0 and now + remaining > self._blocked_until:
                        self._blocked_until = now + remaining
                        self._blocked_status = state["http_status"] or 429
            self._state_check_at = now + 2

    def retry_after(self):
        self._refresh_state()
        with self._lock:
            return max(0, math.ceil(self._blocked_until - time.monotonic()))

    def ensure_available(self):
        self._refresh_state()
        with self._lock:
            remaining = self._blocked_until - time.monotonic()
            if remaining > 0:
                raise SourceRequestError(self.source, self._blocked_status, remaining, deferred=True)

    def pause(self, seconds, status_code=429, *, detail=None):
        with self._lock:
            if self.persistent and status_code == 429:
                self.interval = min(30, max(1.0, self.interval * 2))
            until = time.monotonic() + seconds
            if until > self._blocked_until:
                self._blocked_until = until
                self._blocked_status = status_code
        if self.persistent:
            save_source_pause(self.source, seconds, status_code, detail=detail, min_interval=self.interval)

    def wait(self):
        self._refresh_state()
        with self._lock:
            now = time.monotonic()
            if now < self._blocked_until:
                raise SourceRequestError(
                    self.source, self._blocked_status, self._blocked_until - now, deferred=True
                )
            delay = self._next_request_at - now
            if delay > 0:
                time.sleep(delay)
            self._next_request_at = time.monotonic() + self.interval


def collection_error_detail(error):
    """키, 요청 URL, SQL 파라미터 대신 진단에 필요한 오류만 공개합니다."""
    if isinstance(error, SourceRequestError):
        return str(error)
    response = getattr(error, "response", None)
    status = getattr(response, "status_code", None)
    if status:
        return f"HTTP {status}"
    if isinstance(error, requests.Timeout):
        return "원본 서버 응답 시간 초과"
    if isinstance(error, requests.ConnectionError):
        return "원본 서버 연결 실패"
    return type(error).__name__


def collection_result_error(result):
    errors = result.get("errors") or []
    if not result.get("error_count") or not errors:
        return None
    first = errors[0]
    text = str(first.get("error", "응답 확인 필요") if isinstance(first, dict) else first)
    return safe_source_text(text) or "응답 확인 필요"


def error_http_status(error):
    if isinstance(error, SourceRequestError):
        return error.status_code
    match = re.search(r"\bHTTP\s+(\d{3})\b|\b(\d{3}) (?:Client|Server) Error", str(error))
    return int(next(value for value in match.groups() if value)) if match else None


def collection_error_status(error):
    code = error_http_status(error)
    if code in {429, 500, 502, 503, 504}:
        return "WAITING"
    if code in {401, 403}:
        return "BLOCKED"
    if code in {400, 404}:
        return "WARNING"
    return "FAIL"


def collection_result_status(result, rows=0):
    if result.get("status") in {"WAITING", "BLOCKED", "WARNING", "PARTIAL"}:
        return result["status"]
    if not result.get("error_count"):
        return "SUCCESS"
    if rows:
        return "PARTIAL"
    errors = result.get("errors") or []
    statuses = {collection_error_status(row.get("error", "") if isinstance(row, dict) else row) for row in errors}
    if len(statuses) == 1:
        return statuses.pop()
    return "FAIL"
