function pageButtons(page, totalPages) {
  const pages = new Set([1, totalPages]);
  for (let value = Math.max(1, page - 2); value <= Math.min(totalPages, page + 2); value += 1) {
    pages.add(value);
  }
  const items = [];
  let previous = 0;
  for (const value of [...pages].sort((a, b) => a - b)) {
    if (previous && value - previous > 1) items.push(`gap-${value}`);
    items.push(value);
    previous = value;
  }
  return items;
}

export default function LogPagination({ page, totalPages, onChange }) {
  const go = (value) => onChange(Math.max(1, Math.min(totalPages, value)));
  const jump = (event) => {
    event.preventDefault();
    const value = Number(new FormData(event.currentTarget).get("page"));
    if (Number.isInteger(value)) go(value);
  };

  return <nav className="pagination admin-log-pagination" aria-label="수집 로그 페이지">
    <div className="log-page-buttons">
      <button type="button" disabled={page === 1} onClick={() => go(1)}>처음</button>
      <button type="button" disabled={page === 1} onClick={() => go(page - 1)}>이전</button>
      {pageButtons(page, totalPages).map((value) => typeof value === "number"
        ? <button type="button" key={value} className={value === page ? "active" : ""} aria-label={`${value} 페이지`} aria-current={value === page ? "page" : undefined} onClick={() => go(value)}>{value}</button>
        : <span className="log-page-gap" key={value} aria-hidden="true">…</span>)}
      <button type="button" disabled={page === totalPages} onClick={() => go(page + 1)}>다음</button>
      <button type="button" disabled={page === totalPages} onClick={() => go(totalPages)}>끝</button>
    </div>
    <form className="log-page-jump" onSubmit={jump}>
      <label htmlFor="log-page-jump">페이지 이동</label>
      <input key={`${page}-${totalPages}`} id="log-page-jump" name="page" type="number" min="1" max={totalPages} step="1" defaultValue={page} required />
      <span>/ {totalPages}</span>
      <button type="submit">이동</button>
    </form>
  </nav>;
}
