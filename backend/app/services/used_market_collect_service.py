"""공개 중고나라 매물 수집. robots/접속 제한을 확인하고 차단 시 중단합니다."""
import json
import os
import re
from datetime import timedelta
from html import unescape
from time import monotonic, sleep
from threading import Lock
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup
from sqlalchemy import or_

from app.models import UsedMarketItem
from app.services.used_market_moderation_service import ExcludedMarketItem, is_excluded
from app.utils.time import utc_now

BASE_SEARCH_URL = "https://web.joongna.com/search/{keyword}"
PROVIDERS = {
    "joongna": {"name": "중고나라", "origin": "https://web.joongna.com", "paths": ("/product/", "/products/")},
}
HEADERS = {"User-Agent": "DealNaruPriceCollector/1.1", "Accept-Language": "ko-KR,ko;q=0.9"}
DEFAULT_KEYWORDS = ["게임CD", "닌텐도", "PS5", "PS4"]
DEFAULT_USED_MARKET_MAX_AGE_DAYS = 14
MAX_USED_GAME_PRICE = 200000
EXCLUDE_KEYWORDS = ["계정", "다운로드", "디지털", "코드", "쿠폰", "대리", "강의", "치트", "본체", "게임기", "콘솔", "디스크에디션", "디스크 에디션", "슬림", "프로콘", "조이콘", "듀얼센스", "듀얼쇼크", "컨트롤러", "패드", "리모컨", "리모콘", "충전기", "충전독", "거치대", "어댑터", "케이블", "케이스", "파우치", "보호필름", "가방", "메모리", "헤드셋", "이어폰", "스피커", "삽니다", "구매합니다", "구매글", "구해요", "일괄", "개당", "각각", "가격협의"]
_ROBOTS, _LAST_REQUEST = {}, {}
_FETCH_LOCK = Lock()


class CollectionUnavailable(ValueError):
    def __init__(self, message, *, reason_code="access_restricted", stage="page", http_status=None):
        super().__init__(message)
        self.reason_code = reason_code
        self.stage = stage
        self.http_status = http_status


def get_used_market_max_age_days():
    try:
        return max(1, int(os.getenv("USED_MARKET_MAX_AGE_DAYS", "14")))
    except (TypeError, ValueError):
        return 14


def get_used_market_cutoff(*, max_age_days=None, now=None):
    return (now or utc_now()) - timedelta(days=max_age_days or get_used_market_max_age_days())


def delete_stale_used_market_items(db, *, max_age_days=None, now=None, commit=False):
    count = db.query(UsedMarketItem).filter(UsedMarketItem.collected_at < get_used_market_cutoff(max_age_days=max_age_days, now=now)).delete(synchronize_session=False)
    db.commit() if commit else db.flush()
    return count


def remove_retired_market_data(db):
    """사용자가 제거한 이전 출처의 수집 행만 정리하며 중고나라 데이터는 보존합니다."""
    return db.query(UsedMarketItem).filter(or_(
        UsedMarketItem.source.in_(["당근", "당근마켓", "Daangn", "daangn"]),
        UsedMarketItem.item_url.startswith("https://www.daangn.com/"),
    )).delete(synchronize_session=False)


def clean_text(value):
    return " ".join(unescape(str(value or "")).split())


def normalize_listing_title(value, price=None):
    """가격·시간을 상품명으로 채택하지 않고, 붙어 있는 동일 가격만 분리합니다."""
    title = clean_text(value)
    if not title:
        return None
    # 제목 뒤의 가격이 실제 파싱한 가격과 같을 때만 제거합니다.
    # FIFA 17, 428, PS4 등의 상품명에 포함된 숫자는 보존합니다.
    for match in re.finditer(r"(?:₩\s*)?\d[\d,]*(?:\.\d+)?\s*(?:만\s*(?:\d+\s*천)?\s*)?원", title):
        if parse_price(match.group()) == price:
            title = title[:match.start()].strip(" |·-–—:₩")
            break
    trailing = re.search(r"(?:^|\s)(?:₩\s*)?(\d[\d,]*)\s*$", title)
    if trailing and price is not None and int(trailing.group(1).replace(",", "")) == price:
        title = title[:trailing.start()].strip(" |·-–—:")
    if not title or re.fullmatch(r"[\d\s,₩.$]+(?:원|만원|/월)?", title):
        return None
    if re.fullmatch(r"\d+\s*(?:초|분|시간|일|개월|년)\s*전|판매\s*완료|예약\s*중|판매\s*중|무료\s*배송|배송비\s*포함", title):
        return None
    if title.casefold() in {"상품 이미지", "상품이미지", "product image", "image", "thumbnail", "중고나라", "찜", "관심"}:
        return None
    return title[:255]


