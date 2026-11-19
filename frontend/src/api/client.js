import axios from "axios";

const apiBaseUrl = (import.meta.env.VITE_API_BASE_URL || "").replace(/\/$/, "");
const api = axios.create({ baseURL: apiBaseUrl, timeout: 15000, withCredentials: true });

export function apiError(error, fallback) {
  const detail = error.response?.data?.detail;
  if (typeof detail === "string") return detail;
  const field = Array.isArray(detail) && detail[0]?.loc?.at(-1);
  const messages = {
    target_price: "목표 가격은 0~2,147,483,647원의 정수로 입력해 주세요.",
    password: "비밀번호는 8~128자로 입력해 주세요.",
    nickname: "닉네임은 50자까지 입력할 수 있습니다.",
    email: "이메일 주소를 확인해 주세요.",
    keywords: "검색어는 최대 10개, 각 100자까지 입력할 수 있습니다.",
    limit_per_keyword: "키워드당 수집 개수는 1~20개로 입력해 주세요.",
  };
  return (field && messages[field]) || fallback;
}

export default api;
