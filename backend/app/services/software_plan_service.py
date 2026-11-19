"""할인 이벤트와 별도로 정식 요금제를 저장·확인합니다."""

import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from decimal import Decimal, DecimalException
from pathlib import Path
from urllib.parse import urlparse

import requests
from bs4 import BeautifulSoup

from app.models import Software, SoftwarePlan
from app.services.subscription_service import extract_page_text
from app.utils.time import to_utc_iso_z, utc_now

CATALOG_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "software_plan_catalog.json"
)
CONFIG_FIELDS = {
    "product",
    "adapter",
    "pattern",
    "multiplier",
    "monthly_plan_key",
    "pricing_product",
    "regular_pattern",
    "current_pattern",
    "patterns", "verified_at", "table_column", "table_row", "selector",
    "source_note", "min_price", "max_price", "pricing_type",
    "price_variant",
    "pricing_section",
}


def load_plan_catalog():
    return json.loads(CATALOG_PATH.read_text(encoding="utf-8"))


def seed_software_plans(db):
    catalog = load_plan_catalog()
    db.query(SoftwarePlan).filter(SoftwarePlan.source_key.in_(catalog.get("retired_plan_keys", []))).update(
        {SoftwarePlan.is_current: False}, synchronize_session=False)
    products = {
        row.source_key: row
        for row in db.query(Software).filter(Software.source_key.isnot(None)).all()
    }
    for entry in catalog["plans"]:
        product = products.get(entry["product"])
        if product is None:
            continue
        existing = (
            db.query(SoftwarePlan)
            .filter(SoftwarePlan.source_key == entry["source_key"])
            .first()
        )
        values = {
            key: value for key, value in entry.items() if key not in CONFIG_FIELDS
        }
        entry_timestamp = datetime.fromisoformat(entry.get("verified_at", catalog["verified_at"]))
        if existing:
            # 공식 카탈로그의 주소·설명은 갱신하고, 새 검증 근거가 있을 때만 가격을 갱신합니다.
            for key, value in values.items():
                if key not in {"regular_price", "current_price", "source_status"}:
                    setattr(existing, key, value)
            existing.is_current = True
            if entry["current_price"] is not None and (not existing.checked_at or existing.checked_at < entry_timestamp):
                existing.regular_price = entry["regular_price"]
                existing.current_price = entry["current_price"]
                existing.checked_at = entry_timestamp
                existing.source_status = "verified"
                existing.last_error = None
            continue
        db.add(
            SoftwarePlan(
                software_id=product.id,
                checked_at=entry_timestamp if entry["current_price"] is not None else None,
                **values,
            )
        )
    db.flush()


def fetch_plan_page(url):
    response = requests.get(
        url,
        timeout=20,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; GameSoft/0.5)",
            "Accept-Language": "ko-KR,ko;q=0.9"
            if "/ko-kr/" in url or "google.com" in url
            else "en-US,en;q=0.9",
        },
    )
    response.raise_for_status()
    if urlparse(response.url).hostname != urlparse(url).hostname:
        raise ValueError("공식 사이트 외부로 이동했습니다.")
    response.encoding = "utf-8"
    html = response.text[:3_000_000]
    if urlparse(url).hostname == "one.google.com" and "g1-localized-price" in html:
        from app.services.google_pricing_service import hydrate_google_prices
        html = hydrate_google_prices(html)
    body = extract_page_text(html)
    embedded_prices = False
    if urlparse(url).hostname == "www.jetbrains.com":
        from app.services.jetbrains_pricing_service import jetbrains_price_documents
        embedded_prices = bool(jetbrains_price_documents(html))
    if (len(body) < 100 and not embedded_prices) or re.search(
        r"access denied|site unavailable|unsupported client|verify you are human|just a moment",
        body,
        re.I,
    ):
        raise ValueError("공식 요금제 본문을 확인하지 못했습니다.")
    return html, body


def parse_plan_price(entry, html, body):
    try:
        amount = _parse_plan_price(entry, html, body)
    except (DecimalException, KeyError, TypeError) as error:
        raise ValueError("공식 요금 데이터의 형식을 확인하지 못했습니다.") from error
    if not amount.is_finite() or amount < 0 or (amount == 0 and entry["adapter"] != "free"):
        raise ValueError("유효하지 않은 가격")
    if not Decimal(str(entry.get("min_price", 0))) <= amount <= Decimal(str(entry.get("max_price", 100000000))):
        raise ValueError("요금제 가격 허용 범위를 벗어났습니다.")
    return amount.quantize(Decimal("0.01"))


