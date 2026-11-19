"""python -m app.software_price_cli status | refresh (기존 DB 초기화 없음)."""
import argparse
import json

from app.database import SessionLocal
from app.models import SoftwarePlan
from app.services.software_plan_service import format_software_plan
from app.services.subscription_service import refresh_subscription_offers, seed_subscription_catalog


def price_status(db):
    rows = [format_software_plan(row) for row in db.query(SoftwarePlan).filter(SoftwarePlan.is_current.is_(True)).all()]
    counts = {key: sum(row["price_basis"] == key for row in rows) for key in ("current", "last_confirmed", "unavailable")}
    return {"plan_count": len(rows), "counts": counts,
            "needs_attention": [{"source_key": row["source_key"], "status": row["status"],
                                 "currency": row["currency"], "checked_at": row["checked_at"], "url": row["url"]}
                                for row in rows if row["price_basis"] != "current"]}


def main():
    parser = argparse.ArgumentParser(description="정식 요금제 가격 수집·확인 현황. 사용자·게임 데이터는 초기화하지 않습니다.")
    parser.add_argument("action", choices=("status", "refresh"))
    args = parser.parse_args()
    with SessionLocal() as db:
        seed_subscription_catalog(db)
        result = {"refresh": refresh_subscription_offers(db)} if args.action == "refresh" else {}
        result["status"] = price_status(db)
        print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
