"""외부 수집 요청의 간격과 서버가 지정한 재시도 대기 시간을 지킵니다."""

import math
import re
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from threading import Lock

import requests


class SourceRequestError(RuntimeError):
    def __init__(self, source, status_code, retry_after=0):
        self.source = source
        self.status_code = status_code
        self.retry_after = max(0, math.ceil(retry_after))
        detail = {
            401: "API 키 인증을 확인해 주세요",
            403: "API 접근 권한 또는 원본의 접속 제한을 확인해 주세요",
            429: "요청 한도에 도달했습니다",
        }.get(status_code, "원본 서버 응답을 확인해 주세요")
        wait = f" · {self.retry_after}초 후 재시도" if self.retry_after else ""
        super().__init__(f"{source} HTTP {status_code}: {detail}{wait}")


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

    def __init__(self, source, interval):
        self.source = source
        self.interval = interval
        self._lock = Lock()
        self._next_request_at = 0.0
        self._blocked_until = 0.0
        self._blocked_status = 429

    def retry_after(self):
        with self._lock:
            return max(0, math.ceil(self._blocked_until - time.monotonic()))

    def pause(self, seconds, status_code=429):
        with self._lock:
            until = time.monotonic() + seconds
            if until > self._blocked_until:
                self._blocked_until = until
                self._blocked_status = status_code

    def wait(self):
        with self._lock:
            now = time.monotonic()
            if now < self._blocked_until:
                raise SourceRequestError(
                    self.source, self._blocked_status, self._blocked_until - now
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
    text = re.sub(r"https?://\S+", "[원본 주소 생략]", text)
    text = re.sub(r"(?i)\b(api[_-]?key|token|password|secret)\s*[:=]\s*[^\s,;]+", r"\1=[숨김]", text)
    return " ".join(text.split())[:200] or "응답 확인 필요"
