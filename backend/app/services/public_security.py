"""Request checks and bounded rate limits for the single public API worker."""
from collections import OrderedDict, deque
from time import monotonic
from urllib.parse import urlsplit

from fastapi.responses import JSONResponse

from app.settings import ALLOWED_ORIGINS, PRODUCTION

AUTH_LIMITS = {"login": (12, 60), "register": (5, 300), "resend-verification": (5, 300), "verify-email": (15, 60), "withdraw": (5, 300)}
MAX_CLIENT_BUCKETS = 4000


class RequestBudget:
    def __init__(self):
        self.clients = OrderedDict()
        self.totals = {}

    def admit(self, key, limit, period):
        now = monotonic()
        while self.clients and next(iter(self.clients.values()))[0] < now - 300:
            self.clients.popitem(last=False)
        pool = self.totals.setdefault(key[1], deque())
        while pool and pool[0] <= now - 60:
            pool.popleft()
        if len(pool) >= (48 if key[1] == "login" else 100):
            return 60
        if key not in self.clients and len(self.clients) >= MAX_CLIENT_BUCKETS:
            return 60
        _, hits = self.clients.get(key, (now, deque()))
        while hits and hits[0] <= now - period:
            hits.popleft()
        if len(hits) >= limit:
            return max(1, int(hits[0] + period - now) + 1)
        hits.append(now)
        pool.append(now)
        self.clients[key] = (now, hits)
        self.clients.move_to_end(key)
        return 0


budget = RequestBudget()


async def protect_public_api(request, call_next):
    path = request.url.path
    if not path.startswith("/api/"):
        return await call_next(request)

    rejected = None
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        origin = request.headers.get("origin")
        fetch_site = request.headers.get("sec-fetch-site")
        if (origin is not None and origin not in ALLOWED_ORIGINS) or (
            PRODUCTION and (fetch_site == "cross-site" or (
                "__Host-dealnaru_session" in request.cookies and origin is None and fetch_site != "same-origin"
            ))
        ):
            rejected = JSONResponse({"detail": "허용되지 않은 요청 출처입니다."}, 403)
    if rejected is None and PRODUCTION and request.method == "POST":
        action = path.rstrip("/").rsplit("/", 1)[-1]
        limits = AUTH_LIMITS.get(action) if path.startswith("/api/auth/") else None
        if path.startswith("/api/games/") and path.rstrip("/").endswith("/history/sync"):
            action, limits = "history-sync", (24, 60)
        if limits:
            retry = budget.admit((request.client.host if request.client else "unknown", action), *limits)
            if retry:
                rejected = JSONResponse({"detail": "요청이 많습니다. 잠시 뒤 다시 시도해 주세요."}, 429,
                                        headers={"Retry-After": str(retry)})
    response = rejected if rejected is not None else await call_next(request)
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    return response


def https_url(value):
    if not isinstance(value, str) or not value or "\\" in value or any(c.isspace() or ord(c) < 32 for c in value):
        return None
    try:
        parsed = urlsplit(value)
        if parsed.scheme == "https" and parsed.hostname and parsed.username is None:
            return value
    except ValueError:
        pass
    return None
