"""설명 가능한 통계 예측. 학습된 정확도나 보장된 할인 확률을 주장하지 않습니다."""
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from math import exp, isfinite
from statistics import mean, median


@dataclass
class DiscountEvent:
    start_date: datetime
    end_date: datetime
    max_discount_rate: float
    min_price: int


def as_utc(value):
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def korean_time(value):
    return as_utc(value).replace(tzinfo=timezone.utc).astimezone(timezone(timedelta(hours=9)))


def number(value, default=0.0):
    try:
        result = float(value)
        return result if isfinite(result) else default
    except (TypeError, ValueError, OverflowError):
        return default


def clean_history(prices, now):
    # 같은 날 여러 번 수집한 값은 가장 최근 값만 사용합니다.
    daily = {}
    for row in sorted(prices, key=lambda p: as_utc(p.collected_at)):
        at = as_utc(row.collected_at)
        if at > now or not getattr(row, "is_available", True):
            continue
        amount = number(getattr(row, "discounted_price", None), -1)
        rate = number(getattr(row, "discount_rate", None), -1)
        if amount < 0 or not 0 <= rate <= 100:
            continue
        if getattr(row, "currency", "KRW") != "KRW":
            continue
        original = number(getattr(row, "original_price", None), -1)
        if original > 0 and (amount > original or abs(100 * (1 - amount / original) - rate) > 1):
            continue
        daily[at.date()] = row
    return list(daily.values())


def extract_discount_events(price_snapshots):
    events, active, previous = [], None, None
    for row in sorted(price_snapshots, key=lambda p: as_utc(p.collected_at)):
        at = as_utc(row.collected_at)
        rate = number(row.discount_rate)
        previous_end = getattr(previous, "sale_end_at", None) if previous else None
        # 수집 공백만으로 서로 다른 세일이었다고 추정하지 않습니다.
        # 가격 변경 이력은 다음 변경까지 같은 상태가 이어질 수 있습니다.
        separated = active and previous_end and as_utc(previous_end) < at
        if active and (rate <= 0 or separated):
            events.append(active)
            active = None
        if rate > 0:
            amount = int(number(row.discounted_price))
            if active is None:
                active = DiscountEvent(at, at, rate, amount)
            else:
                active.end_date = at
                active.max_discount_rate = max(active.max_discount_rate, rate)
                active.min_price = min(active.min_price, amount)
        previous = row
    if active:
        events.append(active)
    return events


def calculate_intervals(events):
    return [(b.start_date - a.start_date).days for a, b in zip(events, events[1:])
            if (b.start_date - a.start_date).days > 0]


def fit_cycle(intervals):
    center = median(intervals)
    mad = median(abs(x - center) for x in intervals)
    # 이상 주기를 제거하지 않고 범위를 제한해 작은 표본도 보존합니다.
    radius = max(7, 3 * 1.4826 * mad, center * .25)
    clipped = [max(center - radius, min(center + radius, x)) for x in intervals]
    weights = [exp(-.28 * (len(clipped) - 1 - i)) for i in range(len(clipped))]
    cycle = sum(x * w for x, w in zip(clipped, weights)) / sum(weights)
    spread = max(5, 1.4826 * mad, cycle * .12)
    return cycle, spread, sum(x != y for x, y in zip(intervals, clipped))


def rolling_backtest(events):
    errors = []
    for i in range(max(3, len(events) - 12), len(events)):
        cycle, _, _ = fit_cycle(calculate_intervals(events[:i]))
        actual = (events[i].start_date - events[i - 1].start_date).days
        errors.append(abs(actual - cycle))
    return errors


def quantile(values, q):
    values = sorted(values)
    pos = (len(values) - 1) * q
    lo = int(pos)
    return values[lo] + (values[min(lo + 1, len(values) - 1)] - values[lo]) * (pos - lo)


