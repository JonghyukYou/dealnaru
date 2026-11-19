import { useCallback, useEffect, useRef, useState } from "react";
import api, { apiError } from "../api/client";
import { Link } from "react-router-dom";
import PriceTestPanel from "../components/PriceTestPanel";
import RequestedGameCollector from "../components/RequestedGameCollector";
import LogPagination from "../components/LogPagination";

const COLLECTION_TIMEOUT_MS = 180000;
const LOG_LABELS = { SUCCESS: "완료", FAIL: "실패", PARTIAL: "일부 완료", RUNNING: "진행 중", WAITING: "요청 대기", BLOCKED: "접근 제한", WARNING: "요청 확인", SKIPPED: "건너뜀" };

function collectionResultMessage(data) {
  const result = data.result || {};
  const reasons = Object.entries(result.skip_reasons || {}).map(([reason, count]) => `${reason}: ${count}개`).join("\n");
  return [data.message, result.scanned_count !== undefined ? `검사 ${result.scanned_count}개 · 저장 ${result.imported_count || 0}개 · 제외 ${result.skipped_count || 0}개 · 오류 ${result.error_count || 0}개` : "", reasons, ...(result.source_warnings || [])].filter(Boolean).join("\n");
}

function formatKoreanDateTime(value) {
  if (!value) {
    return "-";
  }

  return new Date(value).toLocaleString("ko-KR", {
    timeZone: "Asia/Seoul",
    year: "numeric",
    month: "numeric",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  });
}

