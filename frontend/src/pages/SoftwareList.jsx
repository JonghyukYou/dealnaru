import { useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../api/client";
import ProductLogo from "../components/ProductLogo";
import GameSearch from "../components/GameSearch";
import { formatCheckedAt, formatSubscriptionMoney, offerStatusLabels } from "../utils/subscription-format";

const categories = ["전체", "디자인", "개발", "생산성", "협업", "AI"];
const kinds = [{ value: "all", label: "전체" }, { value: "software", label: "프로그램" }, { value: "ai", label: "AI 구독" }];

export default function SoftwareList() {
  const [searchParams, setSearchParams] = useSearchParams();
  const keyword = searchParams.get("keyword") || "";
  const [category, setCategory] = useState("전체");
  const [kind, setKind] = useState("all");
  const [offersOnly, setOffersOnly] = useState(false);
  const [billing, setBilling] = useState("all");
  const [currency, setCurrency] = useState("all");
  const [budget, setBudget] = useState("");
  const [freeOnly, setFreeOnly] = useState(false);
  const [softwareList, setSoftwareList] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    const timer = window.setTimeout(async () => {
      setIsLoading(true);
      setError("");
      try {
        const response = await api.get("/api/software", {
          params: { keyword, category, kind, offers_only: offersOnly }, signal: controller.signal,
        });
        setSoftwareList(response.data);
      } catch (error) {
        if (!controller.signal.aborted) setError(error.response?.data?.detail || "목록을 불러오지 못했습니다. 다시 시도해주세요.");
      } finally {
        if (!controller.signal.aborted) setIsLoading(false);
      }
    }, 250);
    return () => { window.clearTimeout(timer); controller.abort(); };
  }, [keyword, category, kind, offersOnly, reload]);

  const displayed = softwareList.map((software) => {
    const plans = (software.plans || []).filter((plan) => (billing === "all" || plan.period === billing) && (currency === "all" || plan.currency === currency) && (!freeOnly || plan.display_price === 0));
    const matching = plans.filter((plan) => budget === "" || (plan.display_price != null && plan.display_price / (plan.period === "year" ? 12 : 1) <= Number(budget)));
    const plan = matching.find((row) => row.id === software.starting_plan?.id) || matching.find((row) => row.display_price != null) || matching[0];
    const filtered = billing !== "all" || currency !== "all" || budget !== "" || freeOnly;
    return filtered && !plan ? null : { ...software, displayPlan: plan || software.starting_plan };
  }).filter(Boolean);

  return (
    <div className="page software-page">
      <section className="category-header subscription-header">
        <span className="subscription-eyebrow">SOFTWARE & AI</span>
        <h1>프로그램·AI 요금제와 혜택</h1>
        <p>이미지·음성 AI부터 개발·영상·문서 도구까지. 월간·연간·일회성 요금과 무료 에디션을 비교하세요.</p>
      </section>
      <section className="software-search-panel">
        <div className="subscription-kind-tabs" aria-label="제품 유형">
          {kinds.map((item) => <button key={item.value} className={kind === item.value ? "active" : ""}
            onClick={() => { setKind(item.value); setCategory("전체"); }} aria-pressed={kind === item.value}>{item.label}</button>)}
        </div>
        <div className="software-budget-filters">
          <label>결제 방식<select value={billing} onChange={(event) => setBilling(event.target.value)}><option value="all">전체</option><option value="month">월간</option><option value="year">연간</option><option value="once">일회성·무료</option></select></label>
          <label>통화<select value={currency} onChange={(event) => { setCurrency(event.target.value); setBudget(""); }}><option value="all">전체 통화</option><option value="KRW">원화 KRW</option><option value="USD">달러 USD</option></select></label>
          <label>예산 상한 ({currency === "all" ? "통화 선택 후 입력" : currency})<input type="number" min="0" step={currency === "KRW" ? "1000" : "1"} disabled={currency === "all"} value={budget} onChange={(event) => setBudget(event.target.value)} placeholder="제한 없음" /></label>
          <label className="inline-checkbox"><input type="checkbox" checked={freeOnly} onChange={(event) => setFreeOnly(event.target.checked)} />무료 요금제 있는 제품</label>
        </div>
        <p className="subscription-notice">예산은 월간 요금·연간 금액의 월 환산액·일회성 구매액을 기준으로 적용합니다. 통화는 자동 환산하지 않습니다. ‘최근 확인 금액’은 표시 날짜의 가격으로 현재 결제 가격과 다를 수 있습니다.</p>
        <GameSearch keyword={keyword} searchLabel="프로그램·AI 검색" placeholder="Adobe, ChatGPT, Gemini, Claude 검색" onSearch={(value) => { setSearchParams(value ? { keyword: value } : {}); setReload((count) => count + 1); }} />
        <div className="subscription-filter-row">
          <div className="genre-button-wrap">
            {(kind === "ai" ? ["전체", "AI"] : categories).map((value) => <button key={value}
              className={category === value ? "active" : ""} onClick={() => setCategory(value)} aria-pressed={category === value}>{value}</button>)}
          </div>
          <label className="subscription-check"><input type="checkbox" checked={offersOnly} onChange={(event) => setOffersOnly(event.target.checked)} /> 확인된 혜택만</label>
        </div>
        <p className="subscription-notice">학생·연간 결제·대상 한정 혜택은 적용 조건이 다릅니다. 표시된 통화와 결제 주기를 확인하세요.</p>
      </section>
      {isLoading ? <p className="loading-text">프로그램·AI 정보를 불러오는 중입니다...</p> : error ? (
        <section className="empty-panel" role="alert"><p>{error}</p><button className="secondary-button" onClick={() => setReload((value) => value + 1)}>다시 시도</button></section>
      ) : displayed.length === 0 ? (
        <section className="empty-panel"><h2>조건에 맞는 제품이 없습니다.</h2><p>검색어를 바꾸거나 ‘확인된 혜택만’을 해제해보세요.</p></section>
      ) : <>
        <p className="subscription-count">{displayed.length}개 제품 · 공식 출처와 최근 확인일 제공</p>
        <section className="software-grid">
          {displayed.map((software) => {
            const promotion = software.promotion;
            const plan = software.displayPlan;
            const hasPlans = software.plan_count > 0;
            const planPrice = plan?.current_price ?? plan?.last_confirmed_price;
            const planKnown = planPrice !== null && planPrice !== undefined;
            const priceLabel = hasPlans ? planKnown ? plan?.status === "verified" ? "공식 요금 확인" : "최근 확인 금액" : "금액 확인 필요" : offerStatusLabels[promotion?.status] || "공식 안내";
            return <Link to={`/software/${software.id}`} className="software-list-card subscription-card" key={software.id}>
              <div className="software-cover" style={{ backgroundColor: software.image_color }}>
                <ProductLogo software={software} /><span className="software-brand-name">{software.developer}</span><small>{software.kind === "ai" ? "AI 구독" : "프로그램"}</small>
              </div>
              <div className="software-list-info">
                <div className="subscription-card-header">
                  <span className="software-category">{software.category} · {software.developer}</span>
                  <h2>{software.name}</h2><p className="software-card-description" title={software.description}>{software.description}</p>
                </div>
                <div className="subscription-card-price">
                  <div className="price-row">
                    <strong>{hasPlans ? formatSubscriptionMoney(planPrice, plan?.currency) : promotion?.discounted_price != null ? formatSubscriptionMoney(promotion.discounted_price, promotion.currency) : "대상별 혜택 안내"}{planKnown && planPrice !== 0 && <small className="software-price-unit"> / {plan.period === "year" ? "년" : plan.period === "month" ? "월" : "1회"}</small>}</strong>
                    {hasPlans && <span className="catalog-status">{software.plan_count}개 요금제</span>}
                    {software.has_free_plan && <span className="catalog-status">무료 요금제 있음</span>}
                  </div>
                  {plan ? <p className="subscription-period">{plan.name} · {plan.billing_period} · {plan.currency}{planKnown && plan.status !== "verified" && " · 최근 확인 금액"}</p> : promotion?.billing_period && <p className="subscription-period">{promotion.billing_period} · {promotion.currency}</p>}
                  {plan?.period === "year" && planPrice != null && <p className="subscription-period">월 환산 {formatSubscriptionMoney(planPrice / 12, plan.currency)} · 연간 전체 금액 선결제</p>}
                  <p className="subscription-card-benefit">{software.offer_count > 0 ? `${software.offer_count}개 혜택 · ${software.promo_title}` : "요금제·공식 조건 보기"}</p>
                </div>
                <div className="subscription-card-footer"><span className={`offer-status ${hasPlans && !planKnown ? "needs_check" : plan?.status === "verified" ? "active" : promotion?.status || "reference"}`}>{priceLabel}</span>
                  <small>{formatCheckedAt(plan?.checked_at || promotion?.checked_at)} 확인</small></div>
              </div>
            </Link>;
          })}
        </section>
      </>}
    </div>
  );
}
