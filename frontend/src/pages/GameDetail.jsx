import { safeExternalUrl } from "../utils/site-config";
import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { useAuth } from "../context/auth-context";
import PriceHistoryChart from "../components/PriceHistoryChart";
import api from "../api/client";
import GameSearch from "../components/GameSearch";
import { formatGameMoney } from "../utils/game-format";
import { formatCheckedAt } from "../utils/subscription-format";

const periods = [{ value: "1m", label: "1개월" }, { value: "3m", label: "3개월" }, { value: "6m", label: "6개월" }, { value: "1y", label: "1년" }, { value: "all", label: "수집한 전체" }];
export default function GameDetail() {
  const { gameId } = useParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const [game, setGame] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [wishlisted, setWishlisted] = useState(false);
  const [platform, setPlatform] = useState("최저가");
  const [period, setPeriod] = useState("1y");
  const [currency, setCurrency] = useState("");
  const [history, setHistory] = useState(null);
  const [historyLoading, setHistoryLoading] = useState(true);
  const [historyError, setHistoryError] = useState("");
  const [page, setPage] = useState(1);
  useEffect(() => {
    const controller = new AbortController();
    let pending = false;
    const refresh = async () => {
      if (pending || document.hidden) return;
      pending = true;
      try {
        const [detail, nextHistory] = await Promise.all([
          api.get(`/api/games/${gameId}`, { signal: controller.signal }),
          api.get(`/api/games/${gameId}/history`, { params: { platform, period, currency: currency || undefined }, signal: controller.signal }),
        ]);
        if (!controller.signal.aborted) { setGame(detail.data); setHistory(nextHistory.data); }
      } catch { /* 표시 중인 자료는 유지하고 다음 갱신에서 다시 확인합니다. */ }
      finally { pending = false; }
    };
    const timer = window.setInterval(refresh, 30000);
    window.addEventListener("focus", refresh);
    return () => { controller.abort(); window.clearInterval(timer); window.removeEventListener("focus", refresh); };
  }, [gameId, platform, period, currency]);
  useEffect(() => {
    const controller = new AbortController();
    const load = async () => {
      setLoading(true);
      setPlatform("최저가"); setCurrency(""); setPage(1);
      try { const response = await api.get(`/api/games/${gameId}`, { signal: controller.signal }); setGame(response.data); setError(""); }
      catch (e) { if (!controller.signal.aborted) setError(e.response?.data?.detail || "게임을 불러오지 못했습니다."); }
      finally { if (!controller.signal.aborted) setLoading(false); }
    };
    load();
    return () => controller.abort();
  }, [gameId]);
  useEffect(() => {
    const controller = new AbortController();
    let timer;
    let waiting = false;
    let failures = 0;
    const load = async () => {
      try {
        const response = await api.get(`/api/games/${gameId}/history`, { params: { platform, period, currency: currency || undefined }, signal: controller.signal });
        if (controller.signal.aborted) return;
        setHistory(response.data); setHistoryError(""); failures = 0;
        if (["queued", "running"].includes(response.data.history_status)) {
          waiting = true;
          timer = setTimeout(load, 3000);
        } else if (waiting && response.data.history_status === "synced") {
          waiting = false;
          const detail = await api.get(`/api/games/${gameId}`, { signal: controller.signal });
          if (!controller.signal.aborted) setGame(detail.data);
        }
      }
      catch {
        if (!controller.signal.aborted) {
          setHistoryError("가격 이력을 불러오지 못했습니다. 잠시 뒤 기간이나 구매처를 다시 선택해주세요.");
          if (waiting && failures++ < 3) timer = setTimeout(load, 8000);
        }
      }
      finally { if (!controller.signal.aborted) setHistoryLoading(false); }
    };
    const start = async () => {
      setHistoryLoading(true);
      try { await api.post(`/api/games/${gameId}/history/sync`, {}, { signal: controller.signal }); }
      catch { /* 외부 수집 연결 실패 시에도 이미 저장된 이력은 표시합니다. */ }
      if (!controller.signal.aborted) await load();
    };
    start();
    return () => { controller.abort(); clearTimeout(timer); };
  }, [gameId, platform, period, currency]);
  useEffect(() => {
    const controller = new AbortController();
    api.get(`/api/wishlist/check/${gameId}`, { signal: controller.signal }).then((r) => setWishlisted(Boolean(user && r.data.is_wishlisted))).catch(() => { if (!controller.signal.aborted) setWishlisted(false); });
    return () => controller.abort();
  }, [gameId, user]);
  const addWishlist = async () => {
    if (!user) { navigate("/login"); return; }
    if (wishlisted) return;
    const input = prompt("목표 가격(원)을 입력하세요. 비워두면 할인 알림 설정에서 나중에 지정할 수 있습니다.");
    if (input === null) return;
    const price = input.trim() ? Number(input) : null;
    if (price !== null && (!Number.isFinite(price) || price < 0)) { alert("목표 가격은 0 이상의 숫자여야 합니다."); return; }
    try { await api.post(`/api/wishlist/games/${game.id}`, { target_price: price, notify_enabled: true }); setWishlisted(true); }
    catch (e) { alert(e.response?.data?.detail || "위시리스트 저장에 실패했습니다."); }
  };
  if (loading) return <p className="loading-text">게임 정보를 불러오는 중입니다...</p>;
  if (error || !game) return <div className="page empty-panel" role="alert"><p>{error || "게임을 찾을 수 없습니다."}</p><Link to="/games">게임 목록으로</Link></div>;
  const prices = game.prices || [];
  const lowest = prices[0];
  const totalPages = Math.max(1, Math.ceil(prices.length / 8));
  const safePage = Math.min(page, totalPages);
  const visible = prices.slice((safePage - 1) * 8, safePage * 8);
  const stats = history?.stats;
  const chartCurrency = history?.currency || "KRW";
  const historyMessage = !history ? "가격 이력 상태를 확인하는 중입니다."
    : ["queued", "pending"].includes(history?.history_status)
    ? "ITAD 과거 가격 이력 보강 대기 중입니다. 완료되면 차트가 자동으로 갱신됩니다."
    : history?.history_status === "running" ? "과거 가격 변경 이력을 가져오는 중입니다. 완료되면 자동으로 표시합니다."
    : history?.history_status === "unconfigured" ? "외부 가격 이력 수집 연결이 설정되지 않았습니다. 관리자에게 연결 설정을 요청해주세요."
    : history?.history_status === "unlinked" ? "등록된 구매처 연결이 없어 과거 이력을 조회할 수 없습니다. 관리자에게 게임 연결 보강을 요청해주세요."
    : history?.history_status === "needs_check" ? history.history_error || "외부 가격 이력을 다시 확인해야 합니다."
    : history?.external_history_count > 0 ? `외부 이력 확인: ${formatCheckedAt(history.history_synced_at)} · 선택 구매처 이력 ${history.external_history_count}건`
    : history?.external_history_total > 0 ? "이 구매처에는 확인된 과거 이력이 없습니다. 다른 구매처를 선택해주세요."
    : "ITAD가 제공한 과거 이력이 없습니다. 확인된 가격 관측만 표시합니다.";
  const prediction = game.prediction || {};
  return <div className="page detail-page">
    <GameSearch compact onSearch={(keyword) => navigate(keyword ? `/games?keyword=${encodeURIComponent(keyword)}` : "/games")} />
    <div className="breadcrumb"><Link to="/">홈</Link><span>&gt;</span><Link to="/games">게임</Link><span>&gt;</span><strong>{game.title}</strong></div>
    {game.active_price_tests?.length > 0 && <aside className="test-price-notice" role="status"><strong>테스트용 가격 적용 중</strong><p>{game.active_price_tests.map((row) => `${row.platform}: ${formatGameMoney(row.discounted_price, "KRW")} · ${formatCheckedAt(row.expires_at)} 자동 해제`).join(" / ")}</p><p>관리자가 알림 확인을 위해 설정한 가격입니다. 실제 판매 가격은 구매처에서 확인하세요.</p></aside>}
    <section className="detail-hero"><div className="cover detail-cover" style={{ backgroundColor: game.image_color, backgroundImage: game.header_image ? `url(${game.header_image})` : undefined, backgroundSize: "cover", backgroundPosition: "center" }}>{!game.header_image && <span>{game.title}</span>}</div>
      <div className="detail-main-info"><h1>{game.title}</h1><p>{[game.developer, game.publisher].filter(Boolean).join(" · ")}</p>
        <div className="lowest-price-box"><span>{lowest?.is_test_price ? "현재 표시 가격 (테스트용)" : lowest ? lowest.currency === "KRW" ? "현재 최저가" : `현재 확인 가격 (${lowest.currency} 기준)` : "가격 확인 대기"}</span><strong>{formatGameMoney(lowest?.discounted_price, lowest?.currency)}</strong><p>{lowest ? `${lowest.platform} · ${lowest.discount_rate > 0 ? `${lowest.discount_rate}% 할인` : "현재 정상가"}` : "인기 게임 카탈로그에 등록되었습니다. 다음 갱신에서 구매처 가격과 이력을 확인합니다."}</p></div>
        <div className="detail-button-row">{safeExternalUrl(lowest?.url || game.store_url) && <a href={safeExternalUrl(lowest?.url || game.store_url)} className="primary-button" target="_blank" rel="noreferrer">{lowest ? "구매처에서 확인" : "공식 게임 페이지"} ↗</a>}<button className="secondary-button" onClick={addWishlist}>{wishlisted ? "✓ 위시리스트 저장됨" : "+ 위시리스트"}</button></div>
        <div className="tag-wrap">{(game.tags || []).map((tag) => <span key={tag}>{tag}</span>)}</div>
        <div className="meta-grid"><p><strong>출시일</strong>{game.release_date || "확인 중"}</p><p><strong>평가</strong>{game.rating || "평가 정보 확인 중"} · {(game.review_count || 0).toLocaleString()}개</p><p><strong>한국어 지원</strong>{game.korean_support || "공식 페이지 확인"}</p></div>
        {game.catalog_source && <p className="subscription-notice">카탈로그 출처: {game.catalog_source}</p>}
      </div>
    </section>
    <section className="detail-grid">
      <article className="panel price-compare-panel"><h2>구매처별 현재 가격</h2><p className="subscription-notice">{prices.length}개 구매처 · 원화 가격을 먼저 비교합니다. 외화 금액은 해당 통화로 표시합니다.</p>
        {prices.length === 0 && <p>아직 확인된 현재 가격이 없습니다. 게임 목록은 유지되며 자동 갱신 때 다시 확인합니다.</p>}
        <div className="price-table">{visible.map((price) => <div className="price-table-row" key={`${price.platform}-${price.currency}`}><div><strong>{price.platform}</strong>{price.is_test_price && <span className="test-price-badge">테스트용</span>}<span>{price.discount_rate > 0 ? `-${price.discount_rate}%` : "정상가"} · {price.currency}</span></div><div><strong>{formatGameMoney(price.discounted_price, price.currency)}</strong>{price.discount_rate > 0 && <del>{formatGameMoney(price.original_price, price.currency)}</del>}<small>{formatCheckedAt(price.collected_at)} {price.is_test_price ? "테스트 설정" : "확인"}</small></div>{safeExternalUrl(price.url) && <a href={safeExternalUrl(price.url)} target="_blank" rel="noreferrer">구매처 ↗</a>}</div>)}</div>
        {totalPages > 1 && <div className="pagination"><button disabled={safePage === 1} onClick={() => setPage(safePage - 1)}>이전</button><span>{safePage}/{totalPages}</span><button disabled={safePage === totalPages} onClick={() => setPage(safePage + 1)}>다음</button></div>}
      </article>
      <article className="panel prediction-panel"><h2>할인 예측</h2><div className="prediction-grid"><div><span>다음 할인 예상 시점</span><strong>{prediction.expected_date || "이력 수집 대기"}</strong><p>{prediction.expected_event}</p></div><div><span>예상 할인율</span><strong>{prediction.expected_discount_rate || "분석 대기"}</strong><p>신뢰도: {prediction.confidence || "데이터 부족"}</p></div></div><p className="prediction-reason">{prediction.reason}</p>
        {prediction.model_version && <details className="prediction-method"><summary>예측 근거와 오차 확인</summary><dl><div><dt>분석 자료</dt><dd>할인 {prediction.evidence?.event_count ?? 0}회 · 일별 관측 {prediction.evidence?.snapshot_count ?? 0}개</dd></div><div><dt>최근 가중 할인 주기</dt><dd>{prediction.evidence?.weighted_cycle_days != null ? `약 ${prediction.evidence.weighted_cycle_days}일` : "자료 부족"}</dd></div><div><dt>과거 시점 검증</dt><dd>{prediction.backtest?.samples > 0 ? `${prediction.backtest.samples}회 · 평균 날짜 오차 ${prediction.backtest.mae_days}일` : "검증할 과거 이벤트 부족"}</dd></div><div><dt>자료 참고 점수</dt><dd>{prediction.confidence_score ?? 0} / 100 · {prediction.confidence_note}</dd></div></dl><p>최근 주기 가중치, 이상 간격 완화, 월별 할인 이력을 사용한 통계 모형입니다. 예상 범위는 모형 분포의 중앙 60%이며 실제 적중률을 보장하지 않습니다. 최신 이력이 30일 이상 없으면 예측을 보류합니다.</p></details>}
      </article>
      <article className="panel history-panel"><div className="section-title-row"><div><h2>가격 히스토리 {history?.history_platform && `(${history.history_platform})`}</h2><p className="subscription-notice">ITAD의 실제 가격 변경 이력과 직접 수집한 관측값입니다. 구매처와 통화를 각각 선택하세요.</p></div>
        <div className="history-controls"><select aria-label="히스토리 구매처" value={platform} onChange={(e) => { setPlatform(e.target.value); setCurrency(""); }}><option value="최저가">현재 최저가 구매처</option>{(history?.available_platforms || game.available_platforms || []).map((name) => <option key={name}>{name}</option>)}</select>
          {(history?.available_currencies?.length || 0) > 1 && <select aria-label="히스토리 통화" value={currency || chartCurrency} onChange={(e) => setCurrency(e.target.value)}>{history.available_currencies.map((value) => <option key={value}>{value}</option>)}</select>}
          <div className="period-buttons">{periods.map((item) => <button key={item.value} className={period === item.value ? "active" : ""} onClick={() => setPeriod(item.value)}>{item.label}</button>)}</div></div>
      </div>
        <p className="subscription-notice" aria-live="polite">{historyMessage}</p>
        <p className="admin-help-text">{history?.price_filter_notice}</p>
        <div className="chart-box">{historyLoading ? <div className="empty-chart-box">가격 이력을 불러오는 중...</div> : historyError ? <div className="empty-chart-box" role="alert">{historyError}</div> : <PriceHistoryChart history={history || {}} />}</div>
        {stats && <div className="stats-grid"><p><span>분석 기간</span><strong>{periods.find((item) => item.value === stats.period)?.label || stats.period}</strong></p><p><span>선택 기간 최저가</span><strong>{formatGameMoney(stats.lowest_price, chartCurrency)}</strong></p><p><span>할인 시작 횟수</span><strong>{stats.discount_count}회</strong></p><p><span>평균 최대 할인율</span><strong>{stats.average_discount_rate}%</strong></p></div>}
      </article>
    </section>
  </div>;
}
