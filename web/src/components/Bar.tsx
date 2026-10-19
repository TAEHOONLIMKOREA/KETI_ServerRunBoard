import type { Color } from "../types";

export function Bar({ pct, color }: { pct: number | null; color: Color }) {
  const width = pct === null ? 0 : Math.max(0, Math.min(100, pct));
  return (
    <div className="bar" title={pct === null ? "수집 불가" : `${pct}%`}>
      <i className={`bg-${color}`} style={{ width: `${width}%` }} />
    </div>
  );
}
