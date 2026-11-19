"""환경 설정과 공개 서버의 시작 조건."""
import os
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv()

APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
PRODUCTION = APP_ENV in {"prod", "production"}
FRONTEND_BASE_URL = os.getenv("FRONTEND_BASE_URL", "http://localhost:5173").rstrip("/")
ALLOWED_ORIGINS = tuple(
    origin.strip().rstrip("/")
    for origin in os.getenv("ALLOWED_ORIGINS", FRONTEND_BASE_URL).split(",")
    if origin.strip()
)
EMAIL_DEV_MODE = os.getenv("EMAIL_DEV_MODE", "true").strip().lower() == "true"
EMAIL_VERIFICATION_REQUIRED = (
    os.getenv("EMAIL_VERIFICATION_REQUIRED", "true").strip().lower() == "true"
)
TERMS_VERSION = "1.1"
USED_MARKET_ENABLED = os.getenv("USED_MARKET_ENABLED", "false" if PRODUCTION else "true").strip().lower() == "true"


def validate_production_settings():
    if not PRODUCTION:
        return
    url = urlsplit(FRONTEND_BASE_URL)
    if (
        url.scheme != "https" or not url.hostname or url.username is not None
        or url.path or url.query or url.fragment or url.hostname == "localhost"
        or ALLOWED_ORIGINS != (FRONTEND_BASE_URL,)
    ):
        raise RuntimeError("운영 사이트 주소와 허용 오리진에 같은 HTTPS 주소를 설정하세요.")
    if EMAIL_DEV_MODE or not EMAIL_VERIFICATION_REQUIRED:
        raise RuntimeError("운영에서는 이메일 인증을 켜고 개발 메일 모드를 끄세요.")
    if not os.getenv("RESEND_API_KEY", "").strip() or not os.getenv("MAIL_FROM", "").strip():
        raise RuntimeError("운영용 RESEND_API_KEY와 인증된 MAIL_FROM을 설정하세요.")
