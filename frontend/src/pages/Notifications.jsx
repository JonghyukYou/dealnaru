import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import api from "../api/client";

function Notifications() {
  const [notifications, setNotifications] = useState([]);
  const [isLoading, setIsLoading] = useState(true);

  const fetchNotifications = useCallback(async () => {
    try {
      const response = await api.get("/api/notifications");
      setNotifications(response.data);
    } catch {
      alert("알림을 불러오지 못했습니다. 다시 로그인해주세요.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const markAsRead = async (notificationId) => {
    try {
      await api.patch(`/api/notifications/${notificationId}/read`);

      setNotifications((prevNotifications) =>
        prevNotifications.map((notification) =>
          notification.id === notificationId
            ? { ...notification, is_read: true }
            : notification
        )
      );
      window.dispatchEvent(new Event("gamesoft:notifications-updated"));
    } catch {
      alert("알림 읽음 처리 중 오류가 발생했습니다.");
    }
  };

  const markAllAsRead = async () => {
    try {
      await api.patch("/api/notifications/read-all");

      setNotifications((prevNotifications) =>
        prevNotifications.map((notification) => ({
          ...notification,
          is_read: true,
        }))
      );
      window.dispatchEvent(new Event("gamesoft:notifications-updated"));
    } catch {
      alert("전체 읽음 처리 중 오류가 발생했습니다.");
    }
  };

  useEffect(() => {
    fetchNotifications();
  }, [fetchNotifications]);

  if (isLoading) {
    return <p className="loading-text">알림을 불러오는 중입니다...</p>;
  }

  return (
    <div className="page">
      <section className="category-header">
        <h1>알림</h1>
        <p>위시리스트 목표가 도달, 할인 정보 업데이트를 확인합니다.</p>
      </section>

      <section className="notification-toolbar">
        <strong>전체 {notifications.length}개</strong>
        <button onClick={markAllAsRead}>모두 읽음 처리</button>
      </section>

      {notifications.length === 0 ? (
        <section className="empty-panel">
          <h2>알림이 없습니다.</h2>
          <p>위시리스트에 게임을 추가하고 목표 가격을 설정해주세요.</p>
          <Link to="/games" className="primary-button">게임 보러 가기</Link>
        </section>
      ) : (
        <section className="notification-list">
          {notifications.map((notification) => (
            <article
              className={`notification-card ${notification.is_read ? "read" : "unread"}`}
              key={notification.id}
            >
              <div>
                <span className="notification-type">{notification.type}</span>
                <h2>{notification.title}</h2>
                <p>{notification.message}</p>
                <small>
                  {notification.created_at
                    ? new Date(notification.created_at).toLocaleString()
                    : ""}
                </small>
              </div>

              <div className="notification-actions">
                {notification.game_id && (
                  <Link to={`/games/${notification.game_id}`} className="secondary-button">
                    게임 보기
                  </Link>
                )}

                {!notification.is_read && (
                  <button onClick={() => markAsRead(notification.id)}>
                    읽음
                  </button>
                )}
              </div>
            </article>
          ))}
        </section>
      )}
    </div>
  );
}

export default Notifications;