def repair_used_market_titles(db, commit=False):
    """이전 수집으로 손상된 제목을 정리. 원래 이름을 모르면 재수집 전까지 제외."""
    cleaned = quarantined = 0
    rows = db.query(UsedMarketItem).filter(
        UsedMarketItem.source == "중고나라",
        UsedMarketItem.status.in_(["판매중", "제목 확인 필요"]),
    ).all()
    for item in rows:
        title = normalize_listing_title(item.title, item.price)
        if title is None:
            if item.status != "제목 확인 필요":
                item.status = "제목 확인 필요"
                quarantined += 1
        elif title != item.title:
            item.title = title
            cleaned += 1
    db.commit() if commit else db.flush()
    return {"cleaned_titles": cleaned, "quarantined_titles": quarantined}


def extract_listing_title(link, card, price):
    candidates = []
    # title이라는 이름의 속성/필드부터 확인합니다. h3가 가격인 카드도 있습니다.
    for selector in ("[data-testid*='product-name']", "[data-testid*='title']", "[class*='productName']", "[class*='product-name']", "[class*='title']"):
        candidates.extend(node.get_text(" ", strip=True) for node in card.select(selector))
    candidates.extend([link.get("title"), link.get("aria-label")])
    candidates.extend(node.get_text(" ", strip=True) for node in link.select("h1,h2,h3,h4,h5,h6"))
    candidates.extend(node.get("alt") for node in link.select("img[alt]"))
    # 중고나라의 실제 상품명은 일반 p/div이고 가격만 h3인 형태도 처리합니다.
    candidates.extend(link.stripped_strings)
    if card is not link:
        candidates.extend(card.stripped_strings)
    for candidate in candidates:
        title = normalize_listing_title(candidate, price)
        if title and not should_skip_market_item(title, price):
            return title
    return None


def extract_card_price(card):
    """원 표시가 생략된 가격 노드도 읽되, 인접 매물의 가격과 섞지 않습니다."""
    nodes = card.select("[data-testid*='price'],[class*='price'],[class*='Price']")
    amounts = {amount for node in nodes if (amount := parse_price(node.get_text(" ", strip=True))) is not None}
    if amounts:
        return amounts.pop() if len(amounts) == 1 else None
    amount = parse_price(card.get_text(" ", strip=True))
    if amount is not None:
        return amount
    leaves = [clean_text(text) for text in card.stripped_strings]
    amounts = {amount for text in leaves
               if re.fullmatch(r"(?:₩\s*)?\d[\d,]*(?:\.\d+)?\s*(?:원|만원)?", text)
               and (amount := parse_price(text)) is not None}
    return amounts.pop() if len(amounts) == 1 else None


def parse_price(value):
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value) if value >= 0 and float(value).is_integer() else None
    text = clean_text(value).replace(",", "")
    if re.search(r"협의|문의|나눔|무료|개당|각각|~", text):
        return None
    if re.fullmatch(r"\d{1,9}", text):
        return int(text)
    if re.fullmatch(r"₩\s*\d{1,9}", text):
        return int(re.sub(r"\D", "", text))
    matches = re.findall(r"(?<![\d.])(\d+(?:\.\d+)?)\s*만\s*(?:(\d+(?:\.\d+)?)\s*천)?\s*(\d{1,4})?\s*원", text)
    if matches:
        return int(float(matches[0][0]) * 10000 + float(matches[0][1] or 0) * 1000 + int(matches[0][2] or 0)) if len(matches) == 1 else None
    matches = re.findall(r"(?<![\d.])(\d{1,9})\s*원", text)
    return int(matches[0]) if len(matches) == 1 else None


