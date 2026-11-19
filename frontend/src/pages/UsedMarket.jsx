import { safeExternalUrl } from "../utils/site-config";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import api from "../api/client";
import GameSearch from "../components/GameSearch";

function formatPrice(price) {
  return price > 0 ? `${Number(price).toLocaleString("ko-KR")}원` : "가격 미공개";
}

export default function UsedMarket() {
  const [searchParams, setSearchParams] = useSearchParams();
  const keyword = searchParams.get("keyword") || "전체";
  const sort = searchParams.get("sort") === "price" ? "price" : "recent";
  const page = Math.max(1, Number.parseInt(searchParams.get("page"), 10) || 1);
  const [items, setItems] = useState([]);
  const [keywords, setKeywords] = useState(["전체"]);
  const [pageInfo, setPageInfo] = useState(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const update = (changes) => {
    const params = new URLSearchParams(searchParams);
    params.delete("source");
    params.delete("page");
    for (const [key, value] of Object.entries(changes)) {
      if (!value || value === "전체" || value === "all") params.delete(key);
      else params.set(key, value);
    }
    setSearchParams(params);
  };
  const search = (value) => update({ keyword: value });

  useEffect(() => {
    const controller = new AbortController();
    setIsLoading(true); setError("");
    api.get("/api/used-market", { params: { keyword, sort, page, size: 12 }, signal: controller.signal })
      .then(({ data }) => { if (!controller.signal.aborted) { setItems(data.items || []); setKeywords(data.keywords || ["전체"]); setPageInfo(data); } })
      .catch(() => { if (!controller.signal.aborted) setError("중고 거래 정보를 불러오지 못했습니다. 다시 시도해주세요."); })
      .finally(() => { if (!controller.signal.aborted) setIsLoading(false); });
    return () => controller.abort();
  }, [keyword, sort, page, reload]);

  return <div className="page used-market-page">
    <section className="category-header"><h1>중고 거래 둘러보기</h1><p>중고나라의 공개 게임 패키지 매물을 비교하세요. 최근 14일 안에 수집한 정보이며, 출처의 접속 제한에 따라 갱신이 지연될 수 있습니다.</p></section>
    <section className="software-search-panel">
      <GameSearch keyword={keyword === "전체" ? "" : keyword} searchLabel="중고 매물 검색" placeholder="매물 제목·키워드 검색 · 엘든링, 닌텐도" onSearch={search} />
      <div className="genre-button-wrap">{keywords.map((item) => <button key={item} className={keyword === item ? "active" : ""} aria-pressed={keyword === item} onClick={() => search(item)}>{item}</button>)}</div>
      <div className="market-filters">
        <label>정렬 <select value={sort} onChange={(event) => update({ sort: event.target.value })}><option value="recent">최저가 강조 · 최근 수집 순</option><option value="price">낮은 가격 순</option></select></label>
      </div>
      <p className="subscription-notice">최저가는 현재 검색 조건에 맞는 수집 매물 전체에서 계산합니다. 배송비는 제외하며, 플랫폼·에디션·상태·구성품 차이가 있으니 상품 내용을 확인하세요. 판매완료·예약·가격 협의·묶음 매물은 수집 시 제외합니다.</p>
      {!isLoading && !error && <div className="market-external-links">{pageInfo?.search_links?.map((link) => safeExternalUrl(link.url) && <a key={link.source} href={safeExternalUrl(link.url)} target="_blank" rel="noreferrer">{link.source}에서 직접 검색</a>)}</div>}
    </section>
    {isLoading ? <p className="loading-text">중고 매물을 불러오는 중입니다…</p> : error ? <section className="empty-panel" role="alert"><p>{error}</p><button className="secondary-button" onClick={() => setReload((value) => value + 1)}>다시 시도</button></section> : items.length === 0 ? <section className="empty-panel"><h2>수집된 매물이 없습니다.</h2><p>검색어를 바꾸거나 위의 공식 검색 링크에서 확인해보세요. 출처에서 수집을 제한하면 매물이 표시되지 않을 수 있습니다.</p><button className="secondary-button" onClick={() => update({ keyword: "" })}>전체 매물 보기</button></section> : <>
      {pageInfo?.lowest_price != null && <aside className="market-lowest-summary" aria-label="검색 최저가"><div><span>‘{keyword}’ 검색 최저가</span><strong>{formatPrice(pageInfo.lowest_price)}</strong></div><p>{pageInfo.lowest_count > 1 ? `동일 최저가 ${pageInfo.lowest_count}개` : "가장 저렴한 수집 매물"} · 전체 {pageInfo.total}개 검색 결과 기준 · 배송비 별도</p></aside>}
      <p className="subscription-count">{pageInfo?.total?.toLocaleString()}개 매물 · {sort === "price" ? "낮은 가격 순" : "검색 최저가 우선 · 최근 수집 순"}</p>
      <section className="used-market-grid">{items.map((item) => <article className={`used-market-card${item.is_lowest ? " is-lowest" : ""}`} key={item.id}>
        <div className="used-market-card-top"><span className="used-market-source">{item.source}</span>{item.is_lowest && <span className="market-lowest-badge">최저가</span>}<span className="used-market-keyword">{item.keyword}</span></div>
        <h2>{item.title}</h2><strong className="used-market-price">{formatPrice(item.price)}</strong>
        <div className="used-market-meta"><span>{item.status} · 수집 시점 기준</span>{item.location && <span>{item.location}</span>}{item.posted_at_text && <span>게시 당시: {item.posted_at_text}</span>}{item.collected_at && <span>수집: {new Date(item.collected_at).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}</span>}</div>
        {safeExternalUrl(item.item_url) && <a href={safeExternalUrl(item.item_url)} target="_blank" rel="noreferrer" className="used-market-link">매물 확인하기</a>}
      </article>)}</section>
      {pageInfo?.total_pages > 1 && <nav className="pagination" aria-label="중고 매물 페이지"><button disabled={pageInfo.page <= 1} onClick={() => update({ page: String(pageInfo.page - 1) })}>이전</button><span className="pagination-current">{pageInfo.page} / {pageInfo.total_pages}</span><button disabled={pageInfo.page >= pageInfo.total_pages} onClick={() => update({ page: String(pageInfo.page + 1) })}>다음</button></nav>}
    </>}
  </div>;
}
