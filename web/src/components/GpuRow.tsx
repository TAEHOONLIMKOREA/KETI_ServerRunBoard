import { Link } from "react-router-dom";
import { fmtElapsed, fmtMemPair, fmtPct, sourceLabel, typeLabel } from "../format";
import type { Gpu, WorkloadOnGpu } from "../types";
import { UNKNOWN_USER } from "../types";
import { Badge, Badges } from "./Badges";
import { Bar } from "./Bar";

function OwnerLink({ w }: { w: WorkloadOnGpu }) {
  if (w.owner === null) {
    const fact = [w.exec_username, w.container_name ? `@ ${w.container_name}` : null]
      .filter(Boolean)
      .join(" ");
    return (
      <Link to={`/users/${UNKNOWN_USER}`} className="owner unknown" title={`미확인 · ${fact}`}>
        미확인
      </Link>
    );
  }
  const title = `${w.owner} · 근거: ${sourceLabel[w.owner_source] ?? w.owner_source}`;
  return (
    <Link to={`/users/${encodeURIComponent(w.owner)}`} className="owner" title={title}>
      {w.owner_display ?? w.owner}
    </Link>
  );
}

export function WorkloadLine({ w }: { w: WorkloadOnGpu }) {
  const span = w.gpu_count > 1 ? ` (GPU ${w.gpu_count}장)` : "";
  return (
    <div className="wl-item">
      {w.type === "shared_service" && <Badge text={typeLabel.shared_service} kind="type" />}
      <OwnerLink w={w} />
      <Link to={`/workloads/${w.workload_id}`} className="wname" title={w.cmdline}>
        {w.name}
        {span}
      </Link>
      <span className="elapsed">{fmtElapsed(w.elapsed_s)}</span>
      {w.status === "unknown" && <Badge text="미확인" />}
    </div>
  );
}

export function GpuRow({ gpu }: { gpu: Gpu }) {
  return (
    <div className="gpu-row">
      <span className="idx">[{gpu.index}]</span>
      <Bar pct={gpu.util_gpu} color={gpu.color} />
      <span className="num">{fmtPct(gpu.util_gpu)}</span>
      <span className="mem">{fmtMemPair(gpu.mem_used_mb, gpu.mem_total_mb)}</span>
      <div className="wl">
        {gpu.workloads.length === 0 ? (
          <span className="free">FREE</span>
        ) : (
          gpu.workloads.map((w) => <WorkloadLine key={w.workload_id} w={w} />)
        )}
        <Badges items={gpu.badges} />
      </div>
    </div>
  );
}
