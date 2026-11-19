import { lazy, Suspense, useEffect, useState } from "react";
import { Link, NavLink, Navigate, Route, Routes, useNavigate } from "react-router-dom";
import api from "./api/client";
import ProtectedRoute from "./routes/ProtectedRoute";
import { useAuth } from "./context/auth-context";
import Icon from "./components/Icon";
import { repositoryUrl, supportEmail } from "./utils/site-config";

const AdminCollect = lazy(() => import("./pages/AdminCollect"));
const GameDetail = lazy(() => import("./pages/GameDetail"));
const Games = lazy(() => import("./pages/Games"));
const Home = lazy(() => import("./pages/Home"));
const Login = lazy(() => import("./pages/Login"));
const Notifications = lazy(() => import("./pages/Notifications"));
const Register = lazy(() => import("./pages/Register"));
const SoftwareDetail = lazy(() => import("./pages/SoftwareDetail"));
const SoftwareList = lazy(() => import("./pages/SoftwareList"));
const UsedMarket = lazy(() => import("./pages/UsedMarket"));
const VerifyEmail = lazy(() => import("./pages/VerifyEmail"));
const Wishlist = lazy(() => import("./pages/Wishlist"));

const Policy = lazy(() => import("./pages/Policy"));

function App() {
  const { user, logout } = useAuth();
  const navigate = useNavigate();
  const [unreadCount, setUnreadCount] = useState(0);

  const [loggingOut, setLoggingOut] = useState(false);
  const [logoutError, setLogoutError] = useState("");
  const handleLogout = async () => {
    setLoggingOut(true);
    setLogoutError("");
    try {
      await logout();
      navigate("/");
    } catch {
      setLogoutError("로그아웃하지 못했습니다. 연결을 확인한 뒤 다시 눌러 주세요.");
    } finally {
      setLoggingOut(false);
    }
  };

  useEffect(() => {
    let active = true;

    const fetchUnreadCount = async () => {
      if (!user) {
        if (active) {
          setUnreadCount(0);
        }
        return;
      }

      try {
        const response = await api.get("/api/notifications/unread-count");
        if (active) {
          setUnreadCount(response.data.count);
        }
      } catch {
        if (active) {
          setUnreadCount(0);
        }
      }
    };

    fetchUnreadCount();
    window.addEventListener("gamesoft:notifications-updated", fetchUnreadCount);

    return () => {
      active = false;
      window.removeEventListener("gamesoft:notifications-updated", fetchUnreadCount);
    };
  }, [user]);

  return (
    <div className="app">
      <header className="site-header">
        <Link to="/" className="logo" aria-label="딜나루 홈">
          <img className="brand-mark" src="/brand/dealnaru-mark.png" alt="" />
          <span className="logo-text">
            <strong>딜나루</strong>
          </span>
        </Link>

        <nav className="nav" aria-label="주요 메뉴">
          <NavLink to="/games"><Icon name="game" />게임</NavLink>
          <NavLink to="/software"><Icon name="software" />프로그램·AI</NavLink>
          <NavLink to="/used-market"><Icon name="used" />중고 거래</NavLink>
          <NavLink to="/wishlist"><Icon name="heart" />찜</NavLink>
          <NavLink to="/notifications" className="notification-link">
            <Icon name="bell" />알림
            {unreadCount > 0 && <span className="notification-badge">{unreadCount}</span>}
          </NavLink>
          {user?.is_admin && <NavLink to="/admin"><Icon name="settings" />관리</NavLink>}
        </nav>

        <div className="account-nav">
          {user ? (
            <>
              <span className="user-chip">{user.nickname}</span>
              <button className="login-button" onClick={handleLogout} disabled={loggingOut}>
                로그아웃
              </button>
            </>
          ) : (
            <Link to="/login" className="login-button">
              로그인
            </Link>
          )}
        </div>
      </header>

      {logoutError && <p className="account-message" role="alert">{logoutError}</p>}
      <main id="main-content">
        <Suspense fallback={<p className="loading-text">화면을 불러오는 중입니다...</p>}>
          <Routes>
          <Route path="/" element={<Home />} />
          <Route path="/games" element={<Games />} />
          <Route path="/games/:gameId" element={<GameDetail />} />
          <Route path="/software" element={<SoftwareList />} />
          <Route path="/software/:softwareId" element={<SoftwareDetail />} />
          <Route path="/used-market" element={<UsedMarket />} />
          <Route
            path="/wishlist"
            element={
              <ProtectedRoute>
                <Wishlist />
              </ProtectedRoute>
            }
          />
          <Route path="/login" element={<Login />} />
          <Route path="/register" element={<Register />} />
          <Route path="/verify-email" element={<VerifyEmail />} />
          <Route path="/terms" element={<Policy />} />
          <Route path="/privacy" element={<Policy privacy />} />
          <Route
            path="/notifications"
            element={
              <ProtectedRoute>
                <Notifications />
              </ProtectedRoute>
            }
          />
          <Route
            path="/admin"
            element={
              <ProtectedRoute requireAdmin={true}>
                <AdminCollect />
              </ProtectedRoute>
            }
          />
          <Route path="*" element={<Navigate to="/" replace />} />
          </Routes>
        </Suspense>
      </main>

      <footer className="footer">
        <div className="footer-caption">
          <span className="footer-brand">딜나루</span>
          <span>캡스톤디자인 가격 정보 프로젝트</span>
        </div>
        <nav aria-label="사이트 정보" className="footer-links">
          <Link to="/terms">이용약관</Link>
          <Link to="/privacy">개인정보 처리방침</Link>
          {repositoryUrl && <a href={repositoryUrl} target="_blank" rel="noopener noreferrer">GitHub 저장소 ↗</a>}
          {supportEmail && <a href={"mailto:" + supportEmail}>문의</a>}
        </nav>
      </footer>
    </div>
  );
}

export default App;
