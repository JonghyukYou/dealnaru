import { useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import api, { apiError } from "../api/client";
import { useAuth } from "../context/auth-context";

function Login() {
  const navigate = useNavigate();
  const { login } = useAuth();
  const location = useLocation();
  const from = location.state?.from || "/";

  const [email, setEmail] = useState(location.state?.email || "");
  const [password, setPassword] = useState("");
  const [isResending, setIsResending] = useState(false);

  const handleSubmit = async (event) => {
    event.preventDefault();

    try {
      const response = await api.post("/api/auth/login", {
        email,
        password,
      });

      login(response.data.user);
      navigate(from);
    } catch (error) {
      alert(apiError(error, "로그인에 실패했습니다."));
    }
  };

  const handleResendVerification = async () => {
    if (!email) {
      alert("인증 메일을 받을 이메일을 입력해주세요.");
      return;
    }

    try {
      setIsResending(true);

      const response = await api.post("/api/auth/resend-verification", {
        email,
      });

      const devUrl = response.data.dev_verify_url
        ? `\n\n개발 모드 인증 링크:\n${response.data.dev_verify_url}`
        : "";

      alert(`${response.data.message}${devUrl}`);
    } catch (error) {
      alert(apiError(error, "인증 메일을 다시 요청하지 못했습니다."));
    } finally {
      setIsResending(false);
    }
  };

  return (
    <div className="page">
      <section className="auth-box">
        <h1>로그인</h1>
        <p>위시리스트를 사용하려면 로그인해주십시오.</p>

        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            이메일
            <input
              type="email"
              autoComplete="username"
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="email@example.com"
              required
            />
          </label>

          <label>
            비밀번호
            <input
              type="password"
              autoComplete="current-password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="비밀번호"
              required
            />
          </label>

          <button type="submit">로그인</button>

          <button
            type="button"
            className="secondary-button"
            onClick={handleResendVerification}
            disabled={isResending}
          >
            {isResending ? "재전송 중..." : "인증 메일 다시 받기"}
          </button>
        </form>

        <p className="auth-link-text">
          처음 이용하시나요? <Link to="/register">회원가입</Link>
        </p>
      </section>
    </div>
  );
}

export default Login;
