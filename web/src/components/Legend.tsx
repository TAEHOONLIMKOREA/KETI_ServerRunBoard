import { type ReactNode, useMemo } from "react";
import { legendEntries } from "../colors";
import type { Overview } from "../types";
import { Swatch } from "./Swatch";

/** 행 색 범례. 지금 화면에 있는 색만 올린다. children 은 줄 오른쪽 끝에 붙는 다른 보기 링크다. */
export function Legend({ overview, children }: { overview: Overview; children?: ReactNode }) {
  const entries = useMemo(() => legendEntries(overview), [overview]);
  return (
    <div className="legend" aria-label="행 색 범례">
      {entries.map((e) => (
        <span key={e.key} className="legend-item">
          <Swatch paint={e.paint} />
          <b>{e.label}</b>
        </span>
      ))}
      {children}
    </div>
  );
}
