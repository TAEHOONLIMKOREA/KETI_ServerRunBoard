import { useParams } from "react-router-dom";
import { useUser } from "../api";
import { ErrorBox, Loading } from "../components/Status";
import { WorkloadTable } from "../components/Tables";
import { fmtGb } from "../format";

export default function UserDetail() {
  const { username = "" } = useParams();
  const { data, error, isLoading } = useUser(username);
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const u = data.user;

  return (
    <>
      <div className="controls">
        <h1 style={{ margin: 0 }}>{u.display_name}</h1>
        {u.username && <span className="muted">{u.username}</span>}
      </div>
      {u.username === null && (
        <p className="note">
          소유자를 판정하지 못한 작업입니다. 컨테이너에 <span className="mono">runboard.owner</span> 라벨을
          붙이거나, 컨테이너명을 <span className="mono">&lt;user&gt;_&lt;용도&gt;</span> 로 짓거나, 관리자에게
          계정 매핑을 요청하세요.
        </p>
      )}
      <div className="summary">
        <div className="stat">
          <b>{u.workloads}</b>
          <span>작업</span>
        </div>
        <div className="stat">
          <b>{u.gpus}</b>
          <span>GPU</span>
        </div>
        <div className="stat">
          <b>{fmtGb(u.mem_used_mb)}</b>
          <span>메모리 점유</span>
        </div>
        <div className={`stat ${u.unknown_sessions ? "warn" : ""}`}>
          <b>{u.unknown_sessions}</b>
          <span>미확인 세션</span>
        </div>
      </div>
      <WorkloadTable workloads={data.workloads} />
    </>
  );
}
