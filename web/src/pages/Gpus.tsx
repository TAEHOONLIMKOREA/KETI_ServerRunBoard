import { type CSSProperties, useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useOverview } from "../api";
import { gpuOwner, gpuPaint } from "../colors";
import { Badges } from "../components/Badges";
import { Bar, MemBar } from "../components/Bar";
import { WorkloadLine } from "../components/GpuRow";
import { SortIcon } from "../components/Icons";
import { Legend } from "../components/Legend";
import { ConnDot } from "../components/NodeCard";
import { ErrorBox, Loading } from "../components/Status";
import { fmtMemPair, fmtPct } from "../format";
import { matchSearch, searchSuffix, useSearchQuery } from "../search";
import type { Gpu, Node } from "../types";

// 전체 GPU 한 표 (/gpus). 새 API 없이 overview 를 펼친다 (헤더와 같은 1초 폴링 하나를 같이 쓴다).
// 기본 순서는 overview 그대로 노드를 처음 본 순서, 그 안에서 GPU 번호 순이다 (카드 고정은 대시보드에만 쓴다).
// 열 머리를 누르면 정렬하고 상태는 URL ?sort=<열> (오름차순) / ?sort=-<열> (내림차순) 에 둔다.
// 같은 값은 기본 순서를 지키고 값이 없는 행(null)은 방향과 상관없이 맨 뒤다.

interface Row {
  node: Node;
  gpu: Gpu;
  order: number; // 기본 순서
}

interface Column {
  key: string; // ?sort= 에 쓰는 이름
  label: string;
  num?: boolean; // 오른쪽 정렬
  value: (r: Row) => number | string | null;
}

/** 소유자 열의 정렬 값: 행 색을 정한 작업의 소유자 이름 (판정 못 했으면 "미확인"). 빈 GPU 는 null. */
function ownerText(gpu: Gpu): string | null {
  const w = gpuOwner(gpu);
  if (!w) return null;
  return w.owner === null ? "미확인" : (w.owner_display ?? w.owner);
}

const COLUMNS: Column[] = [
  { key: "node", label: "노드", value: (r) => r.node.display_name },
  { key: "index", label: "#", value: (r) => r.gpu.index },
  { key: "model", label: "모델", value: (r) => r.gpu.name },
  { key: "util", label: "사용률", num: true, value: (r) => r.gpu.util_gpu },
  { key: "mem", label: "메모리", num: true, value: (r) => r.gpu.mem_used_mb },
  { key: "temp", label: "온도", num: true, value: (r) => r.gpu.temp_c },
  { key: "power", label: "전력", num: true, value: (r) => r.gpu.power_w },
  { key: "owner", label: "소유자·작업", value: (r) => ownerText(r.gpu) },
  { key: "badges", label: "뱃지", value: (r) => r.gpu.badges.join(" ") || null },
];

interface Sort {
  column: Column;
  desc: boolean;
}

function parseSort(raw: string | null): Sort | null {
  if (!raw) return null;
  const desc = raw.startsWith("-");
  const column = COLUMNS.find((c) => c.key === (desc ? raw.slice(1) : raw));
  return column ? { column, desc } : null;
}

const collator = new Intl.Collator("ko", { numeric: true }); // Server_2 가 Server_10 보다 앞

function sortRows(rows: Row[], sort: Sort | null): Row[] {
  if (!sort) return rows;
  const { column, desc } = sort;
  return [...rows].sort((x, y) => {
    const a = column.value(x);
    const b = column.value(y);
    if (a === null || b === null) {
      if (a !== b) return a === null ? 1 : -1;
    } else {
      const c = typeof a === "number" && typeof b === "number" ? a - b : collator.compare(String(a), String(b));
      if (c !== 0) return desc ? -c : c;
    }
    return x.order - y.order;
  });
}

/** "150 / 300 W". 한도를 모르면 "150 W" */
function Power({ gpu }: { gpu: Gpu }) {
  if (gpu.power_w === null) return <>—</>;
  const limit = gpu.power_limit_w === null ? "" : ` / ${gpu.power_limit_w.toFixed(0)}`;
  return (
    <>
      {gpu.power_w.toFixed(0)}
      <span className="muted">{limit} W</span>
    </>
  );
}

