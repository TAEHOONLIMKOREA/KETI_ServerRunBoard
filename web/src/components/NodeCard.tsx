import { Link } from "react-router-dom";
import { connLabel, fmtAgo, fmtMemPair, fmtPct } from "../format";
import type { Gpu, Node } from "../types";
import { GpuRow } from "./GpuRow";

export const INACTIVE_TEXT = "노드 비활성화 상태 (서버가 꺼졌거나 에이전트가 꺼져 있음)";

export function ConnDot({ status }: { status: Node["conn_status"] }) {
  return (
    <>
      <span className={`dot ${status}`} />
      {connLabel[status]}
    </>
  );
}

/** 노드 패널: 이름 크게, 서버 정보 요약 한 줄, 그 아래 GPU 행. 끊긴 노드는 흐리게 두고 비활성화 안내를 단다. */
export function NodeCard({ node, gpus }: { node: Node; gpus: Gpu[] }) {
  const model = node.gpus[0]?.name ?? "GPU 없음";
  const offline = node.conn_status === "offline";
  const cpu = node.cpu_percent === null ? null : Math.round(node.cpu_percent);
  return (
    <section className={`card ${offline ? "offline" : ""}`}>
      <div className="node-head">
        <Link to={`/nodes/${encodeURIComponent(node.name)}`} className="name">
          {node.name}
        </Link>
        <span className="meta">
          <ConnDot status={node.conn_status} />
        </span>
        <span className="right">마지막 보고 {fmtAgo(node.age_s)}</span>
      </div>
      {offline && <div className="inactive">{INACTIVE_TEXT}</div>}
      <div className="node-summary">
        <span>
          {model} ×{node.gpu_count}
          {node.gpu_count > 0 && ` · 빈 GPU ${node.free_gpu_count}`}
        </span>
        <span>IP {node.ip ?? "—"}</span>
        <span>
          드라이버 {node.driver_version ?? "—"} / CUDA {node.cuda_version ?? "—"}
        </span>
        <span>CPU {fmtPct(cpu)}</span>
        <span>RAM {node.mem_total_mb ? fmtMemPair(node.mem_used_mb, node.mem_total_mb) : "—"}</span>
        <span>에이전트 {node.agent_version}</span>
        {node.errors.length > 0 && <span className="badge warn">수집 오류 {node.errors.length}</span>}
        {node.clock_skew_flag && <span className="badge warn">시계 오차</span>}
      </div>
      {gpus.map((g) => (
        <GpuRow key={g.uuid} gpu={g} />
      ))}
    </section>
  );
}
