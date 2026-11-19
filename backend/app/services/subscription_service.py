"""공식 출처를 수집하고 금액·통화·결제 기간·대상을 분리해 저장합니다."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup
from sqlalchemy import func, or_, text

from app.models import Software, SoftwarePromotion
from app.utils.time import to_utc_iso_z, utc_now

CATALOG_PATH = Path(__file__).resolve().parents[1] / "data" / "subscription_catalog.json"
MAX_VERIFICATION_AGE_DAYS = 7


def load_catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def seed_subscription_catalog(db):
    """검증 기준일을 보존하며 초기 한 번만 입력. 사용자 행/게임/계정은 삭제하지 않음."""
    catalog = load_catalog()
    verified_at = datetime.fromisoformat(catalog["verified_at"])
    if db.get_bind().dialect.name == "postgresql":
        db.execute(text("SELECT pg_advisory_xact_lock(5092027)"))
    db.query(SoftwarePromotion).filter(
        SoftwarePromotion.source_key.is_(None),
        SoftwarePromotion.url.in_(["#", ""]),
        or_(SoftwarePromotion.description.contains("화면 테스트"),
            SoftwarePromotion.description.contains("예시")),
    ).update({SoftwarePromotion.is_current: False, SoftwarePromotion.source_status: "demo"}, synchronize_session=False)
    for item in catalog["products"]:
        software = db.query(Software).filter(Software.source_key == item["source_key"]).first()
        if software is None:
            software = db.query(Software).filter(func.lower(Software.name) == item["name"].lower()).first()
        if software is None:
            software = Software()
            db.add(software)
        # 공식 카탈로그 관리 항목의 제품 정보만 갱신합니다.
        for key, value in item.items():
            if key != "promotions":
                setattr(software, key, value)
        software.pricing_type = item.get("pricing_type", "구독형")
        software.rating_label = "공식 출처"
        db.flush()
        for fields in item["promotions"]:
            if db.query(SoftwarePromotion).filter(SoftwarePromotion.source_key == fields["source_key"]).first():
                continue
            values = {key: value for key, value in fields.items() if key != "adapter"}
            db.add(SoftwarePromotion(
                software=software, platform="공식 사이트", checked_at=verified_at,
                **values,
            ))
    from app.services.software_plan_service import seed_software_plans
    seed_software_plans(db)
    db.commit()


def extract_page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for element in soup(["script", "style", "nav", "footer"]):
        element.decompose()
    return " ".join(soup.get_text(" ", strip=True).split())


def fetch_official_page(url):
    # 수집 대상은 카탈로그의 고정 공식 주소입니다. 이용자 입력 URL은 받지 않습니다.
    response = requests.get(url, timeout=25, headers={
        "User-Agent": "Mozilla/5.0 (compatible; GameSoft/0.5)",
        "Accept-Language": "en-US,en;q=0.9" if urlparse(url).hostname == "claude.com" else "ko-KR,ko;q=0.9,en;q=0.8",
    })
    response.raise_for_status()
    response.encoding = "utf-8"
    requested_host = urlparse(url).hostname
    final_host = urlparse(response.url).hostname
    allowed_hosts = {requested_host, "www.jetbrains.com", "www.notion.com"}
    if final_host not in allowed_hosts:
        raise ValueError("공식 페이지 밖으로 이동한 응답은 사용하지 않습니다.")
    page_text = extract_page_text(response.text[:2_000_000])
    if len(page_text) < 80 or re.search(r"access denied|verify you are human|just a moment", page_text, re.I):
        raise ValueError("공식 페이지 본문을 확인하지 못했습니다.")
    return page_text


def parse_official_offer(adapter, page_text):
    """가격이 없는 교육/제휴 정보는 None을 유지. 모호한 숫자는 가격으로 추정하지 않음."""
    text = " ".join(page_text.split())
    lower = text.lower()
    if adapter == "claude":
        # 메뉴에도 Pro라는 단어가 있습니다. 가격 카드의 연간 문구를 기준으로 찾습니다.
        match = re.search(r"annual subscription discount\s*\(\s*\$\s*([\d,]+(?:\.\d+)?)\s+billed\s+up\s*front\)\.?\s*\$\s*([\d,]+(?:\.\d+)?)\s+if billed monthly", text, re.I)
        if not match or not re.search(r"\bPro\b", text[max(0, match.start() - 250):match.start()]):
            raise ValueError("Claude Pro 월간·연간 비교 요금을 확인하지 못했습니다.")
        amount = Decimal(match.group(1).replace(",", ""))
        regular = Decimal(match.group(2).replace(",", "")) * 12
        if not 0 < amount < regular:
            raise ValueError("연간 할인 가격 검증에 실패했습니다.")
        return {"discounted_price": amount, "original_price": regular,
                "discount_rate": round((1 - amount / regular) * 100, 2), "currency": "USD", "is_offer": True}
    if adapter == "chatgpt":
        if "promo" not in lower or not re.search(r"eligib|new.*subscriber|partner", lower):
            raise ValueError("ChatGPT 공식 프로모션 대상 안내를 확인하지 못했습니다.")
        return {"original_price": None, "discounted_price": None, "discount_rate": None, "is_offer": False}
    if adapter.startswith("gemini"):
        if "google ai" not in lower:
            raise ValueError("Google AI 공지 본문을 확인하지 못했습니다.")
        if adapter == "gemini-plus":
            if not re.search(r"(?:플러스|plus).{0,100}(?:1년|12개월).{0,30}무료", text, re.I):
                raise ValueError("Google AI Plus 학생 무료 혜택을 확인하지 못했습니다.")
            expiry = re.search(r"(20\d\d)년\s*(\d{1,2})월\s*(\d{1,2})일.{0,25}신청", text)
            if not expiry:
                raise ValueError("학생 혜택 신청 마감일을 확인하지 못했습니다.")
            end = datetime(*map(int, expiry.groups())).date().isoformat()
            return {"discounted_price": 0, "original_price": None, "discount_rate": None, "end_date": end, "is_offer": True}
        match = re.search(r"Google AI Pro.{0,130}월\s*([\d,]+)원", text, re.I)
        if not match:
            raise ValueError("Google AI Pro 학생 요금을 확인하지 못했습니다.")
        return {"discounted_price": Decimal(match.group(1).replace(",", "")), "original_price": None, "discount_rate": None, "is_offer": True}
    if adapter == "adobe":
        if "creative cloud" not in lower or not re.search(r"학생|student", text, re.I):
            raise ValueError("Adobe 교육 요금 안내를 확인하지 못했습니다.")
        # 일반 세일 카드와 학생 첫해 요금을 혼합하지 않습니다.
        match = re.search(r"학생과 교사에게는(.{0,3500})", text)
        section = match.group(1) if match else ""
        base = re.search(r"정가\s*₩\s*([\d,]+)\s*/\s*월", section)
        first_year = re.search(r"첫해 결제 금액은\s*₩\s*([\d,]+)\s*/\s*월", section)
        renewal = re.search(r"그 이후에는\s*₩\s*([\d,]+)\s*/\s*월", section)
        if base and first_year and renewal:
            regular = Decimal(base.group(1).replace(",", ""))
            amount = Decimal(first_year.group(1).replace(",", ""))
            if not 0 < amount < regular:
                raise ValueError("Adobe 교육 요금 비교에 실패했습니다.")
            return {"original_price": regular, "discounted_price": amount,
                    "discount_rate": round((1 - amount / regular) * 100, 2),
                    "currency": "KRW", "billing_period": "월별 청구 · 연간 약정",
                    "renewal_notice": f"첫해 이후 교육 요금은 {int(renewal.group(1).replace(',', '')):,}원/월입니다. 연간 약정·중도 해지 조건을 확인하세요.",
                    "is_offer": True}
        # 동적으로 표시되어 확인하지 못한 결제 금액은 추정하지 않습니다.
        return {"original_price": None, "discounted_price": None, "discount_rate": None, "is_offer": True}
    if adapter == "education":
        if not re.search(r"student|education|학생|교육", text, re.I) or not re.search(r"free|무료", text, re.I):
            raise ValueError("교육용 무료 혜택 본문을 확인하지 못했습니다.")
        return {"original_price": None, "discounted_price": 0, "discount_rate": None, "is_offer": True}
    raise ValueError("지원하지 않는 공식 출처입니다.")


def refresh_subscription_offers(db, heartbeat=None):
    seed_subscription_catalog(db)
    entries = [fields for item in load_catalog()["products"] for fields in item["promotions"]]
    urls = list(dict.fromkeys(entry["url"] for entry in entries))

    def fetch(url):
        try:
            return url, fetch_official_page(url), None
        except (requests.RequestException, ValueError) as error:
            return url, None, type(error).__name__

    result = {"updated_count": 0, "error_count": 0, "skipped_count": 0, "errors": []}
    # DB 세션은 스레드에 전달하지 않습니다.
    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = dict((url, (body, error)) for url, body, error in pool.map(fetch, urls))
    for entry in entries:
        if heartbeat:
            heartbeat()
        promotion = db.query(SoftwarePromotion).filter(SoftwarePromotion.source_key == entry["source_key"]).one()
        promotion.url = entry["url"]
        page, error = pages[entry["url"]]
        try:
            if error:
                raise ValueError("공식 사이트 접속 실패")
            fields = parse_official_offer(entry["adapter"], page)
            for key, value in fields.items():
                setattr(promotion, key, value)
            promotion.checked_at = utc_now()
            promotion.source_status = "verified"
            promotion.last_error = None
            promotion.is_current = True
            result["updated_count"] += 1
        except ValueError as error:
            # 기존 값은 보존하지만 화면에서 확인된 현재 할인으로 표시하지 않습니다.
            promotion.source_status = "needs_check"
            promotion.last_error = str(error)
            result["error_count"] += 1
            result["errors"].append({"source": entry["source_key"], "error": str(error)})
        db.commit()
    from app.services.software_plan_service import refresh_software_plans
    plan_result = refresh_software_plans(db, heartbeat=heartbeat)
    result["offers_updated"] = result["updated_count"]
    result["plans_updated"] = plan_result["updated_count"]
    result["updated_count"] += plan_result["updated_count"]
    result["error_count"] += plan_result["error_count"]
    result["skipped_count"] += plan_result["skipped_count"]
    result["errors"].extend(plan_result["errors"])
    return result


def promotion_status(promotion, now=None):
    now = now or utc_now()
    if not promotion.is_current:
        return "inactive"
    if promotion.source_status == "demo":
        return "demo"
    for name, status, compare in (("end_date", "expired", lambda d: d < now.date()),
                                  ("start_date", "upcoming", lambda d: d > now.date())):
        value = getattr(promotion, name)
        if value:
            try:
                if compare(datetime.fromisoformat(value.replace(".", "-")).date()):
                    return status
            except ValueError:
                pass
    if promotion.source_key:
        if promotion.source_status != "verified":
            return "needs_check" if promotion.source_status == "needs_check" else "reference"
        if not promotion.checked_at or now - promotion.checked_at > timedelta(days=MAX_VERIFICATION_AGE_DAYS):
            return "stale"
    return "active" if promotion.is_offer else "information"


def format_subscription_promotion(promotion):
    status = promotion_status(promotion)
    known = status in {"active", "information"}
    def amount(value):
        return float(value) if known and value is not None else None
    return {
        "id": promotion.id, "title": promotion.title,
        "platform": promotion.platform, "promo_type": promotion.promo_type,
        "original_price": amount(promotion.original_price), "discounted_price": amount(promotion.discounted_price),
        "discount_rate": amount(promotion.discount_rate), "currency": promotion.currency or "KRW",
        "last_confirmed_price": float(promotion.discounted_price) if promotion.discounted_price is not None and promotion.checked_at else None,
        "price_kind": "conditional" if promotion.is_offer else "information",
        "billing_period": promotion.billing_period, "eligibility": promotion.eligibility,
        "region": promotion.region, "renewal_notice": promotion.renewal_notice,
        "start_date": promotion.start_date, "end_date": promotion.end_date,
        "coupon_code": promotion.coupon_code, "url": promotion.url,
        "description": promotion.description, "checked_at": to_utc_iso_z(promotion.checked_at),
        "status": status, "source_status": promotion.source_status,
        "is_current": status in {"active", "information", "reference", "needs_check", "stale"},
        "is_offer": promotion.is_offer,
    }


def format_subscription_card(software):
    from app.services.software_plan_service import (
        format_software_plan,
        load_plan_catalog,
    )
    plans = [format_software_plan(plan) for plan in software.plans if plan.is_current]
    plan_map = {plan["source_key"]: plan for plan in plans}
    for entry in load_plan_catalog()["plans"]:
        annual = plan_map.get(entry["source_key"])
        monthly = plan_map.get(entry.get("monthly_plan_key"))
        if annual and monthly and annual["current_price"] is not None and monthly["current_price"]:
            if annual["currency"] == monthly["currency"] and annual["unit"] == monthly["unit"]:
                annual["annual_saving_rate"] = round((1 - annual["current_price"] / (monthly["current_price"] * 12)) * 100, 1)
    paid = [plan for plan in plans if (plan["current_price"] or plan["last_confirmed_price"] or 0) > 0]
    paid.sort(key=lambda plan: (plan["status"] != "verified", plan["currency"] != "KRW", plan["period"] != "month", plan["current_price"] or plan["last_confirmed_price"]))
    free = [plan for plan in plans if plan["display_price"] == 0]
    current = [format_subscription_promotion(p) for p in software.promotions
               if promotion_status(p) not in {"inactive", "demo", "expired", "upcoming"}]
    current.sort(key=lambda p: (p["status"] == "active", p["discount_rate"] or 0), reverse=True)
    best = current[0] if current else None
    return {
        "id": software.id, "title": software.name, "name": software.name,
        "category": software.category, "kind": software.kind,
        "developer": software.developer, "description": software.description,
        "image_color": software.image_color, "pricing_type": software.pricing_type,
        "logo_url": software.logo_url,
        "plans": plans, "plan_count": len(plans), "starting_plan": paid[0] if paid else free[0] if free else plans[0] if plans else None,
        "has_free_plan": bool(free),
        "rating_label": software.rating_label, "official_url": software.official_url,
        "promotion": best, "offer_count": sum(p["is_offer"] and p["status"] == "active" for p in current),
        "original_price": best["original_price"] if best else None,
        "discounted_price": best["discounted_price"] if best else None,
        "discount_rate": best["discount_rate"] if best else None,
        "promo_title": best["title"] if best else "현재 확인된 혜택 없음",
    }
