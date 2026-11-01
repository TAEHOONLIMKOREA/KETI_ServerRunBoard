import { Link, useParams, useSearchParams } from "react-router-dom";
import { useNode, useNodeHistory, useWorkloads } from "../api";
import { Badges } from "../components/Badges";
import { HistoryCharts } from "../components/HistoryChart";
import { ConnDot, INACTIVE_TEXT } from "../components/NodeCard";
import { ErrorBox, Loading } from "../components/Status";
import { WorkloadTable } from "../components/Tables";
import { fmtAgo, fmtGb, fmtMemPair, fmtPct, fmtStamp, rangeLabel } from "../format";
import type { HistoryRange } from "../types";

const OCC: Record<string, string> = { free: "비어 있음", occupied: "사용 중", service: "서비스 상주" };
const ACT: Record<string, string> = { active: "연산 중", low: "낮은 활동", unknown: "수집 불가" };
const HLTH: Record<string, string> = { ok: "정상", temp_warn: "온도 경고", collect_error: "수집 오류" };
const RANGES: HistoryRange[] = ["24h", "7d"];

/** GPU 별 이력 차트. 기간은 URL ?range= 에 두어 주소로 공유한다. */
function HistorySection({ nodeKey }: { nodeKey: string }) {
  const [params, setParams] = useSearchParams();
  const range = RANGES.find((r) => r === params.get("range")) ?? RANGES[0];
  const { data, error, isPlaceholderData } = useNodeHistory(nodeKey, range);
  const step = data && (data.step_s >= 3600 ? `${data.step_s / 3600}시간` : `${data.step_s / 60}분`);

  function select(next: HistoryRange) {
    setParams(
      (prev) => {
        prev.set("range", next);
        return prev;
      },
      { replace: true },
    );
  }

  return (
    <>
      <div className="hist-head">
        <h2>이력</h2>
        <div className="seg" role="group" aria-label="이력 기간">
          {RANGES.map((r) => (
            <button key={r} type="button" aria-pressed={r === range} onClick={() => select(r)}>
              {rangeLabel[r]}
            </button>
          ))}
        </div>
        {data && (
          <span className="muted">
            {step} 단위 · {fmtStamp(data.to_ts)} 까지
          </span>
        )}
      </div>
      {error ? (
        <ErrorBox error={error} />
      ) : data ? (
        <HistoryCharts history={data} stale={isPlaceholderData} />
      ) : (
        <Loading />
      )}
    </>
  );
}

export default function NodeDetail() {
  const { name: key = "" } = useParams(); // 경로의 :name 은 노드 식별자(key)
  const node = useNode(key);
  const workloads = useWorkloads({ node: key });

  if (node.isLoading) return <Loading />;
  if (node.error) return <ErrorBox error={node.error} />;
  const n = node.data;
  if (!n) return null;

  return (
    <>
      <div className="controls">
        <h1 style={{ margin: 0 }}>{n.display_name}</h1>
        <span className="muted">{n.hostname}</span>
        <span className="meta">
          <ConnDot status={n.conn_status} />
        </span>
        <span className="muted">마지막 보고 {fmtAgo(n.age_s)}</span>
      </div>
      {n.conn_status === "offline" && <div className="inactive">{INACTIVE_TEXT}</div>}

      <section className="card">
        <div className="kv">
          <div>
            <span>호스트명</span> {n.hostname}
          </div>
          <div>
            <span>식별자</span> <span className="mono">{n.key}</span>
          </div>
          <div>
            <span>IP</span> {n.ip ?? "—"}
          </div>
          <div>
            <span>드라이버</span> {n.driver_version ?? "—"} / CUDA {n.cuda_version ?? "—"}
          </div>
          <div>
            <span>에이전트</span> {n.agent_version}
          </div>
          <div>
            <span>CPU</span> {fmtPct(n.cpu_percent === null ? null : Math.round(n.cpu_percent))}
          </div>
          <div>
            <span>RAM</span> {n.mem_total_mb ? fmtMemPair(n.mem_used_mb, n.mem_total_mb) : "—"}
          </div>
          <div>
            <span>부팅 ID</span> <span className="mono">{n.boot_id.slice(0, 8)}</span>
          </div>
          <div>
            <span>시계 오차</span> {n.clock_skew_s.toFixed(1)}s{n.clock_skew_flag && " (수신 시각 사용)"}
          </div>
          <div>
            <span>보고 주기</span> {n.report_interval_s === null ? "—" : `${n.report_interval_s}초`}
          </div>
        </div>
      </section>

      {n.errors.length > 0 && (
        <div className="error-box">
          <b>수집 오류·미지원 항목</b>
          <ul style={{ margin: "4px 0 0 18px" }}>
            {n.errors.map((e, i) => (
              <li key={i} className="mono">
                {e.scope} {e.item}: {e.code}
                {e.message ? ` — ${e.message}` : ""}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="section-head">
        <h2>GPU</h2>
        <Link to="/gpus" className="view-link">
          전체 GPU 표
        </Link>
      </div>
      <div className="table-wrap one-line">
        <table>
          <thead>
            <tr>
              <th>#</th>
              <th>모델</th>
              <th className="num">사용률</th>
              <th className="num">메모리</th>
              <th className="num">온도</th>
              <th className="num">전력</th>
              <th>점유</th>
              <th>활동</th>
              <th>건강</th>
              <th>작업</th>
            </tr>
          </thead>
          <tbody>
            {n.gpus.map((g) => (
              <tr key={g.uuid}>
                <td className="mono">[{g.index}]</td>
                <td>
                  {g.name}
                  {g.mig_enabled && <span className="badge">MIG</span>}
                </td>
                <td className="num">{fmtPct(g.util_gpu)}</td>
                <td className="num">{fmtMemPair(g.mem_used_mb, g.mem_total_mb)}</td>
                <td className="num">{g.temp_c === null ? "—" : `${g.temp_c}°C`}</td>
                <td className="num">
                  {g.power_w === null ? "—" : `${g.power_w.toFixed(0)}`}
                  {g.power_limit_w !== null && <span className="muted"> / {g.power_limit_w.toFixed(0)} W</span>}
                </td>
                <td>{OCC[g.occupancy]}</td>
                <td>{ACT[g.activity]}</td>
                <td>{HLTH[g.health]}</td>
                <td>
                  {g.workloads.map((w) => (
                    <div key={w.workload_id}>
                      {w.owner_display ?? "미확인"} · {w.name}
                      {w.mem_used_mb !== null && <span className="muted"> {fmtGb(w.mem_used_mb)}</span>}
                    </div>
                  ))}
                  <Badges items={g.badges} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h2>작업</h2>
      {workloads.error ? (
        <ErrorBox error={workloads.error} />
      ) : (
        <WorkloadTable workloads={workloads.data?.workloads ?? []} showNode={false} />
      )}

      <HistorySection key={n.key} nodeKey={n.key} />
    </>
  );
}
