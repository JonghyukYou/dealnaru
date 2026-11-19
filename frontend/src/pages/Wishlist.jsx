import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api, { apiError } from "../api/client";
import { formatGameMoney } from "../utils/game-format";

function getCoverStyle(game) {
  return {
    backgroundColor: game.image_color,
    backgroundImage: game.header_image ? `url(${game.header_image})` : undefined,
    backgroundSize: "cover",
    backgroundPosition: "center",
  };
}

function Wishlist() {
  const [items, setItems] = useState([]);
  const [editValues, setEditValues] = useState({});
  const [isLoading, setIsLoading] = useState(true);

  const fetchWishlist = useCallback(async () => {
    try {
      const response = await api.get("/api/wishlist");
      setItems(response.data);

      const nextEditValues = {};

      response.data.forEach((item) => {
        nextEditValues[item.id] = {
          target_price: item.target_price ?? "",
          notify_enabled: item.notify_enabled,
        };
      });

      setEditValues((current) => Object.fromEntries(response.data.map((item) => [item.id, current[item.id] || nextEditValues[item.id]])));
    } catch {
      alert("위시리스트를 불러오지 못했습니다. 다시 로그인해주세요.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const handleRemove = async (gameId) => {
    if (!confirm("위시리스트에서 삭제할까요?")) {
      return;
    }

    try {
      await api.delete(`/api/wishlist/games/${gameId}`);
      setItems((prevItems) => prevItems.filter((item) => item.id !== gameId));
    } catch {
      alert("삭제 중 오류가 발생했습니다.");
    }
  };

  const handleEditChange = (gameId, field, value) => {
    setEditValues((prevValues) => ({
      ...prevValues,
      [gameId]: {
        ...prevValues[gameId],
        [field]: value,
      },
    }));
  };

  const handleSaveSettings = async (gameId) => {
    const values = editValues[gameId];

    const targetPrice =
      values.target_price === "" ? null : Number(values.target_price);

    if (targetPrice !== null && (!Number.isSafeInteger(targetPrice) || targetPrice < 0 || targetPrice > 2147483647)) {
      alert("목표 가격은 0~2,147,483,647원의 정수로 입력해 주세요.");
      return;
    }

    try {
      await api.put(`/api/wishlist/games/${gameId}`, {
        target_price: targetPrice,
        notify_enabled: values.notify_enabled,
      });

      alert("위시리스트 설정을 저장했습니다.");
      fetchWishlist();
    } catch (error) {
      alert(apiError(error, "설정 저장에 실패했습니다."));
    }
  };

  useEffect(() => {
    fetchWishlist();
    const timer = window.setInterval(() => { if (!document.hidden) fetchWishlist(); }, 30000);
    window.addEventListener("focus", fetchWishlist);
    return () => { window.clearInterval(timer); window.removeEventListener("focus", fetchWishlist); };
  }, [fetchWishlist]);

  if (isLoading) {
    return <p className="loading-text">위시리스트를 불러오는 중입니다...</p>;
  }

  return (
    <div className="page">
      <section className="category-header">
        <h1>위시리스트</h1>
        <p>목표 가격을 설정하고, 가격이 내려가면 알림을 받을 수 있습니다.</p>
      </section>

      {items.length === 0 ? (
        <section className="empty-panel">
          <h2>저장된 게임이 없습니다.</h2>
          <p>게임 상세 페이지에서 위시리스트에 추가해주세요.</p>
          <Link to="/games" className="primary-button">게임 보러 가기</Link>
        </section>
      ) : (
        <section className="game-card-list">
          {items.map((game) => {
            const editValue = editValues[game.id] || {
              target_price: "",
              notify_enabled: true,
            };

            return (
              <article className="wishlist-manage-card" key={game.id}>
                <Link to={`/games/${game.id}`} className="wishlist-card-link">
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
                      {game.is_test_price && <span className="test-price-badge">테스트용</span>}
                      {game.discount_rate > 0 && <del>{formatGameMoney(game.original_price, game.currency)}</del>}
                      {game.discount_rate > 0 ? <span className="discount-badge">-{game.discount_rate}%</span> : <span className="catalog-status">{game.price_status}</span>}
                    </div>

                    {game.is_target_reached && (
                      <p className="target-reached-text">
                        {game.is_test_price ? "(테스트용) 목표가 도달! 실제 판매 가격은 구매처에서 확인하세요." : "목표가 도달! 지금 확인해보세요."}
                      </p>
                    )}
                  </div>
                </Link>

                <div className="wishlist-setting-box">
                  <label>
                    목표 가격
                    <input
                      type="number"
                      min="0"
                      max="2147483647"
                      step="1"
                      value={editValue.target_price}
                      onChange={(event) =>
                        handleEditChange(game.id, "target_price", event.target.value)
                      }
                      placeholder="예: 30000"
                    />
                  </label>

                  <label className="checkbox-label">
                    <input
                      type="checkbox"
                      checked={editValue.notify_enabled}
                      onChange={(event) =>
                        handleEditChange(game.id, "notify_enabled", event.target.checked)
                      }
                    />
                    알림 받기
                  </label>

                  <div className="wishlist-button-row">
                    <button onClick={() => handleSaveSettings(game.id)}>
                      저장
                    </button>

                    <button
                      className="remove-button"
                      onClick={() => handleRemove(game.id)}
                    >
                      삭제
                    </button>
                  </div>
                </div>
              </article>
            );
          })}
        </section>
      )}
    </div>
  );
}

export default Wishlist;
