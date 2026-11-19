"""Put every regression run on an isolated disposable database."""
import os
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4

db_url = os.getenv("DEALNARU_TEST_POSTGRES_URL")
TEST_DB_PATH = None
if db_url:
    parsed = urlsplit(db_url)
    if (
        parsed.scheme != "postgresql+psycopg2"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or not parsed.path.endswith("_test")
        or parsed.query or parsed.fragment
    ):
        raise RuntimeError("테스트 대상은 로컬의 별도 _test PostgreSQL DB여야 합니다.")
else:
    TEST_DB_PATH = Path(__file__).with_name(f".dealnaru-{uuid4().hex}-test.db")
    db_url = f"sqlite:///{TEST_DB_PATH.as_posix()}"

os.environ.update(DATABASE_URL=db_url, APP_ENV="test", EMAIL_DEV_MODE="true",
                  EMAIL_VERIFICATION_REQUIRED="true", CURRENT_PRICE_MAX_AGE_HOURS="168",
                  ITAD_API_KEY="", FRONTEND_BASE_URL="http://localhost:5173",
                  ALLOWED_ORIGINS="http://localhost:5173")
