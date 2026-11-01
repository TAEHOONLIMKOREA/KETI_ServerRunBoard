import { type KeyboardEvent, type PointerEvent, useEffect, useMemo, useRef, useState } from "react";
import { fmtClock, fmtDay, fmtMemPair, fmtPct, fmtStamp, rangeLabel } from "../format";
import type { GpuSeries, HistoryPoint, NodeHistory } from "../types";

// GPU 이력 차트 (인라인 SVG). 세로축은 0~100% 하나이고 세 series 를 같은 축에 그린다:
// 사용률 평균(면), 사용률 최대(얇은 선), 메모리 사용 비율(선). null 칸은 0 이 아니라 끊긴 자리로 둔다.
// SVG 는 가로·세로 모두 0~100 백분율 좌표로 그려 상자에 맞춰 늘린다 (선 굵기는 non-scaling-stroke 로 고정).
// 글자·십자선·툴팁은 HTML 로 같은 백분율 위치에 얹어 늘어나지 않게 한다. 색은 styles.css 의 --hist-*.

const SERIES = [
  { key: "avg", label: "사용률 평균", tip: "사용률 평균" },
  { key: "max", label: "사용률 최대", tip: "사용률 최대" },
  { key: "memory", label: "메모리 사용 비율", tip: "메모리" },
] as const;
type SeriesKey = (typeof SERIES)[number]["key"];
type Values = Record<SeriesKey, number | null>;

const EDGE_PCT = 4; // 양 끝에서 이만큼 안쪽의 눈금은 글자가 상자 밖으로 나가므로 선만 긋는다
const TIP_PX = 200; // 툴팁이 십자선 옆에 차지하는 폭 어림값 (띄우는 간격 10px 포함)
const clamp = (v: number) => Math.max(0, Math.min(100, v));
const pct = (v: number | null) => fmtPct(v === null ? null : Math.round(v));

function valuesOf(p: HistoryPoint, memTotal: number): Values {
  const memory = p.mem_avg_mb === null || memTotal <= 0 ? null : (p.mem_avg_mb / memTotal) * 100;
  return { avg: p.util_avg, max: p.util_max, memory };
}

/** null 로 끊긴 구간마다 선 하나. 칸 i 는 가로 [i, i+1] / n 이고 값은 칸 가운데에 찍는다.
 *  구간 양 끝은 칸 가장자리까지 늘려 점 하나짜리 구간도 보이게 한다. area 는 같은 선을 바닥까지 닫은 면이다. */
function paths(vals: (number | null)[]): { line: string; area: string } {
  const x = (i: number) => ((i / vals.length) * 100).toFixed(2);
  let line = "";
  let area = "";
  let start = -1;
  let run = "";
  vals.forEach((v, i) => {
    if (v === null) return;
    const y = (100 - clamp(v)).toFixed(1);
    if (start < 0) {
      start = i;
      run = `${x(i)} ${y}`;
    }
    run += `L${x(i + 0.5)} ${y}`;
    if (i + 1 === vals.length || vals[i + 1] === null) {
      run += `L${x(i + 1)} ${y}`;
      line += `M${run}`;
      area += `M${x(start)} 100L${run}L${x(i + 1)} 100Z`;
      start = -1;
    }
  });
  return { line, area };
}

interface Tick {
  at: number; // 가로 위치 %
  label: string | null;
}

/** x 축 눈금 (현지 시각). 24시간은 6시간마다, 7일은 자정마다이고 자정은 날짜로 적는다. */
function ticks(h: NodeHistory): Tick[] {
  const daily = h.range === "7d";
  const out: Tick[] = [];
  const d = new Date(h.from_ts * 1000);
  d.setHours(daily ? 0 : d.getHours(), 0, 0, 0);
  while (d.getTime() < h.to_ts * 1000) {
    const ts = d.getTime() / 1000;
    if (ts > h.from_ts && (daily || d.getHours() % 6 === 0)) {
      const at = ((ts - h.from_ts) / (h.to_ts - h.from_ts)) * 100;
      const label = d.getHours() === 0 ? fmtDay(ts) : fmtClock(ts);
      out.push({ at, label: at < EDGE_PCT || at > 100 - EDGE_PCT ? null : label });
    }
    if (daily) d.setDate(d.getDate() + 1);
    else d.setHours(d.getHours() + 1);
  }
  return out;
}

