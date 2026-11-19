import { useEffect, useRef, useState } from "react";

export default function GameSearch({ keyword = "", onSearch, compact = false, searchLabel = "게임 검색", placeholder = "게임 이름 검색 · The Binding of Isaac" }) {
  const [value, setValue] = useState(keyword);
  const inputRef = useRef(null);

  useEffect(() => { setValue(keyword); }, [keyword]);

  const submit = (event) => {
    event.preventDefault();
    const nextKeyword = value.trim();
    setValue(nextKeyword);
    onSearch(nextKeyword);
  };

  const clear = () => {
    setValue("");
    onSearch("");
    inputRef.current?.focus();
  };

  return <form role="search" aria-label={searchLabel} className={`game-search-form${compact ? " game-search-compact" : ""}`} onSubmit={submit}>
    <div className="main-search-box">
      <input ref={inputRef} type="search" name="keyword" maxLength={200} aria-label={`${searchLabel}어`} value={value}
        onChange={(event) => setValue(event.target.value)}
        onKeyDown={(event) => { if (event.key === "Enter" && event.nativeEvent.isComposing) event.preventDefault(); }}
        placeholder={placeholder} />
      <button type="submit">검색</button>
      {(value || keyword) && <button type="button" className="search-clear-button" onClick={clear}>초기화</button>}
    </div>
  </form>;
}
