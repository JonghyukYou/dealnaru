"""JetBrains 공식 HTML의 productsData에서 상품·대상·통화·기간을 검증합니다."""
import json
import re
from decimal import Decimal

from bs4 import BeautifulSoup


def jetbrains_price_documents(html):
    documents = []
    for script in BeautifulSoup(html, "html.parser").select("script:not([src])"):
        text = script.string or script.get_text()
        for match in re.finditer(r"\b(?:var\s+|let\s+|const\s+|window\.)?productsData\s*=\s*", text):
            try:
                value, _ = json.JSONDecoder().raw_decode(text[match.end():].lstrip())
            except (ValueError, TypeError):
                continue
            if isinstance(value, dict) and isinstance(value.get("currency"), dict):
                documents.append(value)
    return documents


def parse_jetbrains_plan_price(entry, html):
    code = entry.get("pricing_product", "ALL")
    section = entry.get("pricing_section", "personal")
    interval = {"month": "monthly", "year": "yearly"}.get(entry["period"])
    if section not in {"personal", "commercial"} or not interval:
        raise ValueError("지원하지 않는 JetBrains 대상·청구 기간입니다.")
    amounts = set()
    for data in jetbrains_price_documents(html):
        currency = data["currency"]
        if currency.get("iso") != entry["currency"]:
            continue
        product = data.get(code)
        if not isinstance(product, dict) or product.get("code") != code:
            continue
        groups = product.get("prices")
        if not isinstance(groups, dict) or not isinstance(groups.get(section), dict):
            continue
        prices = groups[section].get(interval)
        if not isinstance(prices, list) or not prices or not isinstance(prices[0], str):
            continue
        # yearly의 두 번째·세 번째 값은 계속 구독 할인. 신규 가입 요금만 선택합니다.
        # yearlyPerMonth는 월 환산액이므로 연간 결제액으로 사용하지 않습니다.
        symbol = currency.get("symbol")
        if not isinstance(symbol, str) or not symbol:
            continue
        pattern = re.escape(symbol) + r"\s*([\d,]+(?:\.\d+)?)" if currency.get("isPrefix") else r"([\d,]+(?:\.\d+)?)\s*" + re.escape(symbol)
        match = re.fullmatch(pattern, prices[0].strip())
        if match:
            amount = Decimal(match.group(1).replace(",", ""))
            if amount.is_finite() and amount > 0:
                amounts.add(amount)
    if len(amounts) == 1:
        return amounts.pop()
    if len(amounts) > 1:
        raise ValueError("JetBrains 동일 요금제에 여러 가격 후보가 있어 확정하지 않습니다.")
    raise ValueError("JetBrains 공식 상품·대상·통화·결제 기간이 일치하는 가격을 확인하지 못했습니다.")
