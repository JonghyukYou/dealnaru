"""python -m app.catalog_cli cleanup [--apply]"""

import argparse
import json

from app.database import Base, SessionLocal, engine
from app.services.catalog_filter_service import cleanup_invalid_catalog
from app.services.schema_service import upgrade_subscription_schema


def main():
    parser = argparse.ArgumentParser(description="번들·비게임 선별 정리 (기본: 대상 확인만)")
    parser.add_argument("action", choices=["cleanup"])
    parser.add_argument("--apply", action="store_true", help="일치하는 비게임과 연결된 가격/찜/알림 삭제")
    args = parser.parse_args()
    upgrade_subscription_schema(engine, metadata=Base.metadata)
    with SessionLocal() as db:
        result = cleanup_invalid_catalog(db, apply=args.apply)
        if args.apply:
            db.commit()
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
