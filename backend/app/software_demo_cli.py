import argparse

from app.database import SessionLocal
from app.models import Software
from app.services.software_demo_service import upsert_demo_software


def seed_demo_software() -> None:
    db = SessionLocal()

    try:
        result = upsert_demo_software(db)
        print("소프트웨어 데모 데이터를 준비했습니다.")
        print(
            "소프트웨어: "
            f"{result['software_created']}개 생성, "
            f"{result['software_updated']}개 갱신"
        )
        print(
            "프로모션: "
            f"{result['promotions_created']}개 생성, "
            f"{result['promotions_updated']}개 갱신"
        )
        print("기존 사용자, 게임, 가격 데이터는 삭제하지 않았습니다.")
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def list_software() -> None:
    db = SessionLocal()

    try:
        software_list = db.query(Software).order_by(Software.id.asc()).all()

        if not software_list:
            print("등록된 소프트웨어가 없습니다.")
            return

        print(f"등록된 소프트웨어: {len(software_list)}개")
        for software in software_list:
            print(
                f"- [{software.id}] {software.name} "
                f"({software.category or '분류 없음'}, 프로모션 {len(software.promotions)}개)"
            )
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="다른 DB 데이터를 보존하면서 소프트웨어 Beta 화면을 테스트합니다."
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("seed", help="데모 소프트웨어 5개를 안전하게 추가/갱신합니다.")
    subparsers.add_parser("list", help="현재 등록된 소프트웨어를 확인합니다.")
    args = parser.parse_args()

    if args.command == "seed":
        seed_demo_software()
    else:
        list_software()


if __name__ == "__main__":
    main()
