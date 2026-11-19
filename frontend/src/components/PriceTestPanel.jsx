import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { apiError } from "../api/client";
import { formatCheckedAt } from "../utils/subscription-format";
import { formatGameMoney } from "../utils/game-format";

export default function PriceTestPanel() {
  const [data, setData] = useState({ games: [], items: [] });
  const [gameId, setGameId] = useState("");
  const [query, setQuery] = useState("");
  const [platform, setPlatform] = useState("");
  const [price, setPrice] = useState("");
  const [recipient, setRecipient] = useState("");
  const [sendEmail, setSendEmail] = useState(false);
  const [minutes, setMinutes] = useState("60");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const load = useCallback(async () => {
    try {
      const response = await api.get("/api/admin/price-tests");
      setData(response.data); setError("");
      setGameId((current) => current || String(response.data.games[0]?.id || ""));
    } catch (e) { setError(apiError(e, "가격 테스트 정보를 불러오지 못했습니다.")); }
  }, []);
  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    const timer = window.setInterval(load, 30000);
    return () => window.clearInterval(timer);
  }, [load]);
  const game = data.games.find((row) => String(row.id) === gameId);
  const selectedPlatform = game?.platforms.find((row) => row.name === platform) || game?.platforms[0];
  const filtered = data.games.filter((row) => String(row.id) === gameId || row.title.toLowerCase().includes(query.toLowerCase()));
  const active = data.items.filter((row) => row.active);
  const apply = async (event) => {
    event.preventDefault();
    const amount = Number(price);
    if (!Number.isSafeInteger(amount) || amount < 0 || amount > 2147483647) { setError("테스트 가격은 0 이상의 정수로 입력해 주세요."); return; }
    setBusy(true); setError("");
    try {
      const response = await api.post("/api/admin/price-tests", {
        game_id: Number(gameId), platform: selectedPlatform.name, discounted_price: amount,
        duration_minutes: Number(minutes), recipient_email: recipient.trim() || null, send_email: sendEmail,
      });
      setResult(response.data); await load();
      window.dispatchEvent(new Event("gamesoft:notifications-updated"));
    } catch (e) { setError(apiError(e, "테스트 가격 적용에 실패했습니다.")); }
    finally { setBusy(false); }
  };
  const stop = async (id) => {
    setBusy(true); setError("");
    try {
      const response = await api.delete(`/api/admin/price-tests/${id}`);
      setResult({ message: response.data.message }); await load();
    } catch (e) { setError(apiError(e, "테스트 해제에 실패했습니다.")); }
    finally { setBusy(false); }
  };
  return <section className="panel price-test-panel" aria-labelledby="price-test-heading">
    <div className="section-title-row"><div><p className="section-label">관리자 테스트</p><h2 id="price-test-heading">찜 알림 · 가격 테스트</h2></div><span className="test-price-badge">테스트용</span></div>
    <p>선택한 구매처 가격을 잠시 변경해 찜 알림을 확인합니다. 상세·찜·게임 카드에 테스트용으로 표시하고, 그래프에는 주황색 점으로 남깁니다. 실제 최저가 통계와 예측에는 포함하지 않습니다.</p>
    <p className="admin-help-text">먼저 알림을 받을 계정에서 게임을 찜하고 목표가·알림 받기를 저장하세요. 테스트 알림은 지정한 계정 한 명에게만 전송합니다.</p>
    <form className="price-test-form" onSubmit={apply}>
      <label>게임 이름으로 좁히기<input type="search" value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Baba Is You" /></label>
      <label>테스트할 게임<select value={gameId} onChange={(e) => { setGameId(e.target.value); setPlatform(""); }} required><option value="">게임 선택</option>{filtered.map((row) => <option key={row.id} value={row.id}>{row.title}</option>)}</select></label>
      <label>테스트 구매처<select value={selectedPlatform?.name || ""} onChange={(e) => setPlatform(e.target.value)} required><option value="">구매처 선택</option>{game?.platforms.map((row) => <option key={row.name} value={row.name}>{row.name} · 실제 {formatGameMoney(row.discounted_price, "KRW")}</option>)}</select></label>
      <label>테스트 가격 (원)<input type="number" min="0" max="2147483647" step="1" value={price} onChange={(e) => setPrice(e.target.value)} placeholder="예: 10000" required /></label>
      <label>자동 해제<select value={minutes} onChange={(e) => setMinutes(e.target.value)}><option value="15">15분 후</option><option value="30">30분 후</option><option value="60">1시간 후</option><option value="120">2시간 후</option></select></label>
      <label>알림 받을 테스트 계정 이메일<input type="email" value={recipient} onChange={(e) => setRecipient(e.target.value)} placeholder="비워두면 현재 관리자 본인" maxLength={255} /></label>
      <label className="checkbox-label"><input type="checkbox" checked={sendEmail} onChange={(e) => setSendEmail(e.target.checked)} />테스트 이메일도 보내기 (해당 계정 한 명)</label>
      <button disabled={busy || !selectedPlatform || price === ""}>{busy ? "처리 중…" : "테스트 가격 적용 · 알림 확인"}</button>
    </form>
    {error && <p className="form-error" role="alert">{error}</p>}
    {result && <div className="price-test-result" role="status"><strong>{result.message}</strong>{result.notifications_created != null && <p>사이트 알림 {result.notifications_created}개 · 이메일 {result.emails_sent}개 · 메일 오류 {result.email_errors}개 · 대상: {result.recipient_nickname}</p>}<p>{result.notification_reason}</p>{result.test && <Link to={`/games/${result.test.game_id}`}>게임 상세에서 테스트 가격 확인</Link>}</div>}
    {active.length > 0 && <div className="active-price-tests"><h3>적용 중인 테스트</h3>{active.map((row) => <div className="active-price-test" key={row.id}><div><Link to={`/games/${row.game_id}`}>{row.game_title}</Link><p>{row.platform} · {formatGameMoney(row.discounted_price, row.currency)} <span className="test-price-badge">테스트용</span></p><small>{formatCheckedAt(row.expires_at)} 자동 해제</small></div><button type="button" className="secondary-button" disabled={busy} onClick={() => stop(row.id)}>테스트 해제 · 실제 가격으로</button></div>)}</div>}
    <p className="admin-help-text">해제하거나 시간이 지나면 그동안 수집한 최신 실제 가격으로 돌아갑니다. 그래프의 테스트 기록은 ‘테스트용’ 표시로 남습니다.</p>
  </section>;
}
