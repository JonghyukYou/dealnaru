import { Link } from "react-router-dom";

function AccessDenied() {
  return (
    <div className="page">
      <section className="empty-panel access-denied-panel">
        <span className="access-denied-icon">!</span>

        <h1>접근 권한이 없습니다</h1>

        <p>
          이 페이지는 관리자만 사용할 수 있습니다.
          일반 사용자는 게임 검색, 위시리스트, 알림 기능을 이용할 수 있습니다.
        </p>

        <div className="detail-button-row">
          <Link to="/" className="primary-button">
            메인으로 돌아가기
          </Link>

          <Link to="/games" className="secondary-button">
            게임 보러 가기
          </Link>
        </div>
      </section>
    </div>
  );
}

export default AccessDenied;