def should_skip_market_item(title, price):
    normalized = clean_text(title).lower().replace(" ", "")
    return (normalize_listing_title(title, price) is None or len(normalized) < 3 or price is None or price <= 0 or price >= MAX_USED_GAME_PRICE
            or any(word.replace(" ", "") in normalized for word in EXCLUDE_KEYWORDS)
            or bool(re.search(r"\b(?:dl|download|hdmi|dock|ssd|vr)\b", clean_text(title), re.I)))


def build_search_url(source, keyword):
    if source != "joongna":
        raise ValueError("중고나라만 지원합니다.")
    return BASE_SEARCH_URL.format(keyword=quote(keyword, safe=""))


def canonical_item_url(href, source):
    if source not in PROVIDERS:
        return None
    config = PROVIDERS[source]
    parsed = urlsplit(urljoin(config["origin"], str(href or "")))
    if (parsed.scheme != "https" or parsed.netloc != urlsplit(config["origin"]).netloc
            or not any(parsed.path.startswith(path) and parsed.path.rstrip("/") != path.rstrip("/") for path in config["paths"])
            or parsed.path.rstrip("/").endswith(("/all", "/search"))):
        return None
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path.rstrip("/"), "", ""))


def _request_text(url):
    # 리다이렉트/쿠키/로그인/프록시 우회 없이 고정 출처만 요청합니다.
    with requests.get(url, headers=HEADERS, timeout=(10, 20), allow_redirects=False, stream=True) as response:
        if response.status_code in {401, 403, 429}:
            raise CollectionUnavailable(
                f"공개 요청이 거부되었습니다 (HTTP {response.status_code}). 출처/CDN의 접근 제한이며 매물이 0개라는 뜻이 아닙니다.",
                reason_code=f"http_{response.status_code}", http_status=response.status_code)
        if 300 <= response.status_code < 400:
            raise CollectionUnavailable("출처가 다른 페이지로 이동시켜 수집을 중단했습니다.", reason_code="redirect", http_status=response.status_code)
        response.raise_for_status()
        content = bytearray()
        for chunk in response.iter_content(65536):
            content.extend(chunk)
            if len(content) > 3_000_000:
                raise CollectionUnavailable("공개 페이지 크기 제한을 초과했습니다.")
        return bytes(content).decode("utf-8", errors="replace")


def fetch_public_page(url, source):
    origin = PROVIDERS[source]["origin"]
    if not url.startswith(origin + "/"):
        raise ValueError("허용되지 않은 수집 주소")
    with _FETCH_LOCK:
        cached = _ROBOTS.get(origin)
        if not cached or monotonic() - cached[0] > 900:
            try:
                robots_text = _request_text(origin + "/robots.txt")
            except CollectionUnavailable as error:
                raise CollectionUnavailable(
                    f"robots.txt 확인 단계에서 중단했습니다. {error}",
                    reason_code=error.reason_code, stage="robots", http_status=error.http_status) from error
            except requests.Timeout as error:
                raise CollectionUnavailable("robots.txt 응답 시간이 초과되어 허용 여부를 확인하지 못했습니다.", reason_code="robots_timeout", stage="robots") from error
            except requests.RequestException as error:
                status = error.response.status_code if error.response is not None else None
                suffix = f" (HTTP {status})" if status else ""
                raise CollectionUnavailable("robots.txt에 연결하지 못해 허용 여부를 확인하지 못했습니다." + suffix, reason_code="robots_unavailable", stage="robots", http_status=status) from error
            robot = RobotFileParser()
            robot.parse(robots_text.splitlines())
            _ROBOTS[origin] = (monotonic(), robot)
        else:
            robot = cached[1]
        if not robot.can_fetch(HEADERS["User-Agent"], url):
            raise CollectionUnavailable("출처의 robots.txt에서 수집을 허용하지 않습니다.", reason_code="robots_disallowed", stage="robots")
        delay = max(2, robot.crawl_delay(HEADERS["User-Agent"]) or robot.crawl_delay("*") or 0)
        sleep(max(0, delay - (monotonic() - _LAST_REQUEST.get(origin, 0))))
        _LAST_REQUEST[origin] = monotonic()
        html = _request_text(url)
    if re.search(r"verify you are human|captcha|access denied|just a moment", html[:20000], re.I):
        raise CollectionUnavailable("출처의 자동 접속 제한으로 수집을 중단했습니다.")
    return html