def structured_price(entry, html):
    """JSON-LD Offer의 요금제 이름·통화·청구 기간이 모두 일치할 때만 사용."""
    prices = set()
    def walk(value, context=""):
        if isinstance(value, list):
            for child in value:
                walk(child, context)
        elif isinstance(value, dict):
            name = str(value.get("name") or context)
            if "price" in value and str(value.get("priceCurrency", "")) == entry["currency"]:
                unit = value.get("billingDuration") or value.get("billingIncrement")
                unit = str(unit or "").upper()
                period = {"month": {"P1M", "MONTH"}, "year": {"P1Y", "P12M", "YEAR"}}.get(entry["period"], set())
                if name.casefold().strip() == entry["name"].casefold().strip() and unit in period:
                    try:
                        prices.add(Decimal(str(value["price"])))
                    except Exception:
                        pass
            for child in value.values():
                if isinstance(child, (list, dict)):
                    walk(child, name)
    for script in BeautifulSoup(html, "html.parser").select('script[type="application/ld+json"]'):
        try:
            walk(json.loads(script.string or script.get_text()))
        except (ValueError, TypeError):
            continue
    if len(prices) == 1:
        return prices.pop()
    if len(prices) > 1:
        raise ValueError("서로 다른 공식 요금이 함께 표시되어 확정하지 않습니다.")
    return None


def _parse_plan_price(entry, html, body):
    adapter = entry["adapter"]
    if adapter == "jetbrains":
        from app.services.jetbrains_pricing_service import parse_jetbrains_plan_price
        return parse_jetbrains_plan_price(entry, html)
    if adapter == "reference":
        raise ValueError("공개 페이지에 확정 금액이 없어 공식 결제 화면 확인 필요")
    if adapter == "table":
        soup = BeautifulSoup(html, "html.parser")
        for table in soup.select("table"):
            rows = [[" ".join(cell.get_text(" ", strip=True).split()) for cell in row.select("th,td")]
                    for row in table.select("tr")]
            header = next((row for row in rows if any(entry["table_column"].casefold() == c.casefold() for c in row)), None)
            if not header:
                continue
            column = next(i for i, c in enumerate(header) if c.casefold() == entry["table_column"].casefold())
            for row in rows:
                if row and row[0].casefold() == entry["table_row"].casefold() and len(row) > column:
                    match = re.search(r"\$\s*([\d,]+(?:\.\d+)?)", row[column])
                    if match:
                        return Decimal(match.group(1).replace(",", ""))
        raise ValueError("공식 비교표의 요금제·결제 기간 열을 확인하지 못했습니다.")
    if adapter == "notion":
        # 공식 페이지 JSON의 요금제·통화·청구 기간을 같이 확인합니다.
        sources = [html]
        # Next.js가 JSON을 문자열로 이스케이프한 경우 JSON 문자열만 디코딩합니다.
        # JS 실행/eval이나 문맥 없는 전역 역슬래시 제거는 하지 않습니다.
        for script in BeautifulSoup(html, "html.parser").select("script"):
            raw = script.string or script.get_text()
            if "external_id" not in raw:
                continue
            for match in re.finditer(r'"(?:[^"\\]|\\.)*"', raw):
                try:
                    decoded = json.loads(match.group())
                    if isinstance(decoded, str) and '"external_id"' in decoded:
                        sources.append(decoded)
                except ValueError:
                    continue
        amounts = set()
        for source in sources:
            for match in re.finditer(r'\{\s*"external_id"\s*:', source):
                try:
                    item, _ = json.JSONDecoder().raw_decode(source[match.start():])
                except (ValueError, TypeError):
                    continue
                if (item.get("product") == entry["pricing_product"]
                    and item.get("currency") == entry["currency"]
                    and item.get("billing_interval") == entry["period"]
                    and item.get("state") == "current"):
                    divisor = 1 if entry["currency"] == "KRW" else 100
                    amounts.add(Decimal(str(item["unit_amount"])) / divisor)
        if len(amounts) == 1:
            return amounts.pop()
        if len(amounts) > 1:
            raise ValueError("Notion의 동일 요금제에 여러 가격 후보가 있습니다.")
        raise ValueError(
            "Notion 청구 기간·통화가 일치하는 요금제 데이터를 확인하지 못했습니다."
        )
    if adapter == "google":
        from app.services.google_pricing_service import parse_google_plan_price
        return parse_google_plan_price(entry, html, body)
    structured = structured_price(entry, html) if html and adapter != "free" else None
    if structured is not None:
        return structured
    if entry.get("selector"):
        selected = BeautifulSoup(html, "html.parser").select(entry["selector"])
        body = " ".join(node.get_text(" ", strip=True) for node in selected)
    matches = [re.search(pattern, body, re.I) for pattern in entry.get("patterns", []) + [entry.get("pattern") or r"(?!)"]]
    amounts = {Decimal(match.group(1).replace(",", "")) for match in matches if match and adapter != "free"}
    if len(amounts) > 1:
        raise ValueError("요금제의 가격 후보가 서로 달라 재확인이 필요합니다.")
    match = next((match for match in matches if match), None)
    if not match:
        raise ValueError(
            "요금제 이름·통화·결제 기간에 맞는 가격을 확인하지 못했습니다."
        )
    if adapter == "free":
        return Decimal(0)
    amount = Decimal(match.group(1).replace(",", "")) * Decimal(
        str(entry.get("multiplier", 1))
    )
    if not amount.is_finite() or amount < 0:
        raise ValueError("유효하지 않은 가격")
    return amount.quantize(Decimal("0.01"))


