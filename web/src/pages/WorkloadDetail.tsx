import { Link, useParams } from "react-router-dom";
import { useWorkload } from "../api";
import { ErrorBox, Loading } from "../components/Status";
import { OwnerCell, SessionTable } from "../components/Tables";
import { fmtElapsed, fmtTs, statusLabel, typeLabel } from "../format";

export default function WorkloadDetail() {
  const { id = "" } = useParams();
  const { data: w, error, isLoading } = useWorkload(id);
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!w) return null;

  const gbHours = w.sessions.reduce((a, s) => a + s.mem_gb_hours, 0);

  return (
    <>
      <div className="controls">
        <h1 style={{ margin: 0 }}>{w.name}</h1>
        <span className="badge type">{typeLabel[w.type]}</span>
        <span className="muted">{statusLabel[w.status] ?? w.status}</span>
      </div>

      <section className="card">
        <div className="kv">
          <div>
            <span>소유자</span> <OwnerCell w={w} />
          </div>
          <div>
            <span>노드</span> <Link to={`/nodes/${encodeURIComponent(w.node)}`}>{w.node_display}</Link>
          </div>
          <div>
            <span>GPU</span> <span className="mono">{w.gpu_indexes.map((i) => `[${i}]`).join(" ") || "—"}</span>
          </div>
          <div>
            <span>시작</span> {fmtTs(w.started_ts)} ({fmtElapsed(w.elapsed_s)})
          </div>
          <div>
            <span>프로젝트 / 작업 라벨</span> {w.project ?? "—"} / {w.job ?? "—"}
          </div>
          {w.service_name && (
            <div>
              <span>서비스</span> {w.service_name}
            </div>
          )}
          {w.container_name && (
            <div>
              <span>컨테이너</span> {w.container_name}{" "}
              <span className="mono muted">{w.container_id?.slice(0, 12)}</span>
            </div>
          )}
          {w.image && (
            <div>
              <span>이미지</span> <span className="mono">{w.image}</span>
            </div>
          )}
          <div>
            <span>메모리 점유량 합계</span> {gbHours.toFixed(1)} GB·시간
          </div>
        </div>
      </section>

      <h2>세션</h2>
      <p className="note">
        세션은 프로세스 하나가 GPU 하나를 점유한 관측 구간입니다. 점유 시간은 연산 시간이 아니며, 미확인
        구간은 GB·시간에 넣지 않습니다.
      </p>
      <SessionTable sessions={w.sessions} />
    </>
  );
}
