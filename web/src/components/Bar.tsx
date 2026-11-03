import { fmtMemPair, memPct } from "../format";

/** 사용률 막대. 색은 행의 --row 변수(소유자 색)를 따른다. 말풍선은 title, 없으면 백분율이다. */
export function Bar({ pct, title }: { pct: number | null; title?: string }) {
  const width = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className="bar" title={title ?? (pct === null ? "수집 불가" : `${pct}%`)}>
      <i style={{ width: `${width}%` }} />
    </div>
  );
}

/** 메모리 막대. 전체 메모리 대비 사용 비율만큼 채운다. 말풍선은 `메모리 12 / 48 GB (25%)`. */
export function MemBar({ used, total }: { used: number | null; total: number }) {
  const pct = memPct(used, total);
  const pair = fmtMemPair(used, total);
  return <Bar pct={pct} title={pct === null ? `메모리 ${pair} · 수집 불가` : `메모리 ${pair} (${pct}%)`} />;
}
