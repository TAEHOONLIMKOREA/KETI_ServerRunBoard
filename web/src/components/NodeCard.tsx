import { Link } from "react-router-dom";
import { connLabel, fmtAgo, fmtMemPair, fmtPct } from "../format";
import type { Gpu, Node } from "../types";
import { GpuRow } from "./GpuRow";
import { FoldIcon, PinIcon } from "./Icons";

export const INACTIVE_TEXT = "노드 비활성화 상태 (서버가 꺼졌거나 에이전트가 꺼져 있음)";

export function ConnDot({ status }: { status: Node["conn_status"] }) {
  return (
    <>
      <span className={`dot ${status}`} />
      {connLabel[status]}
    </>
  );
}

/** 접힌 카드의 요약 한 줄: GPU 수, 사용 중·빈 GPU, 평균 사용률. 검색 중이면 맞는 GPU 수를 덧붙인다. */
function FoldedSummary({ node, matched }: { node: Node; matched: number | null }) {
  const utils = node.gpus.flatMap((g) => (g.util_gpu === null ? [] : [g.util_gpu]));
  const avg = utils.length ? Math.round(utils.reduce((a, b) => a + b, 0) / utils.length) : null;
  return (
    <div className="node-summary">
      {node.gpu_count === 0 ? (
        <span>GPU 없음</span>
      ) : (
        <>
          <span>GPU {node.gpu_count}장</span>
          <span>사용 중 {node.gpu_count - node.free_gpu_count}</span>
          <span>빈 GPU {node.free_gpu_count}</span>
          <span>평균 사용률 {fmtPct(avg)}</span>
        </>
      )}
      {matched !== null && <span>검색 결과 {matched}장</span>}
    </div>
  );
}

interface CardProps {
  node: Node;
  gpus: Gpu[]; // 검색에 맞는 GPU 만
  searching: boolean;
  pinned: boolean;
  folded: boolean;
  onPin: () => void;
  onFold: () => void;
}

/**
 * 노드 패널: 이름 크게, 서버 정보 요약 한 줄, 그 아래 GPU 행. 끊긴 노드는 흐리게 두고 비활성화 안내를 단다.
 * 머리 오른쪽의 접기·고정 버튼은 보는 사람별 설정이다 (prefs.ts). 접힌 카드는 머리와 요약 한 줄만 그린다.
 */
export function NodeCard({ node, gpus, searching, pinned, folded, onPin, onFold }: CardProps) {
  const model = node.gpus[0]?.name ?? "GPU 없음";
  const offline = node.conn_status === "offline";
  const cpu = node.cpu_percent === null ? null : Math.round(node.cpu_percent);
  const cls = ["card", offline && "offline", folded && "folded"].filter(Boolean).join(" ");
  return (
    <section className={cls}>
      <div className="node-head">
        <Link to={`/nodes/${encodeURIComponent(node.key)}`} className="name">
          {node.display_name}
        </Link>
        <span className="meta">
          <ConnDot status={node.conn_status} />
        </span>
        <span className="right">
          마지막 보고 {fmtAgo(node.age_s)}
          <span className="card-tools">
            <button
              type="button"
              className="icon-btn"
              aria-pressed={folded}
              aria-label={`${node.display_name} 접기`}
              title={folded ? "펼치기" : "접기"}
              onClick={onFold}
            >
              <FoldIcon folded={folded} />
            </button>
            <button
              type="button"
              className="icon-btn pin"
              aria-pressed={pinned}
              aria-label={`${node.display_name} 고정`}
              title={pinned ? "고정 풀기" : "맨 앞에 고정"}
              onClick={onPin}
            >
              <PinIcon filled={pinned} />
            </button>
          </span>
        </span>
      </div>
      {folded ? (
        <FoldedSummary node={node} matched={searching ? gpus.length : null} />
      ) : (
        <>
          {offline && <div className="inactive">{INACTIVE_TEXT}</div>}
          <div className="node-summary">
            <span>
              {model} ×{node.gpu_count}
              {node.gpu_count > 0 && ` · 빈 GPU ${node.free_gpu_count}`}
            </span>
            <span>호스트명 {node.hostname}</span>
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
        </>
      )}
    </section>
  );
}
