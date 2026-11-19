import { useState } from "react";
import { Link } from "react-router-dom";
import api from "../api/client";

export default function RequestedGameCollector({ onCollected }) {
  const [query, setQuery] = useState("");
  const [candidates, setCandidates] = useState([]);
  const [searched, setSearched] = useState(false);
  const [searching, setSearching] = useState(false);
  const [importing, setImporting] = useState("");
  const [error, setError] = useState("");
  const [warnings, setWarnings] = useState([]);
  const [result, setResult] = useState(null);

  const find = async (event) => {
    event.preventDefault();
    if (!query.trim()) return;
    setSearching(true); setError(""); setWarnings([]); setResult(null); setSearched(false); setCandidates([]);
    try {
      const response = await api.post("/api/admin/games/find", { query: query.trim() }, { timeout: 90000 });
      setCandidates(response.data.candidates || []); setSearched(true);
      const notes = (response.data.warnings || []).map((item) => item.message);
      if (!response.data.itad_available) notes.push("ITAD 연결 설정이 없어 Steam에서 검색합니다. 외부 구매처와 과거 이력에는 ITAD 연결이 필요합니다.");
      setWarnings(notes);
    } catch (error) {
      setCandidates([]); setError(error.response?.data?.detail || "게임을 찾지 못했습니다. 다시 시도해주세요.");
    } finally { setSearching(false); }
  };

  const collect = async (candidate) => {
    const key = String(candidate.steam_appid || candidate.itad_id);
    setImporting(key); setError(""); setResult(null);
    try {
      const response = await api.post("/api/admin/games/import", { steam_appid: candidate.steam_appid || null, itad_id: candidate.itad_id || null }, { timeout: 180000 });
      setResult(response.data);
      setWarnings((response.data.result.warnings || []).map((item) => item.message));
      setCandidates((items) => items.map((item) => String(item.steam_appid || item.itad_id) === key ? { ...item, existing_game_id: response.data.result.game_id } : item));
      await onCollected?.();
    } catch (error) { setError(error.response?.data?.detail || "게임 수집에 실패했습니다. 다시 시도해주세요."); }
    finally { setImporting(""); }
  };

  return <section className="panel requested-game-panel">
    <div className="section-title-row"><div><span className="section-label">직접 선택</span><h2>원하는 게임 추가</h2><p className="admin-help-text">게임 이름으로 찾고 수집할 본편을 선택하세요. 할인하지 않는 게임도 등록할 수 있습니다.</p></div></div>
    <form className="requested-game-search" onSubmit={find} role="search" aria-label="관리자 게임 지정 검색">
      <label htmlFor="requested-game-query">게임 이름 · Steam 링크 · 앱 ID</label>
      <div className="main-search-box"><input id="requested-game-query" value={query} onChange={(event) => setQuery(event.target.value)} placeholder="예: Baba Is You 또는 Steam 상점 링크" maxLength={200} required disabled={Boolean(importing)} /><button type="submit" disabled={searching || Boolean(importing)}>{searching ? "검색 중…" : "게임 찾기"}</button></div>
    </form>
    {error && <p className="inline-error" role="alert">{error}</p>}
    {warnings.length > 0 && <div className="subscription-notice">{warnings.map((note) => <p key={note}>{note}</p>)}</div>}
    {searched && candidates.length === 0 && <p className="subscription-notice">{warnings.length > 0
      ? "검색 출처에 연결하지 못해 결과를 확인하지 못했습니다. 표시된 대기 시간이 지난 뒤 다시 검색해 주세요."
      : "일치하는 본편을 찾지 못했습니다. Steam 상점 링크나 앱 ID로 다시 검색해보세요."}</p>}
    <div className="requested-game-results">{candidates.map((candidate) => {
      const key = String(candidate.steam_appid || candidate.itad_id);
      return <article className="requested-game-result" key={key}>
        {candidate.image ? <img src={candidate.image} alt="" /> : <div className="requested-game-image">게임</div>}
        <div><h3>{candidate.title}</h3><p>{candidate.sources.join(" · ")}{candidate.steam_appid && ` · Steam ${candidate.steam_appid}`}</p>{candidate.existing_game_id && <span className="catalog-status">이미 등록됨 · 최신 가격 갱신 가능</span>}</div>
        <button className="primary-button" type="button" disabled={Boolean(importing)} onClick={() => collect(candidate)}>{importing === key ? "가격 수집 중…" : candidate.existing_game_id ? "가격·이력 갱신" : "가격·이력 수집"}</button>
      </article>;
    })}</div>
    {result && <div className="requested-game-success" role="status"><div><strong>{result.message}</strong><p>{result.result.history_queued ? "과거 이력은 우선 보강 중이며 상세 차트에 자동 반영됩니다." : "현재 가격과 수집 상태를 상세 화면에서 확인하세요."}</p></div><Link className="secondary-button" to={`/games/${result.result.game_id}`}>게임 상세 보기 ↗</Link></div>}
  </section>;
}
