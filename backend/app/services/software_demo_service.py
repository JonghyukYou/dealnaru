from datetime import date, timedelta

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models import Software, SoftwarePromotion
from app.utils.time import utc_now


def get_demo_software_data(reference_date: date | None = None) -> list[dict]:
    """Return clearly marked, date-adjusted software data for local demos."""
    today = reference_date or utc_now().date()
    start_date = (today - timedelta(days=7)).strftime("%Y.%m.%d")
    end_date = (today + timedelta(days=30)).strftime("%Y.%m.%d")

    return [
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
                    "start_date": start_date,
                    "end_date": end_date,
                    "coupon_code": None,
                    "description": "화면 테스트를 위한 학생 할인 예시 데이터입니다.",
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
                    "start_date": start_date,
                    "end_date": end_date,
                    "coupon_code": "TEAM-DEMO",
                    "description": "화면 테스트를 위한 팀 플랜 할인 예시 데이터입니다.",
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
                    "start_date": start_date,
                    "end_date": end_date,
                    "coupon_code": None,
                    "description": "화면 테스트를 위한 개발 도구 할인 예시 데이터입니다.",
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
                    "start_date": start_date,
                    "end_date": end_date,
                    "coupon_code": "START-DEMO",
                    "description": "화면 테스트를 위한 신규 이용자 할인 예시 데이터입니다.",
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
                    "description": "화면 테스트를 위한 무료 소프트웨어 예시 데이터입니다.",
                }
            ],
        },
    ]


def upsert_demo_software(db: Session) -> dict[str, int]:
    """Add demo software without deleting or duplicating existing data."""
    result = {
        "software_created": 0,
        "software_updated": 0,
        "promotions_created": 0,
        "promotions_updated": 0,
    }

    for software_data in get_demo_software_data():
        software = (
            db.query(Software)
            .filter(func.lower(Software.name) == software_data["name"].lower())
            .order_by(Software.id.asc())
            .first()
        )
        if software is not None and software.source_key:
            # 공식 카탈로그 입력 후 과거 데모 명령이 실행돼도 실정보를 덮어쓰지 않음.
            continue

        software_fields = {
            key: value
            for key, value in software_data.items()
            if key != "promotions"
        }

        if software is None:
            software = Software(**software_fields)
            db.add(software)
            result["software_created"] += 1
        else:
            for key, value in software_fields.items():
                setattr(software, key, value)
            result["software_updated"] += 1

        db.flush()

        for promotion_data in software_data["promotions"]:
            promotion = (
                db.query(SoftwarePromotion)
                .filter(
                    SoftwarePromotion.software_id == software.id,
                    SoftwarePromotion.title == promotion_data["title"],
                )
                .order_by(SoftwarePromotion.id.asc())
                .first()
            )
            promotion_fields = {
                **promotion_data,
                "url": "#",
                "is_current": True,
            }

            if promotion is None:
                promotion = SoftwarePromotion(
                    software=software,
                    **promotion_fields,
                )
                db.add(promotion)
                result["promotions_created"] += 1
            else:
                for key, value in promotion_fields.items():
                    setattr(promotion, key, value)
                result["promotions_updated"] += 1

    db.commit()
    return result
