export const offerStatusLabels = {
  active: "공식 혜택 확인", information: "대상·조건 안내", reference: "공식 안내",
  needs_check: "다시 확인 필요", stale: "최신 조건 확인 필요", expired: "신청 종료",
  upcoming: "시작 예정", inactive: "종료", demo: "예시 데이터",
};

export function formatSubscriptionMoney(value, currency = "KRW") {
  if (value === null || value === undefined) return "금액 확인 필요";
  if (value === 0) return "무료";
  return new Intl.NumberFormat("ko-KR", {
    style: "currency", currency, maximumFractionDigits: currency === "KRW" ? 0 : 2,
  }).format(value);
}

export function formatCheckedAt(value) {
  return value ? new Date(value).toLocaleDateString("ko-KR", { timeZone: "Asia/Seoul" }) : "아직 확인 전";
}
