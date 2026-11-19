import { Navigate, useLocation } from "react-router-dom";
import { useAuth } from "../context/auth-context";
import AccessDenied from "../pages/AccessDenied";

function ProtectedRoute({ children, requireAdmin = false }) {
  const { user, isAuthLoading } = useAuth();
  const location = useLocation();

  if (isAuthLoading) {
    return <p className="loading-text">권한을 확인하는 중입니다...</p>;
  }

  if (!user) {
    return (
      <Navigate
        to="/login"
        replace
        state={{ from: location.pathname }}
      />
    );
  }

  if (requireAdmin && !user.is_admin) {
    return <AccessDenied />;
  }

  return children;
}

export default ProtectedRoute;
