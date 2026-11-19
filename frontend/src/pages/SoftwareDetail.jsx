import { safeExternalUrl } from "../utils/site-config";
import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import api from "../api/client";
import ProductLogo from "../components/ProductLogo";
import { formatCheckedAt, formatSubscriptionMoney, offerStatusLabels } from "../utils/subscription-format";

export default function SoftwareDetail() {
  const { softwareId } = useParams();
  const [software, setSoftware] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [billing, setBilling] = useState("all");
  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true);
    api.get(`/api/software/${softwareId}`, { signal: controller.signal }).then((response) => {
      setSoftware(response.data); setError("");
    }).catch((error) => {
      if (!controller.signal.aborted) setError(error.response?.data?.detail || "제품 정보를 불러오지 못했습니다.");
    }).finally(() => { if (!controller.signal.aborted) setIsLoading(false); });
    return () => controller.abort();
  }, [softwareId]);
  if (isLoading) return <p className="loading-text">제품 정보를 불러오는 중입니다...</p>;
  if (error || !software) return <div className="page empty-panel" role="alert"><p>{error || "제품 정보를 찾을 수 없습니다."}</p><Link to="/software">목록으로</Link></div>;

  return <div className="page detail-page software-page software-detail-page">
    <div className="breadcrumb"><Link to="/software">프로그램·AI</Link><span>&gt;</span><span>{software.category}</span><span>&gt;</span><strong>{software.name}</strong></div>
    <section className="detail-hero software-detail-hero">
      <div className="cover detail-cover" style={{ backgroundColor: software.image_color }}><ProductLogo software={software} /><span className="software-brand-name">{software.developer}</span><small>{software.kind === "ai" ? "AI 구독" : "프로그램"}</small></div>
      <div className="detail-main-info">
        <h1>{software.name}</h1><p>{software.developer} · {software.category}</p>
        <div className="lowest-price-box software-benefit-box"><span>대표 혜택</span><strong>{software.promotion?.title || "현재 확인된 혜택 없음"}</strong><p>적용 대상과 결제 조건을 아래에서 확인하세요.</p></div>
        <div className="detail-button-row">{safeExternalUrl(software.official_url) && <a href={safeExternalUrl(software.official_url)} className="primary-button" target="_blank" rel="noreferrer">공식 사이트</a>}<Link to="/software" className="secondary-button">목록으로</Link></div>
        <p className="software-description">{software.description}</p>
      </div>
    </section>
    <section className="detail-grid">
      <article className="panel software-plans-panel">
        <div className="section-title-row"><div><h2>정식 요금제</h2><p className="subscription-notice">계정당·구성원당 금액과 실제 청구 기간을 확인하세요. 연간 금액은 1년 전체 결제액입니다.</p></div>
          <div className="period-buttons" aria-label="결제 기간">{[{ value: "all", label: "전체" }, { value: "month", label: "월간" }, { value: "year", label: "연간" }, { value: "once", label: "일회성·무료" }].map((item) => <button key={item.value} className={billing === item.value ? "active" : ""} aria-pressed={billing === item.value} onClick={() => setBilling(item.value)}>{item.label}</button>)}</div></div>
        <div className="software-plan-grid">{(software.plans || []).filter((plan) => billing === "all" || plan.period === billing).map((plan) => {
          const price = plan.current_price ?? plan.last_confirmed_price;
          const monthly = plan.monthly_equivalent ?? plan.last_confirmed_monthly_equivalent;
          return <div className="software-plan-card" key={plan.id}>
            <div className="subscription-promo-title"><h3>{plan.name}</h3><span className={`offer-status ${plan.status === "verified" ? "active" : "needs_check"}`}>{plan.status === "verified" ? "공식 요금 확인" : "가격 재확인 필요"}</span></div>
            {plan.current_price === null && plan.last_confirmed_price !== null && <p className="subscription-notice">최근 확인 금액 · {formatCheckedAt(plan.checked_at)} 기준</p>}
            {plan.collection_error && <p className="subscription-notice">자동 확인 안내: {plan.collection_error}</p>}
            <strong className="plan-price">{formatSubscriptionMoney(price, plan.currency)}<small> / {plan.period === "year" ? "년" : plan.period === "month" ? "월" : "1회"} · {plan.unit}</small></strong>
            {plan.regular_price > price && price != null && <p>정가 <del>{formatSubscriptionMoney(plan.regular_price, plan.currency)}</del> · 공식 표시 특가</p>}
            {monthly != null && <p className="subscription-period">월 환산 {formatSubscriptionMoney(monthly, plan.currency)} · 연간 선결제{plan.status !== "verified" && " · 최근 확인 금액 기준"}</p>}
            {plan.annual_saving_rate > 0 && <span className="catalog-status">월간 결제 12개월 합계보다 {plan.annual_saving_rate}% 절약</span>}
            <p>{plan.features}</p><dl className="subscription-conditions"><div><dt>청구</dt><dd>{plan.billing_period}</dd></div><div><dt>약정</dt><dd>{plan.commitment}</dd></div><div><dt>대상</dt><dd>{plan.eligibility}</dd></div><div><dt>지역</dt><dd>{plan.region}</dd></div><div><dt>확인일</dt><dd>{formatCheckedAt(plan.checked_at)}</dd></div></dl>
            {safeExternalUrl(plan.url) && <a className="secondary-button" href={safeExternalUrl(plan.url)} target="_blank" rel="noreferrer">공식 요금·가입 조건 ↗</a>}
          </div>;
        })}</div>
        {!(software.plans || []).some((plan) => billing === "all" || plan.period === billing) && <p>이 결제 기간의 공개 요금제 정보가 없습니다.</p>}
      </article>
      <article className="panel software-promo-panel"><h2>할인·구독·교육 혜택</h2>
        <div className="promotion-list">
          {software.promotions.length === 0 && <p>현재 등록된 혜택이 없습니다.</p>}
          {software.promotions.map((promotion) => <div className="promotion-card" key={promotion.id}>
            <div className="subscription-promo-title"><span className="software-category">{promotion.promo_type}</span><span className={`offer-status ${promotion.status}`}>{offerStatusLabels[promotion.status]}</span></div>
            <h3>{promotion.title}</h3><p>{promotion.description}</p>
            <div className="promotion-price-box"><strong>{promotion.discounted_price != null ? formatSubscriptionMoney(promotion.discounted_price, promotion.currency) : promotion.last_confirmed_price != null ? `최근 확인 ${formatSubscriptionMoney(promotion.last_confirmed_price, promotion.currency)}` : promotion.is_offer ? "자격·지역에 따라 적용" : "대상별 혜택 안내"}</strong>
              {promotion.original_price > 0 && <del>{formatSubscriptionMoney(promotion.original_price, promotion.currency)}</del>}
              {promotion.discount_rate > 0 && <span className="discount-badge">{promotion.discount_rate}% 절약</span>}
            </div>
            <dl className="subscription-conditions">
              <div><dt>결제 주기</dt><dd>{promotion.billing_period || "공식 조건 확인"} · {promotion.currency}</dd></div>
              <div><dt>대상</dt><dd>{promotion.eligibility || "공식 가입 조건 확인"}</dd></div>
              <div><dt>지역</dt><dd>{promotion.region || "공식 사이트 확인"}</dd></div>
              <div><dt>신청 마감</dt><dd>{promotion.end_date || "종료일 공개 안 됨"}</dd></div>
              <div><dt>갱신·약정</dt><dd>{promotion.renewal_notice || "혜택 종료 후 요금 및 해지 조건을 확인하세요."}</dd></div>
              <div><dt>최근 확인</dt><dd>{formatCheckedAt(promotion.checked_at)}</dd></div>
            </dl>
            {["needs_check", "stale", "reference"].includes(promotion.status) && <p className="subscription-notice">최신 적용 금액과 조건은 아래 공식 페이지에서 확인하세요.</p>}
            {safeExternalUrl(promotion.url) && <a href={safeExternalUrl(promotion.url)} className="primary-button" target="_blank" rel="noreferrer">공식 혜택·조건 확인 ↗</a>}
          </div>)}
        </div>
      </article>
      <article className="panel prediction-panel"><h2>혜택을 비교할 때</h2>
        <p className="prediction-reason">연간 금액은 월간 구독 12개월 합계와 비교합니다. 학생용 무료 혜택과 일반 구독 할인은 대상이 다르며, USD 요금은 원화로 임의 변환하지 않습니다.</p>
        <p className="prediction-reason">제휴·초대 혜택은 모든 계정에 적용되는 할인이 아닙니다. 신청 화면에서 자격, 무료 기간 이후 요금과 자동 결제 여부를 확인하세요.</p>
      </article>
    </section>
  </div>;
}