function AdminCollect() {
  const [logs, setLogs] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [schedule, setSchedule] = useState(null);
  const [scheduleForm, setScheduleForm] = useState(null);
  const [isSavingSchedule, setIsSavingSchedule] = useState(false);
  const [isRefreshingSoftware, setIsRefreshingSoftware] = useState(false);
  const [scheduleError, setScheduleError] = useState("");

  const [steamImportCount, setSteamImportCount] = useState(30);
  const [isSteamImporting, setIsSteamImporting] = useState(false);

  const [storeImportCount, setStoreImportCount] = useState(20);
  const [itadShopId, setItadShopId] = useState("35");
  const [isStoreImporting, setIsStoreImporting] = useState(false);

  const [usedMarketLimit, setUsedMarketLimit] = useState(5);
  const [usedMarketKeywords, setUsedMarketKeywords] = useState("게임CD, 닌텐도, PS5, PS4");
  const [usedMarketResult, setUsedMarketResult] = useState(null);
  const [isUsedMarketLoading, setIsUsedMarketLoading] = useState(false);

  const [logPage, setLogPage] = useState(1);
  const [logPageSize, setLogPageSize] = useState(10);
  const [logView, setLogView] = useState("summary");
  const [logFilter, setLogFilter] = useState("all");
  const [logTotalPages, setLogTotalPages] = useState(1);
  const [logTotal, setLogTotal] = useState(0);
  const latestRequest = useRef(0);

  const fetchAdminData = useCallback(
    async ({ showLoading = false, showAlert = true, page = logPage } = {}) => {
      const requestId = ++latestRequest.current;
      if (showLoading) {
        setIsLoading(true);
      }

      try {
        const [logsResult, scheduleResult] = await Promise.allSettled([
          api.get("/api/admin/crawling-logs", {
            params: { page, page_size: logPageSize, view: logView, filter: logFilter },
          }),
          api.get("/api/admin/auto-refresh"),
        ]);

        if (requestId !== latestRequest.current) return;
        if (logsResult.status === "fulfilled") {
          const logData = logsResult.value.data;
          setLogs(logData.items || []);
          setLogTotal(logData.total || 0);
          setLogTotalPages(logData.total_pages || 1);
          setLogPage(logData.page || 1);
        } else {
          console.error("logs error:", logsResult.reason);
          setLogs([]);
          setLogTotal(0);
          setLogTotalPages(1);
        }

        if (scheduleResult.status === "fulfilled") {
          setSchedule(scheduleResult.value.data);
          setScheduleForm((current) => current || scheduleResult.value.data);
          setScheduleError("");
        } else {
          setScheduleError("자동 갱신 상태를 불러오지 못했습니다.");
        }
        const results = [logsResult, scheduleResult];
        const statusCodes = results
          .filter((result) => result.status === "rejected")
          .map((result) => result.reason?.response?.status);

        if (showAlert && statusCodes.includes(401)) {
          alert("로그인이 만료되었습니다. 다시 로그인해주세요.");
        } else if (showAlert && statusCodes.includes(403)) {
          alert("관리자 권한이 필요합니다.");
        }
      } catch (error) {
        console.error("admin fetch unexpected error:", error);

        if (showAlert) {
          alert("관리자 데이터를 불러오는 중 오류가 발생했습니다.");
        }
      } finally {
        if (requestId === latestRequest.current) setIsLoading(false);
      }
    },
    [logPage, logPageSize, logView, logFilter]
  );

  const refreshAfterAction = async () => {
    setLogPage(1);
    await fetchAdminData({ showAlert: false, page: 1 });
  };

  const handleImportSteam = async () => {
    try {
      setIsSteamImporting(true);
      const response = await api.post(
        "/api/admin/steam/import-featured",
        {
          max_items: Number(steamImportCount),
          country: "kr",
          language: "korean",
        },
        { timeout: COLLECTION_TIMEOUT_MS }
      );

      alert(collectionResultMessage(response.data));
      await refreshAfterAction();
    } catch (error) {
      alert(error.response?.data?.detail || "Steam 수집에 실패했습니다.");
    } finally {
      setIsSteamImporting(false);
    }
  };

  const handleImportStoreDeals = async () => {
    try {
      setIsStoreImporting(true);
      const response = await api.post(
        "/api/admin/itad/import-deals",
        {
          limit: Number(storeImportCount),
          offset: 0,
          country: "KR",
          sort: "-cut",
          shop_ids: itadShopId ? [Number(itadShopId)] : [],
        },
        { timeout: COLLECTION_TIMEOUT_MS }
      );

      alert(collectionResultMessage(response.data));
      await refreshAfterAction();
    } catch (error) {
      alert(
        error.response?.data?.detail ||
          "구매처별 할인 수집에 실패했습니다. ITAD API 키를 확인해주세요."
      );
    } finally {
      setIsStoreImporting(false);
    }
  };

  const handleCollectUsedMarket = async () => {
    try {
      setIsUsedMarketLoading(true);
      const response = await api.post(
        "/api/admin/used-market/collect",
        {
          limit_per_keyword: Number(usedMarketLimit),
          keywords: usedMarketKeywords.split(",").map((value) => value.trim()).filter(Boolean),
        },
        { timeout: COLLECTION_TIMEOUT_MS }
      );

      setUsedMarketResult(response.data.result);
      alert(response.data.message);
      await refreshAfterAction();
    } catch (error) {
      alert(apiError(error, "중고마켓 수집에 실패했습니다. 검색어는 최대 10개, 키워드당 개수는 1~20개로 확인해 주세요."));
    } finally {
      setIsUsedMarketLoading(false);
    }
  };

  useEffect(() => {
    fetchAdminData();
  }, [fetchAdminData]);

  useEffect(() => {
    const timer = window.setInterval(() => { fetchAdminData({ showAlert: false }); }, 10000);
    return () => window.clearInterval(timer);
  }, [fetchAdminData]);

  const handleSaveSchedule = async (event) => {
    event.preventDefault();
    if (!scheduleForm) return;
    setIsSavingSchedule(true);
    try {
      const response = await api.put("/api/admin/auto-refresh", {
        enabled: scheduleForm.enabled, interval_minutes: Number(scheduleForm.interval_minutes),
        include_software: scheduleForm.include_software, new_games_per_source: Number(scheduleForm.new_games_per_source),
      });
      setSchedule(response.data); setScheduleForm(response.data);
      alert("자동 갱신 설정을 저장했습니다.");
    } catch (error) { alert(typeof error.response?.data?.detail === "string" ? error.response.data.detail : "갱신 설정을 저장하지 못했습니다."); }
    finally { setIsSavingSchedule(false); }
  };

  const handleRunNow = async () => {
    try {
      const response = await api.post("/api/admin/auto-refresh/run");
      setSchedule(response.data.status); alert(response.data.message);
    } catch (error) { alert(error.response?.data?.detail || "갱신 요청에 실패했습니다."); }
  };

  const handleCatalogSync = async () => {
    try {
      const response = await api.post("/api/admin/catalog/sync");
      setSchedule(response.data.status); alert(response.data.message);
    } catch (error) { alert(error.response?.data?.detail || "카탈로그 보강 요청에 실패했습니다."); }
  };

  const handleCleanupCatalog = async () => {
    try {
      const response = await api.post("/api/admin/catalog/cleanup");
      alert(`번들·비게임 ${response.data.removed_count}개를 정리했습니다.`);
      await refreshAfterAction();
    } catch (error) { alert(error.response?.data?.detail || "비게임 정리에 실패했습니다."); }
  };

  const handleHistorySync = async () => {
    try {
      const response = await api.post("/api/admin/history/sync");
      setSchedule(response.data.status); alert(response.data.message);
      await refreshAfterAction();
    } catch (error) { alert(error.response?.data?.detail || "과거 이력 보강 요청에 실패했습니다."); }
  };

  const handleRefreshSoftware = async () => {
    setIsRefreshingSoftware(true);
    try {
      const response = await api.post("/api/admin/software/refresh", {}, { timeout: COLLECTION_TIMEOUT_MS });
      alert(response.data.message); await refreshAfterAction();
    } catch (error) { alert(error.response?.data?.detail || "공식 혜택을 확인하지 못했습니다."); }
    finally { setIsRefreshingSoftware(false); }
  };

  if (isLoading) {
    return <p className="loading-text">관리 페이지를 불러오는 중입니다...</p>;
  }

  return (
    <div className="page">
      <section className="category-header">
        <h1>수집 관리</h1>
        <p>게임과 프로그램·AI 정보를 주기적으로 갱신하고 결과를 확인합니다.</p>
      </section>

      <RequestedGameCollector onCollected={refreshAfterAction} />

      <section className="panel auto-refresh-panel">
        <div className="section-title-row"><div><h2>자동 갱신</h2><p className="admin-help-text">서버가 켜져 있는 동안 실행됩니다. 설정은 서버 재시작 후에도 유지됩니다.</p></div>
          <span className={`offer-status ${schedule?.is_collecting ? "information" : schedule?.enabled ? "active" : "reference"}`}>{schedule?.message || "상태 확인 중"}</span></div>
        {scheduleError && <p role="alert" className="subscription-notice">{scheduleError}</p>}
        {schedule?.source_states?.length > 0 && <div className="source-wait-notices" role="status">
          {schedule.source_states.map((source) => <p key={source.source}>
            <strong>{source.source}: {LOG_LABELS[source.status] || source.status}</strong>
            {source.http_status && ` · HTTP ${source.http_status}`} · 재확인 {formatKoreanDateTime(source.retry_at)}
          </p>)}
          <small>원본의 요청 제한 동안 기존 가격을 유지하며, 다른 출처는 계속 갱신합니다.</small>
        </div>}
        {scheduleForm && <form className="auto-refresh-form" onSubmit={handleSaveSchedule}>
          <label className="subscription-check"><input type="checkbox" checked={scheduleForm.enabled} onChange={(event) => setScheduleForm({ ...scheduleForm, enabled: event.target.checked })} /> 자동 갱신 켜기</label>
          <label>주기 (분)<input type="number" min="30" max="10080" step="1" value={scheduleForm.interval_minutes} onChange={(event) => setScheduleForm({ ...scheduleForm, interval_minutes: event.target.value })} /></label>
          <label>출처별 신규 수집 목표<input type="number" min="1" max="100" value={scheduleForm.new_games_per_source} onChange={(event) => setScheduleForm({ ...scheduleForm, new_games_per_source: event.target.value })} /></label>
          <label className="subscription-check"><input type="checkbox" checked={scheduleForm.include_software} onChange={(event) => setScheduleForm({ ...scheduleForm, include_software: event.target.checked })} /> 프로그램·AI 함께 갱신</label>
          <button disabled={isSavingSchedule}>{isSavingSchedule ? "저장 중..." : "설정 저장"}</button>
          <button type="button" className="secondary-button" disabled={schedule?.is_collecting || schedule?.run_requested || !schedule?.worker_active} onClick={handleRunNow}>지금 갱신</button>
        </form>}
        <div className="auto-refresh-times"><p>다음 갱신 <strong>{formatKoreanDateTime(schedule?.next_run_at)}</strong></p><p>최근 완료 <strong>{formatKoreanDateTime(schedule?.last_finished_at)}</strong></p><p>최근 결과 <strong>{LOG_LABELS[schedule?.last_status] || schedule?.last_status || "-"}</strong></p></div>
        <div className="auto-refresh-times"><p>전체 게임 <strong>{schedule?.catalog_game_count || 0}개</strong></p><p>ITAD 연결 <strong>{schedule?.itad_linked_count || 0}개</strong></p><p>최초 이력 대기 <strong>{schedule?.history_pending_count || 0}개</strong></p><p>이력 재확인 <strong>{schedule?.history_failed_count || 0}개</strong></p></div>
        <div className="auto-refresh-times"><p>이력 수집 대기 <strong>{schedule?.history_queued_count || 0}개</strong></p><p>이력 수집 중 <strong>{schedule?.history_running_count || 0}개</strong></p></div>
        {schedule?.last_result?.steps && <div className="auto-refresh-results">{schedule.last_result.steps.map((step, index) => <span key={`${step.source}-${index}`} className={`offer-status ${step.status === "SUCCESS" ? "active" : "needs_check"}`}>{step.source}: {LOG_LABELS[step.status] || step.status}{step.updated_count !== undefined && ` · ${step.updated_count}개`}{step.deferred_steps > 0 && ` · 나머지 ${step.deferred_steps}단계 대기`}{step.message && ` · ${step.message}`}</span>)}</div>}
      </section>

      <section className="panel collection-console">
        <div className="section-title-row collection-console-title">
          <div>
            <h2>데이터 수집</h2>
            <p className="admin-help-text">
              유효한 유료 게임을 목표 개수까지 검사합니다. 제외 사유는 실행 결과에서 확인할 수 있습니다.
            </p>
          </div>
        </div>

        <div className="collection-row">
          <div className="collection-row-heading"><strong>인기 게임·가격 이력</strong><span>Steam·Epic 본편, 정상가 포함 · 모든 구매처 현재가 · ITAD 과거 이력</span></div>
          <button onClick={handleCatalogSync} disabled={schedule?.is_collecting || schedule?.run_requested || !schedule?.worker_active}>인기 게임·가격 이력 보강</button>
          <button className="secondary-button" onClick={handleHistorySync} disabled={!schedule?.history_worker_active}>과거 가격 이력 우선 보강</button>
          <button className="secondary-button" onClick={handleCleanupCatalog} disabled={schedule?.is_collecting}>번들·비게임 정리</button>
        </div>
        <p className="admin-help-text">최초 이력 보강은 전체 갱신과 별도로 실행됩니다. 주요 공식 스토어 외 90% 초과 할인은 현재가·이력·알림에서 제외합니다.</p>

        <div className="collection-row">
          <div className="collection-row-heading">
            <strong>Steam</strong>
            <span>인기·할인 게임과 현재 가격</span>
          </div>
          <label>
            수집 개수
            <input
              type="number"
              min="1"
              max="100"
              value={steamImportCount}
              onChange={(event) => setSteamImportCount(event.target.value)}
            />
          </label>
          <button onClick={handleImportSteam} disabled={isSteamImporting || schedule?.is_collecting || schedule?.run_requested || schedule?.source_states?.some((source) => source.source === "Steam")}>
            {isSteamImporting ? "Steam 수집 중..." : "Steam 수집"}
          </button>
        </div>

        <div className="collection-row">
          <div className="collection-row-heading">
            <strong>그 외 구매처</strong>
            <span>ITAD를 통한 구매처별 할인</span>
          </div>
          <label>
            구매처
            <select
              value={itadShopId}
              onChange={(event) => setItadShopId(event.target.value)}
            >
              <option value="">전체 구매처</option>
              <option value="61">Steam</option>
              <option value="16">Epic Games Store</option>
              <option value="35">GOG</option>
              <option value="6">Fanatical</option>
              <option value="36">Green Man Gaming</option>
              <option value="37">Humble Store</option>
            </select>
          </label>
          <label>
            수집 개수
            <input
              type="number"
              min="1"
              max="200"
              value={storeImportCount}
              onChange={(event) => setStoreImportCount(event.target.value)}
            />
          </label>
          <button onClick={handleImportStoreDeals} disabled={isStoreImporting || schedule?.is_collecting || schedule?.run_requested || schedule?.source_states?.some((source) => source.source === "ITAD")}>
            {isStoreImporting ? "구매처 수집 중..." : "선택 구매처 수집"}
          </button>
        </div>

        <div className="collection-row">
          <div className="collection-row-heading"><strong>프로그램·AI</strong><span>제품 로고·정식 요금제·공식 할인·교육 혜택 확인</span></div>
          <button onClick={handleRefreshSoftware} disabled={isRefreshingSoftware || schedule?.is_collecting}>{isRefreshingSoftware ? "공식 혜택 확인 중..." : "공식 혜택 갱신"}</button>
        </div>

        <div className="collection-row">
          <div className="collection-row-heading">
            <strong>중고마켓</strong><Link to="/used-market">매물·키워드 관리</Link>
            <span>중고나라 공개 매물 · 14일 초과 데이터 자동 삭제 · 출처의 접속 제한 시 중단</span>
          </div>
          <label>검색어 (쉼표 구분, 최대 10개)<input value={usedMarketKeywords} onChange={(event) => setUsedMarketKeywords(event.target.value)} placeholder="엘든링, 젤다" maxLength={1000} /></label>
          <label>
            키워드당 개수
            <input
              type="number"
              min="1"
              max="20"
              value={usedMarketLimit}
              onChange={(event) => setUsedMarketLimit(event.target.value)}
            />
          </label>
          <button onClick={handleCollectUsedMarket} disabled={isUsedMarketLoading}>
            {isUsedMarketLoading ? "중고마켓 수집 중..." : "중고마켓 수집"}
          </button>
        </div>
        {usedMarketResult && <div className="collection-source-results" role="status">
          {(usedMarketResult.cleaned_titles > 0 || usedMarketResult.pending_title_count > 0) && <p>기존 제목 {usedMarketResult.cleaned_titles || 0}개 정리 · 상품명 복원이 필요한 {usedMarketResult.pending_title_count || 0}개는 재수집 전까지 제외했습니다.</p>}
          {usedMarketResult.source_results?.map((row) => <p key={row.source}><strong>{row.source}: {row.collected_count}개</strong> · {({ success: "수집 완료", blocked: "접속 제한", unavailable: "연결 실패", empty: "확인된 매물 없음" })[row.status]} {row.message}{row.stage && <small> · 중단 단계: {row.stage === "robots" ? "robots.txt 확인" : "검색 페이지"}{row.http_status ? ` · HTTP ${row.http_status}` : ""}</small>}</p>)}
        </div>}
      </section>

      <PriceTestPanel />
      <section className="admin-layout production-layout">
        <article className="panel">
          <h2>수집 로그</h2>
          <div className="admin-log-filters">
            <label htmlFor="log-view">로그 보기</label>
            <select id="log-view" value={logView} onChange={(event) => { setLogPage(1); setLogView(event.target.value); }}><option value="summary">실행 요약</option><option value="all">전체 상세 로그</option></select>
            <label htmlFor="log-filter">상태</label>
            <select id="log-filter" value={logFilter} onChange={(event) => { setLogPage(1); setLogFilter(event.target.value); }}><option value="all">모든 상태</option><option value="issues">확인 필요한 작업</option></select>
          </div>
          <div className="admin-log-toolbar">
            <p className="notice-text">최근 3일 로그 {logTotal}개 · {logPage} / {logTotalPages} 페이지<br />진행 중인 작업을 제외한 72시간 이전 로그는 자동 삭제됩니다.</p>
            <div className="log-size-control"><label htmlFor="admin-log-page-size">표시 개수</label><select id="admin-log-page-size" value={logPageSize} onChange={(event) => { setLogPage(1); setLogPageSize(Number(event.target.value)); }}>
              {[10, 25, 50].map((size) => <option key={size} value={size}>{size}개씩</option>)}
            </select></div>
          </div>

          {logs.length === 0 ? (
            <p className="notice-text">아직 수집 로그가 없습니다.</p>
          ) : (
            <div className="log-table-wrap">
              <table className="log-table">
                <thead>
                  <tr>
                    <th>상태</th>
                    <th>플랫폼</th>
                    <th>건수</th>
                    <th>시간</th>
                    <th>메시지</th>
                    <th>시각</th>
                  </tr>
                </thead>
                <tbody>
                  {logs.map((log) => (
                    <tr key={log.id}>
                      <td>
                        <span className={`log-status ${(log.display_status || log.status).toLowerCase()}`} title={`기록 상태: ${log.status}`}>
                          {LOG_LABELS[log.display_status || log.status] || log.status}
                        </span>
                      </td>
                      <td>{log.platform}</td>
                      <td>{log.rows_count}</td>
                      <td>
                        {log.duration_seconds !== null
                          ? `${log.duration_seconds}초`
                          : "-"}
                      </td>
                      <td>{log.message}{log.repeat_count > 1 && <small className="log-repeat">같은 유형 {log.repeat_count}건을 묶었습니다.</small>}</td>
                      <td>{formatKoreanDateTime(log.last_event_at || log.start_time)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {logTotalPages > 1 && (
            <LogPagination page={logPage} totalPages={logTotalPages} onChange={setLogPage} />
          )}
        </article>
      </section>
    </div>
  );
}

export default AdminCollect;