def listing_status(value):
    text = clean_text(value).lower()
    if any(token in text for token in ("sold", "reserved", "거래완료", "판매완료", "거래 완료", "판매 완료", "예약", "outofstock", "discontinued")):
        return "판매완료"
    if text in {"1", "2", "3"}:  # 숫자 상태는 출처별 의미가 불명확해 판매중으로 추정하지 않음
        return "상태 확인 필요"
    return "판매중" if text in {"", "0", "on_sale", "for_sale", "selling", "sale", "available", "판매중", "판매 중"} or "instock" in text else "상태 확인 필요"


def json_documents(soup):
    for script in soup.select("script"):
        raw = script.string or script.get_text()
        if not raw or len(raw) > 3_000_000:
            continue
        if script.get("type") in {"application/json", "application/ld+json"} or script.get("id") == "__NEXT_DATA__":
            try:
                yield json.loads(raw)
            except (ValueError, TypeError):
                continue


def extract_structured_items(soup, source, keyword):
    items = []
    def walk(value):
        if isinstance(value, list):
            for child in value:
                walk(child)
        elif isinstance(value, dict):
            title_candidates = [value.get("productName"), value.get("title"), value.get("name")]
            href = value.get("url") or value.get("href") or value.get("articleUrl") or value.get("detailUrl")
            if source == "joongna" and not href and value.get("productSeq"):
                href = f"/product/{value['productSeq']}"
            url = canonical_item_url(href, source)
            offer = value.get("offers") if isinstance(value.get("offers"), dict) else {}
            raw_price = next((value[key] for key in ("price", "productPrice", "salePrice") if value.get(key) is not None), offer.get("price"))
            price = parse_price(raw_price)
            title = next((title for value in title_candidates if (title := normalize_listing_title(value, price))), None)
            status = listing_status(value.get("tradeStatus") or value.get("status") or value.get("saleStatus") or offer.get("availability"))
            currency = value.get("currency") or offer.get("priceCurrency") or "KRW"
            if title and url and currency == "KRW" and not should_skip_market_item(title, price) and status == "판매중":
                region = value.get("regionName") or value.get("region")
                if isinstance(region, dict):
                    region = region.get("name") or region.get("name3")
                items.append({"source": PROVIDERS[source]["name"], "keyword": keyword,
                              "title": clean_text(title)[:255], "price": price, "item_url": url,
                              "location": clean_text(region)[:100] or None, "status": status})
            for child in value.values():
                if isinstance(child, (dict, list)):
                    walk(child)
    for document in json_documents(soup):
        walk(document)
    return items


def extract_posted_at_text(text):
    match = re.search(r"\d+\s*(?:초|분|시간|일|개월|년)\s*전", text)
    return match.group() if match else None


def parse_market_page(html, source, keyword, limit):
    soup = BeautifulSoup(html, "html.parser")
    items = extract_structured_items(soup, source, keyword)
    for link in soup.select("a[href]"):
        url = canonical_item_url(link.get("href"), source)
        if not url:
            continue
        card, title, price = link, None, None
        for _ in range(4):
            price = extract_card_price(card)
            title = extract_listing_title(link, card, price) if price is not None else None
            if price is not None and title:
                break
            parent = card.parent
            if not parent or parent.name in {"main", "body", "html", "ul", "ol"} or len({canonical_item_url(a.get("href"), source) for a in parent.select("a[href]") if canonical_item_url(a.get("href"), source)}) > 1:
                break
            card = parent
        text = clean_text(card.get_text(" ", strip=True))
        if listing_status("판매완료" if re.search(r"판매\s*완료|거래\s*완료|예약", text) else "판매중") != "판매중":
            continue
        if should_skip_market_item(title, price):
            continue
        items.append({"source": PROVIDERS[source]["name"], "keyword": keyword, "title": title,
                      "price": price, "item_url": url, "status": "판매중", "posted_at_text": extract_posted_at_text(text)})
    # 직접 매물 URL을 확인하지 못한 텍스트는 매물로 만들지 않습니다.
    unique = {}
    for item in items:
        unique.setdefault(item["item_url"], item)
    return list(unique.values())[:limit]


