"""Google 공식 페이지의 공개 지역별 가격 피드를 읽어 빈 가격 요소를 채웁니다.

공식 웹 컴포넌트가 사용하는 피드 파일명을 해당 페이지의 JS에서 확인합니다.
JS 실행·로그인·브라우저 우회 없이 같은 공개 JSON만 읽습니다.
"""
import re
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal, InvalidOperation
from urllib.parse import urljoin, urlsplit

import requests
from bs4 import BeautifulSoup

GOOGLE_ORIGIN = "https://one.google.com"
MAX_MODULES = 12
FEED_NAME = r"pricing_\d{4}_\d{2}_\d{2}\.json"


def fetch_google_resource(url):
    parsed = urlsplit(url)
    if parsed.scheme != "https" or parsed.netloc != "one.google.com" or not parsed.path.startswith(("/about/assets/", "/intl/ALL_kr/about/feeds/")):
        raise ValueError("Google 공식 가격 리소스 주소가 아닙니다.")
    response = requests.get(url, timeout=(10, 20), allow_redirects=False,
                            headers={"User-Agent": "DealNaruPriceCollector/1.1.1", "Accept-Language": "ko-KR,ko;q=0.9"})
    if response.is_redirect:
        raise ValueError("Google 가격 리소스가 다른 주소로 이동했습니다.")
    response.raise_for_status()
    if len(response.content) > 2_000_000:
        raise ValueError("Google 가격 리소스의 크기 제한을 초과했습니다.")
    response.encoding = "utf-8"
    return response.text


def discover_google_feed(html, fetch=fetch_google_resource):
    soup = BeautifulSoup(html, "html.parser")
    urls = []
    for script in soup.select("script[src]"):
        url = urljoin(GOOGLE_ORIGIN, script["src"])
        parsed = urlsplit(url)
        if parsed.netloc == "one.google.com" and parsed.path.startswith("/about/assets/") and url not in urls:
            urls.append(url)
    def inspect(url):
        try:
            script = fetch(url)
        except (requests.RequestException, ValueError):
            return None
        if "g1-localized-price" not in script:
            return None
        default = re.search(r'\|\|\s*[`"\'](' + FEED_NAME + r')', script)
        if default:
            return default.group(1)
        names = set(re.findall(FEED_NAME, script))
        return next(iter(names)) if len(names) == 1 else None
    # 가격 컴포넌트는 현재 첫 모듈 묶음에 있습니다. 이후 구조도 제한된 범위에서 탐색합니다.
    for offset in range(0, min(MAX_MODULES, len(urls)), 4):
        with ThreadPoolExecutor(max_workers=4) as pool:
            names = {name for name in pool.map(inspect, urls[offset:offset + 4]) if name}
        if len(names) == 1:
            return GOOGLE_ORIGIN + "/intl/ALL_kr/about/feeds/" + names.pop()
        if len(names) > 1:
            raise ValueError("Google 공식 가격 피드 버전이 여러 개여서 확정하지 않습니다.")
    raise ValueError("Google 공식 페이지에서 가격 피드 버전을 확인하지 못했습니다.")


def hydrate_google_prices(html, fetch=None):
    import json
    fetch = fetch or fetch_google_resource
    soup = BeautifulSoup(html, "html.parser")
    nodes = soup.select('g1-localized-price[country="KR"][variant]')
    if not nodes:
        return html
    feed_url = discover_google_feed(html, fetch=fetch)
    try:
        feed = json.loads(fetch(feed_url))
    except (TypeError, ValueError) as error:
        raise ValueError("Google 공식 가격 피드의 JSON 형식을 확인하지 못했습니다.") from error
    if not isinstance(feed, dict) or feed.get("COUNTRY_CODE") != "KR" or feed.get("CURRENCY_CODE") != "KRW":
        raise ValueError("Google 가격 피드의 국가·통화가 대한민국·KRW와 다릅니다.")
    updated = 0
    for node in nodes:
        value = feed.get(node["variant"])
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        try:
            amount = Decimal(str(value))
        except InvalidOperation:
            continue
        if not amount.is_finite() or amount < 0 or amount != amount.to_integral_value():
            continue
        node.string = f"₩{int(amount):,}"
        node["data-price-currency"] = "KRW"
        node["data-price-source"] = feed_url
        updated += 1
    if not updated:
        raise ValueError("Google 공식 가격 피드에 유효한 한국 가격이 없습니다.")
    return str(soup)


def parse_google_plan_price(entry, html, body):
    variant = entry.get("price_variant")
    if variant:
        # 요금제별 키에 MONTHLY가 명시되어 있어 체험가/다른 요금제와 섞이지 않습니다.
        nodes = BeautifulSoup(html, "html.parser").find_all("g1-localized-price", attrs={"country": "KR", "variant": variant})
        amounts = set()
        for node in nodes:
            if node.get("data-price-currency") not in {None, entry["currency"]}:
                continue
            match = re.fullmatch(r"₩\s*([\d,]+)", node.get_text(" ", strip=True))
            if match and entry["currency"] == "KRW" and variant.endswith("_MONTHLY") and entry["period"] == "month":
                amounts.add(Decimal(match.group(1).replace(",", "")))
        if len(amounts) == 1:
            return amounts.pop()
        if len(amounts) > 1:
            raise ValueError("Google 동일 요금제에 서로 다른 가격이 표시됩니다.")
    name = re.escape(entry["name"])
    match = re.search(name + r"\s*(?:₩\s*([\d,]+)|([\d,]+)원)\s*/?\s*월", body, re.I)
    if match:
        return Decimal((match.group(1) or match.group(2)).replace(",", ""))
    raise ValueError("Google 공식 한국 요금제 가격을 읽지 못했습니다. 마지막 확인 금액과 날짜를 유지합니다.")
