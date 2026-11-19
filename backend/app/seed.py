import argparse
import os
from datetime import datetime

from app.database import Base, SessionLocal, engine
from app.models import (
    Game,
    GameTag,
    Platform,
    PredictionResult,
    PriceSnapshot,
    Software,
    SoftwarePromotion,
    UsedMarketSummary,
    User,
)
from app.services.auth_service import hash_password
from app.utils.time import utc_now


def calculate_discount_rate(original_price: int, discounted_price: int) -> float:
    if original_price <= 0:
        return 0

    return round((1 - discounted_price / original_price) * 100, 2)


def create_price(
    game,
    platform,
    original_price,
    discounted_price,
    collected_at,
    is_current=False,
    url="#",
):
    return PriceSnapshot(
        game=game,
        platform=platform,
        currency="KRW",
        original_price=original_price,
        discounted_price=discounted_price,
        discount_rate=calculate_discount_rate(original_price, discounted_price),
        collected_at=collected_at,
        is_current=is_current,
        url=url,
    )


def main(*, reset: bool = False, yes: bool = False):
    if reset and os.getenv("APP_ENV", "").strip().lower() in {"prod", "production"}:
        raise SystemExit("운영 DB에서는 샘플 데이터 초기화를 실행할 수 없습니다.")
    if not reset:
        print("안전상 데이터베이스 초기화를 실행하지 않았습니다.")
        print("샘플 데이터를 다시 만들려면: python -m app.seed --reset")
        return

    if not yes:
        confirmation = input(
            "모든 테이블과 데이터를 삭제합니다. 계속하려면 RESET을 입력하세요: "
        ).strip()

        if confirmation != "RESET":
            print("취소했습니다.")
            return

    print("DB 테이블과 기존 데이터를 초기화합니다...")

    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        steam = Platform(name="Steam", website="https://store.steampowered.com")
        epic = Platform(name="Epic", website="https://store.epicgames.com")
        ps_store = Platform(name="PS Store", website="https://store.playstation.com")
        gog = Platform(name="GOG", website="https://www.gog.com")

        db.add_all([steam, epic, ps_store, gog])
        db.flush()

        games_data = [
            {
                "title": "Elden Ring",
                "genre": "RPG",
                "developer": "FromSoftware Inc.",
                "publisher": "Bandai Namco Entertainment",
                "release_date": "2022.02.25",
                "rating": "매우 긍정적",
                "review_count": 513728,
                "korean_support": "자막 / 음성",
                "description": "광활한 오픈월드와 높은 난이도의 전투를 특징으로 하는 액션 RPG입니다.",
                "image_color": "#4d3f32",
                "tags": ["RPG", "오픈월드", "다크 판타지", "싱글 플레이"],
                "original_price": 51000,
                "current_prices": [
                    ("Steam", 51000, 43350),
                    ("PS Store", 52000, 44350),
                    ("Epic", 51000, 45900),
                ],
                "history": [
                    51000,
                    51000,
                    45900,
                    51000,
                    43350,
                    40800,
                    51000,
                    43350,
                    51000,
                    45900,
                    40800,
                    43350,
                ],
                "prediction": {
                    "expected_date": "2026년 6월 말",
                    "expected_event": "Steam Summer Sale 예상",
                    "expected_discount_rate": "20% ~ 25%",
                    "confidence": "중간",
                    "reason": "최근 할인 간격과 여름 세일 시즌 패턴을 기준으로 계산했습니다.",
                },
                "used_market": {
                    "digital_lowest": 43350,
                    "digital_discount_rate": 15,
                    "joonggonara_average": 34000,
                    "notice": "중고 시세는 현재 UI 예시이며 실제 수집 기능은 추후 구현 예정입니다.",
                },
            },
            {
                "title": "Baldur's Gate 3",
                "genre": "RPG",
                "developer": "Larian Studios",
                "publisher": "Larian Studios",
                "release_date": "2023.08.03",
                "rating": "압도적으로 긍정적",
                "review_count": 620000,
                "korean_support": "자막",
                "description": "선택과 전략이 중요한 턴제 기반 판타지 RPG입니다.",
                "image_color": "#352b52",
                "tags": ["RPG", "전략", "턴제", "스토리"],
                "original_price": 52000,
                "current_prices": [
                    ("Steam", 52000, 44200),
                    ("GOG", 52000, 46800),
                ],
                "history": [
                    52000,
                    52000,
                    49400,
                    52000,
                    46800,
                    44200,
                    52000,
                    46800,
                    52000,
                    49400,
                    44200,
                    44200,
                ],
                "prediction": {
                    "expected_date": "2026년 7월 초",
                    "expected_event": "계절 할인 예상",
                    "expected_discount_rate": "15% ~ 20%",
                    "confidence": "중간",
                    "reason": "최근 할인율과 여름 시즌 할인 흐름을 기준으로 계산했습니다.",
                },
                "used_market": {
                    "digital_lowest": 44200,
                    "digital_discount_rate": 15,
                    "joonggonara_average": 40000,
                    "notice": "중고 시세는 현재 UI 예시이며 실제 수집 기능은 추후 구현 예정입니다.",
                },
            },
            {
                "title": "Cyberpunk 2077",
                "genre": "액션",
                "developer": "CD PROJEKT RED",
                "publisher": "CD PROJEKT RED",
                "release_date": "2020.12.10",
                "rating": "매우 긍정적",
                "review_count": 720000,
                "korean_support": "자막 / 음성",
                "description": "미래 도시 나이트시티를 배경으로 하는 오픈월드 액션 RPG입니다.",
                "image_color": "#5b4b1f",
                "tags": ["액션", "오픈월드", "SF", "싱글 플레이"],
                "original_price": 65000,
                "current_prices": [
                    ("Steam", 65000, 55250),
                    ("Epic", 65000, 52000),
                ],
                "history": [
                    65000,
                    52000,
                    65000,
                    48750,
                    65000,
                    39000,
                    65000,
                    55250,
                    65000,
                    52000,
                    39000,
                    55250,
                ],
                "prediction": {
                    "expected_date": "2026년 6월 말",
                    "expected_event": "대형 시즌 세일 예상",
                    "expected_discount_rate": "40% ~ 50%",
                    "confidence": "높음",
                    "reason": "과거 대형 시즌 세일에서 반복적으로 높은 할인율을 보인 패턴이 있습니다.",
                },
                "used_market": {
                    "digital_lowest": 55250,
                    "digital_discount_rate": 15,
                    "joonggonara_average": 30000,
                    "notice": "중고 시세는 현재 UI 예시이며 실제 수집 기능은 추후 구현 예정입니다.",
                },
            },
            {
                "title": "The Binding of Isaac",
                "genre": "인디",
                "developer": "Edmund McMillen",
                "publisher": "Nicalis",
                "release_date": "2014.11.04",
                "rating": "압도적으로 긍정적",
                "review_count": 250000,
                "korean_support": "일부 지원",
                "description": "반복 플레이와 아이템 조합이 핵심인 로그라이크 액션 게임입니다.",
                "image_color": "#432b2b",
                "tags": ["인디", "로그라이크", "액션"],
                "original_price": 21000,
                "current_prices": [
                    ("Steam", 21000, 16800),
                ],
                "history": [
                    21000,
                    16800,
                    21000,
                    14700,
                    21000,
                    12600,
                    21000,
                    16800,
                    21000,
                    14700,
                    12600,
                    16800,
                ],
                "prediction": {
                    "expected_date": "2026년 6월 중순",
                    "expected_event": "인디 게임 할인 이벤트 예상",
                    "expected_discount_rate": "30% ~ 40%",
                    "confidence": "높음",
                    "reason": "할인 빈도가 높고 반복 패턴이 비교적 뚜렷합니다.",
                },
                "used_market": {
                    "digital_lowest": 16800,
                    "digital_discount_rate": 20,
                    "joonggonara_average": 0,
                    "notice": "디지털 전용 상품은 중고 시세 비교가 제한적입니다.",
                },
            },
        ]

        platform_map = {
            "Steam": steam,
            "Epic": epic,
            "PS Store": ps_store,
            "GOG": gog,
        }

        for game_data in games_data:
            game = Game(
                title=game_data["title"],
                genre=game_data["genre"],
                developer=game_data["developer"],
                publisher=game_data["publisher"],
                release_date=game_data["release_date"],
                rating=game_data["rating"],
                review_count=game_data["review_count"],
                korean_support=game_data["korean_support"],
                description=game_data["description"],
                image_color=game_data["image_color"],
            )

            db.add(game)
            db.flush()

            for tag_name in game_data["tags"]:
                db.add(GameTag(game=game, name=tag_name))

            for platform_name, original_price, discounted_price in game_data[
                "current_prices"
            ]:
                db.add(
                    create_price(
                        game=game,
                        platform=platform_map[platform_name],
                        original_price=original_price,
                        discounted_price=discounted_price,
                        collected_at=datetime(2026, 5, 23),
                        is_current=True,
                    )
                )

            for month_index, price in enumerate(game_data["history"], start=1):
                db.add(
                    create_price(
                        game=game,
                        platform=steam,
                        original_price=game_data["original_price"],
                        discounted_price=price,
                        collected_at=datetime(2025, month_index, 1),
                        is_current=False,
                    )
                )

            db.add(
                PredictionResult(
                    game=game,
                    expected_date=game_data["prediction"]["expected_date"],
                    expected_event=game_data["prediction"]["expected_event"],
                    expected_discount_rate=game_data["prediction"][
                        "expected_discount_rate"
                    ],
                    confidence=game_data["prediction"]["confidence"],
                    reason=game_data["prediction"]["reason"],
                )
            )

            db.add(
                UsedMarketSummary(
                    game=game,
                    digital_lowest=game_data["used_market"]["digital_lowest"],
                    digital_discount_rate=game_data["used_market"][
                        "digital_discount_rate"
                    ],
                    joonggonara_average=game_data["used_market"]["joonggonara_average"],
                    notice=game_data["used_market"]["notice"],
                )
            )
        software_data = [
            {
                "name": "Adobe Creative Cloud",
                "category": "디자인",
                "developer": "Adobe",
                "description": "이미지, 영상, 디자인 작업에 자주 사용되는 크리에이티브 소프트웨어 묶음입니다.",
                "image_color": "#7c3aed",
                "official_url": "#",
                "pricing_type": "구독형",
                "rating_label": "디자인 필수 소프트웨어",
                "promotions": [
                    {
                        "title": "학생 할인 프로모션",
                        "platform": "공식 사이트",
                        "promo_type": "학생 할인",
                        "original_price": 24000,
                        "discounted_price": 12000,
                        "discount_rate": 50,
                        "start_date": "2026.05.01",
                        "end_date": "2026.06.30",
                        "coupon_code": None,
                        "description": "학생 인증 시 월 구독료 할인 혜택을 제공하는 예시 프로모션입니다.",
                    }
                ],
            },
            {
                "name": "Figma Professional",
                "category": "디자인",
                "developer": "Figma",
                "description": "UI/UX 디자인과 협업에 사용되는 웹 기반 디자인 도구입니다.",
                "image_color": "#2563eb",
                "official_url": "#",
                "pricing_type": "구독형",
                "rating_label": "협업 디자인 도구",
                "promotions": [
                    {
                        "title": "팀 플랜 할인",
                        "platform": "공식 사이트",
                        "promo_type": "팀 프로모션",
                        "original_price": 18000,
                        "discounted_price": 12000,
                        "discount_rate": 33,
                        "start_date": "2026.05.10",
                        "end_date": "2026.07.10",
                        "coupon_code": "TEAM2026",
                        "description": "팀 단위 이용자를 위한 월 구독료 할인 예시입니다.",
                    }
                ],
            },
            {
                "name": "JetBrains Toolbox",
                "category": "개발",
                "developer": "JetBrains",
                "description": "IntelliJ, PyCharm 등 개발 IDE 제품군을 포함한 개발자용 도구 묶음입니다.",
                "image_color": "#111827",
                "official_url": "#",
                "pricing_type": "구독형",
                "rating_label": "개발자 인기 도구",
                "promotions": [
                    {
                        "title": "개발자 구독 할인",
                        "platform": "공식 사이트",
                        "promo_type": "기간 한정",
                        "original_price": 35000,
                        "discounted_price": 24500,
                        "discount_rate": 30,
                        "start_date": "2026.05.01",
                        "end_date": "2026.06.15",
                        "coupon_code": None,
                        "description": "개발자용 IDE 묶음 구독 할인 예시입니다.",
                    }
                ],
            },
            {
                "name": "Notion Plus",
                "category": "생산성",
                "developer": "Notion",
                "description": "문서, 프로젝트 관리, 지식베이스 구축에 활용되는 생산성 도구입니다.",
                "image_color": "#374151",
                "official_url": "#",
                "pricing_type": "구독형",
                "rating_label": "문서/협업 도구",
                "promotions": [
                    {
                        "title": "개인 생산성 플랜 할인",
                        "platform": "공식 사이트",
                        "promo_type": "신규 이용자",
                        "original_price": 14000,
                        "discounted_price": 7000,
                        "discount_rate": 50,
                        "start_date": "2026.05.20",
                        "end_date": "2026.06.20",
                        "coupon_code": "START50",
                        "description": "신규 이용자 대상 월 구독료 할인 예시입니다.",
                    }
                ],
            },
            {
                "name": "Visual Studio Code",
                "category": "개발",
                "developer": "Microsoft",
                "description": "확장 기능 기반으로 다양한 언어 개발에 사용할 수 있는 코드 에디터입니다.",
                "image_color": "#0f766e",
                "official_url": "#",
                "pricing_type": "무료",
                "rating_label": "개발 필수 도구",
                "promotions": [
                    {
                        "title": "무료 사용 가능",
                        "platform": "공식 사이트",
                        "promo_type": "무료",
                        "original_price": 0,
                        "discounted_price": 0,
                        "discount_rate": 0,
                        "start_date": "상시",
                        "end_date": "상시",
                        "coupon_code": None,
                        "description": "무료로 사용할 수 있는 개발 도구 예시입니다.",
                    }
                ],
            },
        ]

        for software_item in software_data:
            software = Software(
                name=software_item["name"],
                category=software_item["category"],
                developer=software_item["developer"],
                description=software_item["description"],
                image_color=software_item["image_color"],
                official_url=software_item["official_url"],
                pricing_type=software_item["pricing_type"],
                rating_label=software_item["rating_label"],
            )

            db.add(software)
            db.flush()

            for promotion_item in software_item["promotions"]:
                db.add(
                    SoftwarePromotion(
                        software=software,
                        title=promotion_item["title"],
                        platform=promotion_item["platform"],
                        promo_type=promotion_item["promo_type"],
                        original_price=promotion_item["original_price"],
                        discounted_price=promotion_item["discounted_price"],
                        discount_rate=promotion_item["discount_rate"],
                        start_date=promotion_item["start_date"],
                        end_date=promotion_item["end_date"],
                        coupon_code=promotion_item["coupon_code"],
                        description=promotion_item["description"],
                        url="#",
                        is_current=True,
                    )
                )

        admin_email = os.getenv("ADMIN_EMAIL", "").strip().lower()
        admin_password = os.getenv("ADMIN_PASSWORD", "")
        admin_nickname = os.getenv("ADMIN_NICKNAME", "관리자").strip() or "관리자"

        if admin_email and admin_password:
            if len(admin_password) < 8:
                raise ValueError("ADMIN_PASSWORD는 8자 이상이어야 합니다.")

            db.add(
                User(
                    email=admin_email,
                    nickname=admin_nickname,
                    password_hash=hash_password(admin_password),
                    is_admin=True,
                    email_verified=True,
                    email_verified_at=utc_now(),
                )
            )
        else:
            print(
                "관리자 계정은 생성하지 않았습니다. "
                "ADMIN_EMAIL과 ADMIN_PASSWORD를 설정하거나 "
                "python -m app.admin_cli create를 실행하세요."
            )

        db.commit()
        print("샘플 데이터 입력 완료입니다!")

    except Exception as error:
        db.rollback()
        print("샘플 데이터 입력 실패입니다.")
        print(error)

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="DealNaru 데모 데이터를 새로 생성합니다. 기존 데이터는 삭제됩니다."
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="기존 테이블과 데이터를 삭제한 뒤 샘플 데이터를 생성합니다.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="RESET 확인 입력을 생략합니다. 자동화 환경에서만 사용하세요.",
    )
    args = parser.parse_args()
    main(reset=args.reset, yes=args.yes)
