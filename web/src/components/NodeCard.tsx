import { Link } from "react-router-dom";
import { connLabel, fmtAgo } from "../format";
import type { Gpu, Node } from "../types";
import { GpuRow } from "./GpuRow";

export function ConnDot({ status }: { status: Node["conn_status"] }) {
  return (
    <>
      <span className={`dot ${status}`} />
      {connLabel[status]}
    </>
  );
}

export function NodeCard({ node, gpus }: { node: Node; gpus: Gpu[] }) {
  const model = node.gpus[0]?.name ?? "GPU 없음";
  const offline = node.conn_status === "offline";
  return (
    <section className={`card ${offline ? "offline" : ""}`}>
      <div className="node-head">
        <Link to={`/nodes/${encodeURIComponent(node.name)}`} className="name">
          {node.name}
        </Link>
        <span className="meta">
          <ConnDot status={node.conn_status} />
        </span>
        <span className="meta">
          {model} ×{node.gpu_count}
        </span>
        {node.driver_version && <span className="meta">driver {node.driver_version}</span>}
        {node.errors.length > 0 && <span className="badge warn">수집 오류 {node.errors.length}</span>}
        {node.clock_skew_flag && <span className="badge warn">시계 오차</span>}
        <span className="right" title={`마지막 보고 ${fmtAgo(node.age_s)}`}>
          마지막 보고 {fmtAgo(node.age_s)}
        </span>
      </div>
      {gpus.map((g) => (
        <GpuRow key={g.uuid} gpu={g} />
      ))}
    </section>
  );
}
