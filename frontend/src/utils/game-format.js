export function formatGameMoney(value, currency = "KRW") {
  if (value === null || value === undefined || !Number.isFinite(Number(value))) return "가격 확인 중";
  if (currency === "KRW") return `${Number(value).toLocaleString("ko-KR")}원`;
  return new Intl.NumberFormat("ko-KR", { style: "currency", currency, maximumFractionDigits: 2 }).format(value);
}
