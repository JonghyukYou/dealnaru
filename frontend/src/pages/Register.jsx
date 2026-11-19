import { useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import api, { apiError } from "../api/client";

function Register() {
  const navigate = useNavigate();

  const [email, setEmail] = useState("");
  const [nickname, setNickname] = useState("");
  const [password, setPassword] = useState("");
  const [accepted, setAccepted] = useState(false);
  const [ageConfirmed, setAgeConfirmed] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [errorMessage, setErrorMessage] = useState("");

  const handleSubmit = async (event) => {
    event.preventDefault();

    setErrorMessage("");
    setSubmitting(true);
    try {
      const response = await api.post("/api/auth/register", {
        email,
        nickname,
        password,
        accept_terms: accepted,
        age_confirmed: ageConfirmed,
      });

      const devUrl = response.data.dev_verify_url
        ? `\n\n개발 모드 인증 링크:\n${response.data.dev_verify_url}`
        : "";

      alert(`${response.data.message}${devUrl}`);
      navigate("/login", { state: { email } });
    } catch (error) {
      setErrorMessage(apiError(error, "회원가입에 실패했습니다."));
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div className="page">
      <section className="auth-box">
        <h1>회원가입</h1>
        <p>가입 후 이메일 인증을 완료하면 로그인할 수 있습니다.</p>

        <form onSubmit={handleSubmit} className="auth-form">
          <label>
            이메일
            <input
              type="email"
              autoComplete="email"
              maxLength={255}
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="email@example.com"
              required
            />
          </label>

          <label>
            닉네임
            <input
              autoComplete="nickname"
              maxLength={50}
              value={nickname}
              onChange={(event) => setNickname(event.target.value)}
              placeholder="닉네임"
              required
            />
          </label>

          <label>
            비밀번호
            <input
              type="password"
              autoComplete="new-password"
              maxLength={128}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="8자 이상"
              minLength={8}
              required
            />
          </label>

          <label className="terms-checkbox">
            <input type="checkbox" checked={ageConfirmed} onChange={(event) => setAgeConfirmed(event.target.checked)} required />
            <span>만 14세 이상입니다.</span>
          </label>
          <label className="terms-checkbox">
            <input type="checkbox" checked={accepted} onChange={(event) => setAccepted(event.target.checked)} required />
            <span><Link to="/terms" target="_blank" rel="noopener">이용약관</Link>에 동의합니다.</span>
          </label>
          <p className="policy-meta">가입 정보 처리와 탈퇴 절차는 <Link to="/privacy" target="_blank" rel="noopener">개인정보 처리방침</Link>을 확인해 주세요.</p>
          {errorMessage && <p className="form-error" role="alert">{errorMessage}</p>}
          <button type="submit" disabled={submitting}>{submitting ? "가입 처리 중" : "회원가입"}</button>
        </form>

        <p className="auth-link-text">
          이미 계정이 있습니까? <Link to="/login">로그인</Link>
        </p>
      </section>
    </div>
  );
}

export default Register;