/** 차트 묶음 위에 한 번 두는 범례. 면은 상자, 선은 짧은 선으로 그린다. */
export function HistoryLegend() {
  return (
    <div className="legend" aria-label="이력 차트 범례">
      {SERIES.map((s) => (
        <span key={s.key} className="legend-item">
          <i className={`hkey ${s.key}`} />
          {s.label}
        </span>
      ))}
    </div>
  );
}

/** GPU 한 장의 차트. 마우스·터치로 가리키거나 포커스한 뒤 화살표 키로 칸을 옮기면 그 시각의 값이 툴팁에 뜬다. */
export function HistoryChart({ gpu, history }: { gpu: GpuSeries; history: NodeHistory }) {
  const plot = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<{ i: number; flip: boolean } | null>(null);
  const n = gpu.points.length;
  const drawn = useMemo(() => {
    const vals = gpu.points.map((p) => valuesOf(p, gpu.mem_total_mb));
    const of = (key: SeriesKey) => paths(vals.map((v) => v[key]));
    const utils = vals.flatMap((v) => (v.avg === null ? [] : [v.avg]));
    const mean = utils.length ? utils.reduce((a, b) => a + b, 0) / utils.length : null;
    return { vals, avg: of("avg"), max: of("max"), memory: of("memory"), mean };
  }, [gpu]);
  const marks = useMemo(() => ticks(history), [history]);
  // 터치로 띄운 툴팁은 손을 떼도 남는다. 가로로 끈 뒤에는 포커스가 없어 blur 가 오지 않으므로
  // 다른 곳을 누르면 닫는다. 세로 스크롤·두 손가락 확대처럼 브라우저가 가져간 터치는 pointercancel 로 닫는다.
  const open = hover !== null;
  useEffect(() => {
    if (!open) return;
    const away = (e: Event) => {
      if (!plot.current?.contains(e.target as Node)) setHover(null);
    };
    document.addEventListener("pointerdown", away, true);
    return () => document.removeEventListener("pointerdown", away, true);
  }, [open]);
  const grid = `M0 0H100M0 50H100M0 100H100${marks.map((t) => `M${t.at.toFixed(2)} 0V100`).join("")}`;

  /** 칸 i 를 가리킨다. 툴팁은 차트 왼쪽 절반이면 십자선 오른쪽, 오른쪽 절반이면 왼쪽에 띄우되
   *  그쪽 화면에 자리가 없으면 반대쪽으로 옮긴다 (맨 왼쪽·오른쪽 차트에서 화면 밖으로 나가지 않게). */
  function place(i: number) {
    const at = Math.max(0, Math.min(n - 1, i));
    const r = plot.current?.getBoundingClientRect();
    const x = r ? r.left + ((at + 0.5) / n) * r.width : 0; // 십자선의 화면 x
    const room = document.documentElement.clientWidth - x; // 십자선 오른쪽 자리
    const flip = (at + 0.5) / n > 0.5 ? x >= TIP_PX || x > room : room < TIP_PX && x > room;
    return { i: at, flip };
  }

  function pick(e: PointerEvent<HTMLDivElement>) {
    const r = e.currentTarget.getBoundingClientRect();
    setHover(place(Math.floor(((e.clientX - r.left) / r.width) * n)));
  }

  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    if (e.key === "Escape") return setHover(null);
    const at = hover?.i ?? n - 1;
    const step = e.shiftKey ? 12 : 1; // Shift 는 12칸씩 (24시간은 1시간, 7일은 12시간)
    const moves: Record<string, number> = { ArrowLeft: at - step, ArrowRight: at + step, Home: 0, End: n - 1 };
    if (!(e.key in moves)) return;
    e.preventDefault();
    setHover(place(moves[e.key]));
  }

  if (n === 0) return null;
  const p = hover === null ? null : gpu.points[hover.i];
  const v = hover === null ? null : drawn.vals[hover.i];
  const left = hover === null ? 0 : ((hover.i + 0.5) / n) * 100;
  const label = `[${gpu.index}] ${gpu.name} ${rangeLabel[history.range]} 이력, 평균 사용률 ${pct(drawn.mean)}`;

  return (
    <div className="hchart">
      <div className="hchart-head">
        <span className="idx">[{gpu.index}]</span>
        <span className="name" title={gpu.name}>
          {gpu.name}
        </span>
        <span className="sum" title="기간 평균 사용률 (수집된 구간만)">
          평균 {pct(drawn.mean)}
        </span>
      </div>
      <div className="hchart-body">
        <div className="hchart-y" aria-hidden="true">
          <span style={{ top: "0%" }}>100%</span>
          <span style={{ top: "50%" }}>50%</span>
          <span style={{ top: "100%" }}>0%</span>
        </div>
        <div
          ref={plot}
          className="hchart-plot"
          role="img"
          aria-label={`${label}. 화살표 키로 시각을 옮깁니다`}
          tabIndex={0}
          onPointerDown={pick}
          onPointerMove={pick}
          onPointerLeave={(e) => e.pointerType !== "touch" && setHover(null)}
          onPointerCancel={() => setHover(null)}
          onFocus={() => setHover((h) => h ?? place(n - 1))}
          onBlur={() => setHover(null)}
          onKeyDown={onKey}
        >
          <svg viewBox="0 0 100 100" preserveAspectRatio="none" aria-hidden="true">
            <path className="grid" d={grid} />
            <path className="area" d={drawn.avg.area} />
            <path className="memory" d={drawn.memory.line} />
            <path className="max" d={drawn.max.line} />
          </svg>
          {p && v && (
            <>
              <i className="hchart-cross" style={{ left: `${left}%` }} />
              {SERIES.map(({ key }) => {
                const value = v[key];
                if (value === null) return null;
                const style = { left: `${left}%`, top: `${100 - clamp(value)}%` };
                return <i key={key} className={`hchart-dot ${key}`} style={style} />;
              })}
              <div className={`hchart-tip${hover?.flip ? " flip" : ""}`} style={{ left: `${left}%` }}>
                <div className="when">
                  {fmtStamp(p.ts)}~{fmtClock(p.ts + history.step_s)}
                </div>
                {SERIES.map(({ key, tip }) => (
                  <div key={key}>
                    <i className={`hkey ${key}`} />
                    <b>{pct(v[key])}</b> {tip}
                    {key === "memory" && p.mem_avg_mb !== null && (
                      <span className="muted"> {fmtMemPair(Math.round(p.mem_avg_mb), gpu.mem_total_mb)}</span>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
        <div className={`hchart-x${history.range === "7d" ? " daily" : ""}`} aria-hidden="true">
          {marks.map(
            (t) =>
              t.label && (
                <span key={t.at} style={{ left: `${t.at}%` }}>
                  {t.label}
                </span>
              ),
          )}
        </div>
      </div>
    </div>
  );
}

/** 차트 묶음: 범례 한 번, GPU 마다 차트 하나. 기간을 바꾸는 동안(stale)은 앞 기간의 차트를 흐리게 둔다.
 *  차트는 기간마다 새로 만든다. 가리키던 칸 번호는 앞 기간 격자의 것이라 새 격자에서는 다른 시각이 된다. */
export function HistoryCharts({ history, stale }: { history: NodeHistory; stale: boolean }) {
  if (history.gpus.length === 0) return <div className="empty">GPU 가 없습니다.</div>;
  const empty = history.gpus.every((g) => g.points.every((p) => p.util_avg === null && p.mem_avg_mb === null));
  if (empty) {
    return <div className="empty">이 기간에 쌓인 이력이 없습니다. 서버가 5분마다 집계해 채웁니다.</div>;
  }
  return (
    <>
      <HistoryLegend />
      <div className={`hist-grid${stale ? " stale" : ""}`}>
        {history.gpus.map((g) => (
          <HistoryChart key={`${history.range}:${g.uuid}`} gpu={g} history={history} />
        ))}
      </div>
    </>
  );
}
