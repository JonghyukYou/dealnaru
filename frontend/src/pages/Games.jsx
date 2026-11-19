import { useCallback, useEffect, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import api from "../api/client";
import GameSearch from "../components/GameSearch";
import { formatGameMoney } from "../utils/game-format";

const genres = [
  "전체",
  "액션",
  "RPG",
  "어드벤쳐",
  "전략",
  "시뮬",
  "인디",
  "공포",
  "퍼즐",
  "스포츠",
  "레이싱",
  "캐주얼",
  "기타",
];

const priceRanges = [
  { label: "전체", min: 0, max: null },
  { label: "0원 ~ 10,000원", min: 0, max: 10000 },
  { label: "10,000원 ~ 20,000원", min: 10000, max: 20000 },
  { label: "20,000원 ~ 30,000원", min: 20000, max: 30000 },
  { label: "30,000원 ~ 40,000원", min: 30000, max: 40000 },
  { label: "40,000원 ~ 50,000원", min: 40000, max: 50000 },
  { label: "50,000원 ~ 60,000원", min: 50000, max: 60000 },
  { label: "60,000원 ~ 70,000원", min: 60000, max: 70000 },
  { label: "70,000원 ~ 80,000원", min: 70000, max: 80000 },
  { label: "80,000원 ~ 90,000원", min: 80000, max: 90000 },
  { label: "90,000원 ~ 100,000원", min: 90000, max: 100000 },
  { label: "100,000원 이상", min: 100000, max: null },
];

function getCoverStyle(game) {
  return {
    backgroundColor: game.image_color,
    backgroundImage: game.header_image ? `url(${game.header_image})` : undefined,
    backgroundSize: "cover",
    backgroundPosition: "center",
  };
}

function Games() {
  const [searchParams, setSearchParams] = useSearchParams();
  const keyword = searchParams.get("keyword") || "";

  const [selectedGenre, setSelectedGenre] = useState("전체");
  const [selectedPriceRangeIndex, setSelectedPriceRangeIndex] = useState(0);
  const [minDiscount, setMinDiscount] = useState(0);
  const [selectedPlatform, setSelectedPlatform] = useState("전체 플랫폼");
  const [sort, setSort] = useState("popular");

  const [games, setGames] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState("");
  const [reload, setReload] = useState(0);

  const [page, setPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [total, setTotal] = useState(0);

  const pageSize = 10;

  const selectedPriceRange = priceRanges[selectedPriceRangeIndex];

  const fetchGames = useCallback(async (signal) => {
    try {
      setIsLoading(true);
      setError("");

      const response = await api.get("/api/games", {
        params: {
          keyword,
          genre: selectedGenre,
          min_discount: minDiscount,
          min_price: selectedPriceRange.min,
          max_price: selectedPriceRange.max,
          platform: selectedPlatform,
          sort,
          page,
          page_size: pageSize,
          paginate: true,
        },
        signal,
      });
      if (signal?.aborted) return;
      setGames(response.data.items);
      setTotal(response.data.total);
      setTotalPages(response.data.total_pages);
    } catch {
      if (!signal?.aborted) setError("게임 목록을 불러오지 못했습니다. 다시 시도해주세요.");
    } finally {
      if (!signal?.aborted) setIsLoading(false);
    }
  }, [
    keyword,
    minDiscount,
    page,
    selectedGenre,
    selectedPlatform,
    selectedPriceRange.max,
    selectedPriceRange.min,
    sort,
  ]);

  const resetPage = () => {
    setPage(1);
  };

  useEffect(() => {
    setPage(1);
  }, [keyword]);

  useEffect(() => {
    const controller = new AbortController();
    fetchGames(controller.signal);
    return () => controller.abort();
  }, [fetchGames, reload]);

  const search = (nextKeyword) => {
    const nextParams = new URLSearchParams(searchParams);
    if (nextKeyword) nextParams.set("keyword", nextKeyword);
    else nextParams.delete("keyword");
    setPage(1);
    setSearchParams(nextParams);
    setReload((value) => value + 1);
  };

  const pageNumbers = Array.from(
    { length: totalPages },
    (_, index) => index + 1
  );

  return (
    <div className="page">
      <section className="category-header">
        <h1>게임</h1>
        <p>다양한 장르의 게임을 최저가로 비교해보세요.</p>
      </section>

      <GameSearch keyword={keyword} onSearch={search} compact />

      {keyword && (
        <section className="search-result-notice">
          <div>
            <strong>검색어:</strong> {keyword}
            <span className="result-count">총 {total}개</span>
          </div>
          <button type="button" className="search-reset-link" onClick={() => search("")}>검색 해제</button>
        </section>
      )}

      <section className="genre-tabs">
        <span>장르별 찾기</span>

        <div className="genre-button-wrap">
          {genres.map((genre) => (
            <button
              key={genre}
              className={selectedGenre === genre ? "active" : ""}
              onClick={() => {
                setSelectedGenre(genre);
                resetPage();
              }}
            >
              {genre}
            </button>
          ))}
        </div>
      </section>

      <section className="list-layout">
        <aside className="filter-panel">
          <h2>필터</h2>

          <label>
            가격 범위
            <select
              value={selectedPriceRangeIndex}
              onChange={(event) => {
                setSelectedPriceRangeIndex(Number(event.target.value));
                resetPage();
              }}
            >
              {priceRanges.map((range, index) => (
                <option value={index} key={range.label}>
                  {range.label}
                </option>
              ))}
            </select>
          </label>

          <div className="filter-group">
            <p>할인율</p>

            <button
              className={minDiscount === 10 ? "active" : ""}
              onClick={() => {
                setMinDiscount(10);
                resetPage();
              }}
            >
              10% 이상
            </button>

            <button
              className={minDiscount === 30 ? "active" : ""}
              onClick={() => {
                setMinDiscount(30);
                resetPage();
              }}
            >
              30% 이상
            </button>

            <button
              className={minDiscount === 50 ? "active" : ""}
              onClick={() => {
                setMinDiscount(50);
                resetPage();
              }}
            >
              50% 이상
            </button>

            <button
              className={minDiscount === 70 ? "active" : ""}
              onClick={() => {
                setMinDiscount(70);
                resetPage();
              }}
            >
              70% 이상
            </button>

            <button
              onClick={() => {
                setMinDiscount(0);
                resetPage();
              }}
            >
              할인율 초기화
            </button>
          </div>

          <label>
            플랫폼
            <select
              value={selectedPlatform}
              onChange={(event) => {
                setSelectedPlatform(event.target.value);
                resetPage();
              }}
            >
              <option>전체 플랫폼</option>
              <option>Steam</option>
              <option>Epic Store</option>
              <option>Other</option>
            </select>
          </label>

          <label>
            정렬 기준
            <select
              value={sort}
              onChange={(event) => {
                setSort(event.target.value);
                resetPage();
              }}
            >
              <option value="popular">인기순</option>
              <option value="price">가격 낮은순</option>
              <option value="discount">할인율 높은순</option>
            </select>
          </label>

          <button
            className="apply-button"
            onClick={() => {
              resetPage();
              setReload((value) => value + 1);
            }}
          >
            적용하기
          </button>
        </aside>

        <section className="game-card-list">
          <div className="list-summary">
            <strong>총 {total}개</strong>
            <span>
              {page} / {totalPages} 페이지
            </span>
          </div>

          {isLoading && <p className="loading-text">게임 목록을 불러오는 중입니다...</p>}

          {error && <div className="empty-panel" role="alert"><p>{error}</p><button className="secondary-button" onClick={() => setReload((value) => value + 1)}>다시 시도</button></div>}

          {!isLoading && !error && games.length === 0 && (
            <p className="loading-text">조건에 맞는 게임이 없습니다.</p>
          )}

          {!isLoading && !error &&
            games.map((game) => (
              <Link to={`/games/${game.id}`} className="wide-game-card" key={game.id}>
                <div
                  className="cover cover-medium"
                  style={getCoverStyle(game)}
                >
                  {!game.header_image && <span>{game.title}</span>}
                </div>

                <div className="wide-game-info">
                  <h2>{game.title}</h2>
                  <p>{game.platform}</p>

                  <div className="price-row">
                    <strong>{formatGameMoney(game.discounted_price, game.currency)}</strong>
                    {game.discount_rate > 0 && <del>{formatGameMoney(game.original_price, game.currency)}</del>}
                    {game.discount_rate > 0 ? <span className="discount-badge">-{game.discount_rate}%</span> : <span className="catalog-status">{game.price_status}</span>}
                  </div>

                  <p className="tag-line">
                    {[game.genre, ...(game.tags || [])]
                      .filter(Boolean)
                      .slice(0, 5)
                      .join(" · ")}
                  </p>
                </div>
              </Link>
            ))}

          {totalPages > 1 && (
            <div className="pagination">
              <button
                disabled={page === 1}
                onClick={() => setPage((prevPage) => Math.max(1, prevPage - 1))}
              >
                이전
              </button>

              {pageNumbers.map((pageNumber) => (
                <button
                  key={pageNumber}
                  className={page === pageNumber ? "active" : ""}
                  onClick={() => setPage(pageNumber)}
                >
                  {pageNumber}
                </button>
              ))}

              <button
                disabled={page === totalPages}
                onClick={() =>
                  setPage((prevPage) => Math.min(totalPages, prevPage + 1))
                }
              >
                다음
              </button>
            </div>
          )}
        </section>
      </section>
    </div>
  );
}

export default Games;
