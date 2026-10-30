import { Link } from "react-router-dom";
import {
  endedReasonLabel,
  fmtElapsed,
  fmtGb,
  fmtGbHours,
  fmtPct,
  fmtTs,
  sourceLabel,
  statusLabel,
  typeLabel,
} from "../format";
import type { Session, Workload } from "../types";
import { UNKNOWN_USER } from "../types";

export function OwnerCell({ w }: { w: Pick<Workload, "owner" | "owner_display" | "owner_source"> }) {
  if (w.owner === null) {
    return (
      <Link to={`/users/${UNKNOWN_USER}`} className="owner unknown">
        미확인
      </Link>
    );
  }
  return (
    <>
      <Link to={`/users/${encodeURIComponent(w.owner)}`}>{w.owner_display ?? w.owner}</Link>{" "}
      <span className="muted">({sourceLabel[w.owner_source] ?? w.owner_source})</span>
    </>
  );
}

export function WorkloadTable({ workloads, showNode = true }: { workloads: Workload[]; showNode?: boolean }) {
  if (workloads.length === 0) return <div className="empty">작업이 없습니다.</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>작업</th>
          <th>유형</th>
          <th>소유자</th>
          {showNode && <th>노드</th>}
          <th>GPU</th>
          <th>상태</th>
          <th className="num">경과</th>
          <th className="num">메모리</th>
          <th>명령어</th>
        </tr>
      </thead>
      <tbody>
        {workloads.map((w) => {
          const mem = w.sessions.reduce((a, s) => a + (s.mem_used_mb ?? 0), 0);
          const head = w.sessions[0];
          return (
            <tr key={w.id}>
              <td>
                <Link to={`/workloads/${w.id}`}>{w.name}</Link>
                {w.project && <div className="muted">{w.project}</div>}
              </td>
              <td>{typeLabel[w.type]}</td>
              <td>
                <OwnerCell w={w} />
              </td>
              {showNode && (
                <td>
                  <Link to={`/nodes/${encodeURIComponent(w.node)}`}>{w.node_display}</Link>
                </td>
              )}
              <td className="mono">{w.gpu_indexes.map((i) => `[${i}]`).join(" ")}</td>
              <td>{statusLabel[w.status] ?? w.status}</td>
              <td className="num">{fmtElapsed(w.elapsed_s)}</td>
              <td className="num">{fmtGb(mem)}</td>
              <td className="mono truncate" title={head?.cmdline}>
                {head?.cmdline}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}

export function SessionTable({ sessions }: { sessions: Session[] }) {
  if (sessions.length === 0) return <div className="empty">세션이 없습니다.</div>;
  return (
    <table>
      <thead>
        <tr>
          <th>GPU</th>
          <th className="num">PID</th>
          <th>실행 계정</th>
          <th>상태</th>
          <th>시작</th>
          <th className="num">메모리</th>
          <th className="num">최대</th>
          <th className="num">GB·시간</th>
          <th className="num">사용률</th>
          <th className="num">평균</th>
          <th className="num">미확인</th>
          <th>명령어</th>
        </tr>
      </thead>
      <tbody>
        {sessions.map((s) => (
          <tr key={s.id}>
            <td className="mono">{s.gpu_index === null ? s.gpu_uuid : `[${s.gpu_index}]`}</td>
            <td className="num mono">{s.pid}</td>
            <td>
              {s.exec_username ?? "—"}
              {s.exec_uid !== null && <span className="muted"> ({s.exec_uid})</span>}
            </td>
            <td>
              {statusLabel[s.status] ?? s.status}
              {s.ended_reason && <div className="muted">{endedReasonLabel[s.ended_reason] ?? s.ended_reason}</div>}
            </td>
            <td title={`첫 관측 ${fmtTs(s.start_ts)}`}>{fmtTs(s.proc_start_ts ?? s.start_ts)}</td>
            <td className="num">{fmtGb(s.mem_used_mb)}</td>
            <td className="num">{fmtGb(s.mem_max_mb)}</td>
            <td className="num">{fmtGbHours(s.mem_gb_hours)}</td>
            <td className="num">{fmtPct(s.util_sm)}</td>
            <td className="num">{s.util_avg === null ? "—" : `${s.util_avg.toFixed(0)}%`}</td>
            <td className="num">{s.unknown_seconds > 0 ? fmtElapsed(s.unknown_seconds) : "—"}</td>
            <td className="mono truncate" title={s.cmdline}>
              {s.cmdline}
            </td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