def upsert_used_market_item(db, parsed_item):
    if is_excluded(db, keyword=parsed_item.get("keyword"), item_url=parsed_item.get("item_url")):
        raise ExcludedMarketItem("관리자가 삭제한 키워드 또는 매물입니다.")
    title = normalize_listing_title(parsed_item.get("title"), parsed_item.get("price"))
    if not title:
        raise ValueError("가격만 있는 텍스트는 상품명으로 저장할 수 없습니다.")
    parsed_item = {**parsed_item, "title": title}
    source = parsed_item.get("source", "중고나라")
    if source != "중고나라":
        raise ValueError("중고나라 매물만 저장할 수 있습니다.")
    item = db.query(UsedMarketItem).filter_by(source=source, item_url=parsed_item["item_url"]).first()
    created = item is None
    if created:
        item = UsedMarketItem(source=source, item_url=parsed_item["item_url"])
        db.add(item)
    for key in ("keyword", "title", "price", "location", "posted_at_text"):
        if parsed_item.get(key) is not None:
            setattr(item, key, parsed_item[key])
    item.collected_at = utc_now()
    item.status = parsed_item.get("status", "판매중")
    # 판매자의 닉네임/연락처/상세 본문은 수집하지 않습니다.
    db.flush()
    return item, created


def collect_used_market_items(db, keywords=None, limit_per_keyword=5):
    keywords = list(dict.fromkeys(clean_text(k) for k in (keywords or DEFAULT_KEYWORDS) if clean_text(k)))[:10]
    limit = max(1, min(int(limit_per_keyword), 20))
    removed_count = remove_retired_market_data(db)
    repairs = repair_used_market_titles(db)
    result = {"deleted_stale_count": delete_stale_used_market_items(db), **repairs, "imported": [], "updated": [],
              "skipped": [], "errors": [], "source_results": [], "removed_source_count": removed_count}
    for source in ("joongna",):
        summary = {"source": PROVIDERS[source]["name"], "status": "success", "collected_count": 0, "message": ""}
        for keyword in keywords:
            if is_excluded(db, keyword=keyword):
                result["skipped"].append({"source": summary["source"], "keyword": keyword, "reason": "관리자가 재수집에서 제외한 키워드"})
                continue
            try:
                html = fetch_public_page(build_search_url(source, keyword), source)
                parsed = parse_market_page(html, source, keyword, limit)
                if not parsed:
                    result["skipped"].append({"source": summary["source"], "keyword": keyword, "reason": "검색 결과 없음 또는 공개 매물 구조 확인 불가"})
                for parsed_item in parsed:
                    try:
                        with db.begin_nested():
                            item, created = upsert_used_market_item(db, parsed_item)
                    except ExcludedMarketItem:
                        result["skipped"].append({"source": summary["source"], "keyword": keyword, "reason": "관리자가 삭제한 매물"})
                        continue
                    result["imported" if created else "updated"].append({"id": item.id, "source": item.source, "title": item.title, "price": item.price})
                    summary["collected_count"] += 1
            except CollectionUnavailable as error:
                status = "unavailable" if error.reason_code in {"robots_timeout", "robots_unavailable"} else "blocked"
                summary.update(status=status, message=str(error), reason_code=error.reason_code, stage=error.stage, http_status=error.http_status)
                result["errors"].append({"source": summary["source"], "keyword": keyword, "error": str(error)})
                break  # 차단된 출처를 다음 키워드로 재시도하지 않습니다.
            except requests.RequestException as error:
                timed_out = isinstance(error, requests.Timeout)
                status = error.response.status_code if error.response is not None else None
                summary.update(status="unavailable", message="검색 페이지의 응답 시간이 초과되었습니다." if timed_out else "검색 페이지에 연결하지 못했습니다.",
                               reason_code="page_timeout" if timed_out else "page_unavailable", stage="page", http_status=status)
                result["errors"].append({"source": summary["source"], "keyword": keyword, "error": summary["message"]})
                break
        if not summary["collected_count"] and summary["status"] == "success":
            summary.update(status="empty", message="조건에 맞는 공개 매물을 확인하지 못했습니다.")
        result["source_results"].append(summary)
    for key in ("imported", "updated", "skipped"):
        result[key + "_count"] = len(result[key])
    result["error_count"] = len(result["errors"])
    result["pending_title_count"] = db.query(UsedMarketItem).filter(UsedMarketItem.source == "중고나라", UsedMarketItem.status == "제목 확인 필요").count()
    return result
