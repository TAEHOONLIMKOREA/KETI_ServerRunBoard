/** 사용률 막대. 색은 행의 --row 변수(소유자 색)를 따른다. */
export function Bar({ pct }: { pct: number | null }) {
  const width = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className="bar" title={pct === null ? "수집 불가" : `${pct}%`}>
      <i style={{ width: `${width}%` }} />
    </div>
  );
}