/** 한 행. 색은 대시보드 GPU 행과 같은 규칙(gpuPaint)으로 칠하고 끊긴 노드의 행은 흐리게 둔다. */
function GpuTableRow({ node, gpu }: { node: Node; gpu: Gpu }) {
  const paint = gpuPaint(gpu);
  const style = { "--row": paint.color } as CSSProperties;
  const cls = [paint.kind, node.conn_status === "offline" && "offline"].filter(Boolean).join(" ");
  return (
    <tr className={cls} style={style}>
      <td>
        <Link to={`/nodes/${encodeURIComponent(node.key)}`}>{node.display_name}</Link>
        {node.conn_status !== "online" && (
          <span className="meta">
            <ConnDot status={node.conn_status} />
          </span>
        )}
      </td>
      <td className="mono">[{gpu.index}]</td>
      <td>
        {gpu.name}
        {gpu.mig_enabled && <span className="badge">MIG</span>}
      </td>
      <td className="num">
        <div className="meter">
          <Bar pct={gpu.util_gpu} />
          <span>{fmtPct(gpu.util_gpu)}</span>
        </div>
      </td>
      <td className="num">
        <div className="meter mem">
          <MemBar used={gpu.mem_used_mb} total={gpu.mem_total_mb} />
          <span>{fmtMemPair(gpu.mem_used_mb, gpu.mem_total_mb)}</span>
        </div>
      </td>
      <td className={gpu.health === "temp_warn" ? "num temp warn" : "num temp"}>
        {gpu.temp_c === null ? "—" : `${gpu.temp_c}°C`}
      </td>
      <td className="num">
        <Power gpu={gpu} />
      </td>
      <td>
        <div className="wl">
          {gpu.workloads.length === 0 ? (
            <span className="free-pill">FREE</span>
          ) : (
            gpu.workloads.map((w) => <WorkloadLine key={w.workload_id} w={w} />)
          )}
        </div>
      </td>
      <td>
        <Badges items={gpu.badges} />
      </td>
    </tr>
  );
}

export default function Gpus() {
  const { data, error, isLoading } = useOverview();
  const [params, setParams] = useSearchParams();
  const q = useSearchQuery();
  const sortParam = params.get("sort");
  const sort = useMemo(() => parseSort(sortParam), [sortParam]);

  const all = useMemo(() => {
    const out: Row[] = [];
    for (const node of data?.nodes ?? []) {
      for (const gpu of node.gpus) out.push({ node, gpu, order: out.length });
    }
    return out;
  }, [data]);
  const rows = useMemo(() => sortRows(all.filter((r) => matchSearch(r.node, r.gpu, q)), sort), [all, q, sort]);

  function setSort(next: string | null) {
    setParams(
      (prev) => {
        if (next) prev.set("sort", next);
        else prev.delete("sort");
        return prev;
      },
      { replace: true },
    );
  }

  /** 정렬 중인 열을 다시 누르면 방향을 바꾸고, 다른 열은 오름차순부터. */
  function toggle(column: Column) {
    setSort(sort?.column === column && !sort.desc ? `-${column.key}` : column.key);
  }

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  const free = all.filter((r) => r.gpu.workloads.length === 0).length;
  return (
    <>
      <div className="controls">
        <h1 style={{ margin: 0 }}>전체 GPU</h1>
        <span className="muted">
          {q
            ? `검색 결과 ${rows.length}장 / 전체 ${all.length}장`
            : `노드 ${data.nodes.length}대 · GPU ${all.length}장 · 빈 GPU ${free}`}
        </span>
        {sort && (
          <button type="button" className="btn small" onClick={() => setSort(null)}>
            기본 순서
          </button>
        )}
      </div>
      {data.nodes.length > 0 && (
        <Legend overview={data}>
          <Link to={{ pathname: "/", search: searchSuffix(params) }} className="view-link">
            카드로 보기
          </Link>
        </Legend>
      )}

      {rows.length === 0 ? (
        <div className="empty">
          {data.nodes.length === 0
            ? "아직 보고한 노드가 없습니다. 에이전트를 실행하세요."
            : q
              ? "검색에 맞는 GPU 가 없습니다."
              : "GPU 가 없습니다."}
        </div>
      ) : (
        <div className="table-wrap">
          <table className="gpu-table">
            <thead>
              <tr>
                {COLUMNS.map((c) => {
                  const active = sort?.column === c;
                  const dir = active ? (sort.desc ? "descending" : "ascending") : undefined;
                  return (
                    <th key={c.key} className={c.num ? "num sortable" : "sortable"} aria-sort={dir}>
                      <button type="button" className={active ? "active" : undefined} onClick={() => toggle(c)}>
                        {c.label}
                        <SortIcon desc={active && sort.desc} />
                      </button>
                    </th>
                  );
                })}
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <GpuTableRow key={r.gpu.uuid} node={r.node} gpu={r.gpu} />
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
