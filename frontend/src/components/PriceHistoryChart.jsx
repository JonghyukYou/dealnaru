import { ComposedChart, Line, Scatter, XAxis, YAxis, Tooltip, ResponsiveContainer, CartesianGrid, Legend } from "recharts";
import { formatGameMoney } from "../utils/game-format";

function TestPoint({ cx, cy, payload }) {
  if (!Number.isFinite(cx) || !Number.isFinite(cy)) return null;
  return <g className="test-history-point">
    <title>{`(테스트용) ${formatGameMoney(payload.test_price, payload.currency)} · ${new Date(payload.timestamp).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}${payload.is_active ? " · 적용 중" : " · 해제됨"}`}</title>
    <circle cx={cx} cy={cy} r={6} fill="#c06412" stroke="#fff" strokeWidth={2} />
    <text x={cx} y={cy - 13} textAnchor="middle" fill="#8b420b" fontSize={13}>(테스트용)</text>
  </g>;
}

export default function PriceHistoryChart({ history }) {
  const real = (history.history || []).map((row) => ({ ...row, timestamp: Date.parse(row.datetime) }));
  const tests = (history.test_history || []).map((row) => ({ ...row, test_price: row.price, timestamp: Date.parse(row.datetime) }));
  const times = [...real, ...tests].map((row) => row.timestamp).filter(Number.isFinite);
  if (!times.length) return <div className="empty-chart-box">선택한 구매처·기간의 확인된 이력이 없습니다.</div>;
  const min = Math.min(...times), max = Math.max(...times);
  const domain = min === max ? [min - 3600000, max + 3600000] : [min, max + Math.max((max - min) * 0.02, 60000)];
  const intraday = max - min < 2 * 86400000;
  const currency = history.currency || "KRW";
  return <>
    <ResponsiveContainer width="100%" height={300}>
      <ComposedChart data={real} margin={{ top: 24, right: 38, left: 4, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis dataKey="timestamp" type="number" scale="time" domain={domain} tickCount={6} minTickGap={25} tickFormatter={(value) => new Date(value).toLocaleString("ko-KR", intraday ? { hour: "2-digit", minute: "2-digit", timeZone: "Asia/Seoul" } : { year: "numeric", month: "2-digit", day: "2-digit", timeZone: "Asia/Seoul" })} />
        <YAxis tickFormatter={(value) => currency === "KRW" ? `${Math.floor(value / 1000)}천` : value} />
        <Tooltip formatter={(value, name) => [formatGameMoney(value, currency), name]} labelFormatter={(label) => `가격 시점: ${new Date(Number(label)).toLocaleString("ko-KR", { timeZone: "Asia/Seoul" })}`} />
        <Legend />
        <Line name="실제 관측 가격" type="stepAfter" dataKey="price" stroke="#0f766e" strokeWidth={2.5} dot={real.length < 50 ? { r: 3 } : false} connectNulls={false} isAnimationActive={false} />
        {tests.length > 0 && <Scatter name="테스트용 가격" data={tests} dataKey="test_price" fill="#c06412" shape={<TestPoint />} isAnimationActive={false} />}
      </ComposedChart>
    </ResponsiveContainer>
    {tests.length > 0 && <p className="test-chart-notice">주황색 점은 관리자 테스트용 가격입니다. 실제 가격선·최저가 통계·할인 예측에는 포함하지 않습니다.</p>}
  </>;
}
