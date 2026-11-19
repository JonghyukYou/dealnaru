import { useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api from "../api/client";
import GameSearch from "../components/GameSearch";
import Icon from "../components/Icon";
import { formatGameMoney } from "../utils/game-format";

function cover(game) {
  return { backgroundColor: game.image_color, backgroundImage: game.header_image ? `url(${game.header_image})` : undefined, backgroundSize: "cover", backgroundPosition: "center" };
}
function GamePrice({ game }) {
  return <div className="price-row"><strong>{formatGameMoney(game.discounted_price, game.currency)}</strong>
    {game.is_test_price && <span className="test-price-badge">테스트용</span>}
    {game.discount_rate > 0 ? <><del>{formatGameMoney(game.original_price, game.currency)}</del><span className="discount-badge">-{game.discount_rate}%</span></> : <span className="catalog-status">{game.price_status || "정상가"}</span>}
  </div>;
}
const sections = [
  { value: "games", label: "게임", path: "/games", placeholder: "게임 이름 검색 · Baba Is You" },
  { value: "software", label: "프로그램·AI", path: "/software", placeholder: "프로그램 검색 · Adobe, ChatGPT" },
  { value: "used", label: "중고 거래", path: "/used-market", placeholder: "중고 매물 검색 · 닌텐도, PS5" },
];
export default function Home() {
  const [searchType, setSearchType] = useState("games");
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);
  const navigate = useNavigate();
  useEffect(() => {
    const controller = new AbortController();
    api.get("/api/home", { signal: controller.signal }).then((r) => { setData(r.data); setError(""); }).catch(() => {
      if (!controller.signal.aborted) setError("추천 정보를 불러오지 못했습니다. 다시 시도해주세요.");
    }).finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [reload]);
  const selected = sections.find((section) => section.value === searchType);
  const search = (keyword) => navigate(keyword ? `${selected.path}?keyword=${encodeURIComponent(keyword)}` : selected.path);
  const today = data?.today_deal;
  const random = data?.random_game;
  const popular = data?.popular_games || [];
  return <div className="page home-page">
    <section className="main-search-section home-intro">
      <div>
        <h1>게임·구독·중고 가격 검색</h1>
        <p>게임 구매처와 지난 할인 기록, 프로그램 요금제, 중고나라 매물을 찾아보세요.</p>
      </div>
      <div className="home-search">
        <div className="home-search-tabs" role="group" aria-label="검색할 분야">
          {sections.map((section) => (
            <button key={section.value} type="button" aria-pressed={searchType === section.value}
              className={searchType === section.value ? "active" : ""} onClick={() => setSearchType(section.value)}>
              {section.label}
            </button>
          ))}
        </div>
        <GameSearch key={searchType} onSearch={search} searchLabel={selected.label + " 검색"} placeholder={selected.placeholder} />
      </div>
    </section>
    {error && <div className="empty-panel" role="alert"><p>{error}</p><button className="secondary-button" onClick={() => { setLoading(true); setReload((value) => value + 1); }}>다시 시도</button></div>}
    {loading && <p className="loading-text">추천 정보를 불러오는 중입니다…</p>}
    {!loading && !error && <>
      <section className="hero-grid restored-recommendations">
        {today ? <Link to={`/games/${today.id}`} className="today-deal-card featured-today-card"><div className="cover cover-large today-cover" style={cover(today)}>{!today.header_image && <span>{today.title}</span>}</div><div className="deal-content"><p className="section-label">오늘의 특가</p><h2>{today.title}</h2><p>{today.platform} · 지금 확인한 할인 가격</p><GamePrice game={today}/><span className="featured-link">가격·이력 보기 <Icon name="arrow" size={16}/></span></div></Link>
          : <article className="today-deal-card deal-content"><p className="section-label">오늘의 특가</p><h2>현재 확인된 특가가 없습니다</h2><p>게임 목록에서 현재 가격과 과거 할인 이력을 볼 수 있습니다.</p><Link className="primary-button" to="/games">게임 둘러보기</Link></article>}
        <article className="recommend-card"><div className="section-title-row"><p className="section-label">랜덤 게임 추천</p><button className="text-button" type="button" onClick={() => setReload((value) => value + 1)}>새 추천 ↻</button></div>{random ? <Link to={`/games/${random.id}`} className="random-game-link"><div className="cover recommend-cover" style={cover(random)}>{!random.header_image && <span>{random.title}</span>}</div><div><h2>{random.title}</h2><p>{random.genre} · {random.platform}</p><GamePrice game={random}/></div></Link> : <p className="subscription-notice">표시할 게임이 없습니다.</p>}</article>
      </section>
      <section className="section popular-section"><div className="section-title-row"><div><h2>인기 게임</h2><p className="subscription-notice">평가가 많은 순서로 표시합니다. 정상가인 게임도 포함합니다.</p></div><Link className="text-link" to="/games">전체 보기 <Icon name="arrow" size={16}/></Link></div>
        <div className="popular-grid">{popular.map((game) => <Link to={`/games/${game.id}`} className="small-game-card" key={game.id}><div className="cover cover-small" style={cover(game)}>{!game.header_image && <span>{game.title}</span>}</div>{game.discount_rate > 0 && <span className="corner-discount">-{game.discount_rate}%</span>}<div className="small-game-info"><h3>{game.title}</h3><GamePrice game={game}/></div></Link>)}</div>
        {popular.length === 0 && <p className="subscription-notice">아직 등록된 게임이 없습니다.</p>}
      </section>
    </>}
  </div>;
}
