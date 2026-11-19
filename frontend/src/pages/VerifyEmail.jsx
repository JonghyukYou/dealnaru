import { useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import api from "../api/client";

function VerifyEmail() {
  const location = useLocation();
  const navigate = useNavigate();

  const [message, setMessage] = useState("");
  const [status, setStatus] = useState("ready");
  const isSubmittingRef = useRef(false);

  const token = new URLSearchParams(location.hash.slice(1)).get("token") || new URLSearchParams(location.search).get("token");

  const handleVerifyEmail = async () => {
    if (!token) {
      setStatus("error");
      setMessage("인증 토큰이 없습니다.");
      return;
    }

    if (isSubmittingRef.current) {
      return;
    }

    try {
      isSubmittingRef.current = true;
      setStatus("loading");
      setMessage("이메일 인증을 처리하는 중입니다...");

      const response = await api.post("/api/auth/verify-email", {
        token,
      });

      navigate("/verify-email", { replace: true });
      setStatus("success");
      setMessage(response.data.message);
    } catch (error) {
      setStatus("error");
      setMessage(
        error.response?.data?.detail || "이메일 인증에 실패했습니다."
      );
    } finally {
      isSubmittingRef.current = false;
    }
  };

  return (
    <div className="page">
      <section className="auth-box">
        <h1>이메일 인증</h1>

        {status === "ready" && (
          <>
            <p>
              {token ? "아래 버튼을 누르면 이메일 인증이 완료됩니다." : "유효한 인증 링크가 필요합니다. 메일함에서 인증 링크를 다시 열어 주세요."}
            </p>

            <button
              type="button"
              className="primary-button"
              onClick={handleVerifyEmail}
              disabled={!token}
            >
              이메일 인증하기
            </button>
          </>
        )}

        {status === "loading" && <p>{message}</p>}

        {status === "success" && (
          <>
            <p>{message}</p>
            <Link to="/login" className="primary-button">
              로그인하러 가기
            </Link>
          </>
        )}

        {status === "error" && (
          <>
            <p>{message}</p>
            <Link to="/login" className="secondary-button">
              로그인 페이지로 이동
            </Link>
          </>
        )}
      </section>
    </div>
  );
}

export default VerifyEmail;