def refresh_software_plans(db, heartbeat=None):
    entries = load_plan_catalog()["plans"]
    urls = list(dict.fromkeys(entry["url"] for entry in entries if entry["adapter"] != "reference"))

    def fetch(url):
        try:
            return url, fetch_plan_page(url), None
        except (requests.RequestException, ValueError):
            return url, None, "공식 페이지 접속·본문 확인 실패"

    with ThreadPoolExecutor(max_workers=4) as pool:
        pages = {url: (page, error) for url, page, error in pool.map(fetch, urls)}
    result = {"updated_count": 0, "error_count": 0, "skipped_count": 0, "errors": []}
    for entry in entries:
        if heartbeat:
            heartbeat()
        plan = (
            db.query(SoftwarePlan)
            .filter(SoftwarePlan.source_key == entry["source_key"])
            .first()
        )
        if not plan:
            continue
        plan.url = entry["url"]
        if entry["adapter"] == "reference":
            result["skipped_count"] += 1
            continue
        page, error = pages[entry["url"]]
        try:
            if error:
                raise ValueError(error)
            amount = parse_plan_price(entry, *page)
            regular = re.search(entry.get("regular_pattern", r"(?!)"), page[1], re.I)
            regular_amount = Decimal(regular.group(1).replace(",", "")) if regular else amount
            if regular_amount < amount:
                raise ValueError("정가가 현재가보다 낮아 재확인이 필요합니다.")
            plan.regular_price = regular_amount
            plan.current_price = amount
            plan.checked_at = utc_now()
            plan.source_status = "verified"
            plan.last_error = None
            result["updated_count"] += 1
        except ValueError as error:
            # 마지막 확인값은 날짜와 함께 보존. 검증 실패를 현재 가격으로 승격하지 않음.
            plan.source_status = "needs_check"
            plan.last_error = str(error)
            result["error_count"] += 1
            result["errors"].append(
                {"source": entry["source_key"], "error": str(error)}
            )
        db.commit()
    return result


def format_software_plan(plan, now=None):
    now = now or utc_now()
    status = (
        "verified"
        if plan.source_status == "verified"
        and plan.checked_at
        and now - plan.checked_at <= timedelta(days=7)
        else ("stale" if plan.source_status == "verified" else plan.source_status)
    )
    known = status == "verified"
    last_price = float(plan.current_price) if plan.current_price is not None else None
    price = last_price if known else None
    return {
        "id": plan.id,
        "source_key": plan.source_key,
        "name": plan.name,
        "regular_price": float(plan.regular_price)
        if known and plan.regular_price is not None
        else None,
        "current_price": price,
        "last_confirmed_price": last_price,
        "display_price": last_price,
        "price_basis": "current" if known else "last_confirmed" if last_price is not None else "unavailable",
        "currency": plan.currency,
        "period": plan.period,
        "billing_period": plan.billing_period,
        "monthly_equivalent": round(price / 12, 2)
        if plan.period == "year" and price is not None
        else None,
        "last_confirmed_monthly_equivalent": round(last_price / 12, 2)
        if plan.period == "year" and last_price is not None else None,
        "commitment": plan.commitment,
        "eligibility": plan.eligibility,
        "region": plan.region,
        "unit": plan.unit,
        "features": plan.features,
        "url": plan.url,
        "status": status,
        "checked_at": to_utc_iso_z(plan.checked_at),
        "collection_error": plan.last_error,
    }