def predict_discount_from_history(price_snapshots, now=None):
    now = as_utc(now or datetime.now(timezone.utc))
    prices = clean_history(price_snapshots or [], now)
    events = extract_discount_events(prices)
    intervals = calculate_intervals(events)
    latest = prices[-1] if prices else None
    age = (now - as_utc(latest.collected_at)).days if latest else None
    current_sale = bool(latest and number(latest.discount_rate) > 0 and age <= 7
                        and (not getattr(latest, "sale_end_at", None)
                             or as_utc(latest.sale_end_at) > now))
    result = {
        "model_version": "recency-robust-seasonal-v2",
        "expected_date": "예측 불가", "expected_event": "데이터 부족",
        "expected_discount_rate": "예측 불가", "confidence": "낮음",
        "confidence_score": 0, "confidence_note": "관측 자료의 참고 점수이며 예측 정확도나 할인 확률이 아닙니다.",
        "reason": "서로 구분되는 할인 이벤트가 3개 이상 필요합니다.",
        "forecast_window": None, "backtest": {"samples": 0, "mae_days": None},
        "current_sale": current_sale, "buy_signal": "판단 보류",
        "evidence": {"snapshot_count": len(prices), "event_count": len(events),
                     "latest_observation_age_days": age},
        "debug": {"event_count": len(events), "average_interval_days": None, "average_discount_rate": None},
    }
    if len(events) < 3 or len(intervals) < 2:
        return result
    cycle, spread, outliers = fit_cycle(intervals)
    errors = rolling_backtest(events)
    mae = mean(errors) if errors else None
    if mae is not None:
        spread = max(spread, mae)
    elapsed = (now - events[-1].start_date).days
    result["backtest"] = {"samples": len(errors), "mae_days": round(mae, 1) if mae is not None else None}
    result["evidence"].update({"weighted_cycle_days": round(cycle, 1), "spread_days": round(spread, 1),
                               "outlier_intervals": outliers})
    if age > 30 or elapsed > max(60, cycle * 2.5) or cycle - elapsed > 180:
        result.update(expected_event="최신 이력 필요",
                      reason="최근 관측이 오래되었거나 기존 할인 주기를 크게 벗어났습니다. 가격 이력을 갱신한 뒤 다시 분석합니다.")
        return result
    # 다음 주기 분포를 오늘 이후에 조건화합니다. 지난 날짜를 미래 예측으로 표시하지 않습니다.
    months = {month: 1 for month in range(1, 13)}
    for event in events:
        months[korean_time(event.start_date).month] += exp(-(now - event.start_date).days / 365)
    base_month = sum(months.values()) / 12
    strength = min(.25, len(events) / 40)
    days, scores = [], []
    for offset in range(1, 181):
        day = now + timedelta(days=offset)
        recurrence = exp(-.5 * ((elapsed + offset - cycle) / spread) ** 2)
        seasonal = months[korean_time(day).month] / base_month
        scores.append(max(1e-300, recurrence) * (1 - strength + strength * seasonal))
        days.append(day)
    total, cumulative, bounds = sum(scores), 0, {}
    for day, score in zip(days, scores):
        cumulative += score / total
        for q in (.2, .5, .8):
            if q not in bounds and cumulative >= q:
                bounds[q] = day
    start, predicted, end = (bounds.get(q, days[-1]) for q in (.2, .5, .8))
    start, predicted, end = (korean_time(day) for day in (start, predicted, end))
    rates = [e.max_discount_rate for e in events[-8:]]
    lower, upper = round(quantile(rates, .2)), round(quantile(rates, .8))
    regularity = max(0, 1 - spread / max(cycle, 1))
    evidence = min(1, len(intervals) / 8)
    freshness = exp(-age / 30)
    validation = max(0, 1 - mae / max(cycle, 1)) if mae is not None else .25
    score = round(100 * (.35 * evidence + .3 * regularity + .2 * freshness + .15 * validation))
    score = round(score * exp(-max(0, elapsed - cycle) / spread))
    if len(events) < 5 or len(errors) < 2:
        score = min(score, 59)
    result.update({
        "expected_date": f"{start:%Y.%m.%d} ~ {end:%m.%d}",
        "expected_event": "최근 할인 주기·월별 이력 기반 예상",
        "expected_discount_rate": f"{lower}% ~ {upper}%" if lower != upper else f"{lower}% 내외",
        "confidence": "높음" if score >= 80 else "중간" if score >= 55 else "낮음",
        "confidence_score": score,
        "forecast_window": {"start": f"{start:%Y-%m-%d}", "end": f"{end:%Y-%m-%d}",
                            "median": f"{predicted:%Y-%m-%d}", "timezone": "Asia/Seoul", "kind": "모형 분포의 중앙 60% 참고 구간"},
        "reason": f"할인 {len(events)}회와 일별 관측 {len(prices)}개를 분석했습니다. 최근 주기에 가중치를 두고 이상 간격을 완화한 주기는 약 {cycle:.0f}일입니다. 월별 할인 이력을 보조 반영하며, 수집 공백과 신규 이벤트로 결과가 달라질 수 있습니다.",
        "buy_signal": "할인 진행 중 · 역대 가격과 비교" if current_sale else "다음 할인 대기 고려",
        "debug": {"event_count": len(events), "average_interval_days": round(cycle),
                  "average_discount_rate": round(mean(rates), 2),
                  "last_discount_start": f"{korean_time(events[-1].start_date):%Y-%m-%d}", "predicted_date": f"{predicted:%Y-%m-%d}"},
    })
    if errors:
        result["reason"] += f" 과거 시점 순차 검증 {len(errors)}회의 평균 날짜 오차는 {mae:.1f}일입니다."
    if current_sale:
        result["reason"] += " 현재 할인 중이며 예상 구간은 그 다음 할인 시작 기준입니다."
    return result
