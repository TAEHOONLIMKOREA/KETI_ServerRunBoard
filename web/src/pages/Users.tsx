import { Link } from "react-router-dom";
import { useUsers } from "../api";
import { ErrorBox, Loading } from "../components/Status";
import { Swatch } from "../components/Swatch";
import { userPaint } from "../colors";
import { fmtGb } from "../format";
import { UNKNOWN_USER } from "../types";

export default function Users() {
  const { data, error, isLoading } = useUsers();
  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <>
      <div className="controls">
        <h1 style={{ margin: 0 }}>사용자</h1>
      </div>
      <p className="note">
        현재 점유 기준입니다. 점유 시간과 GB·시간 통계는 작업 상세의 세션 표에서 볼 수 있습니다.
      </p>
      <table>
        <thead>
          <tr>
            <th>사용자</th>
            <th className="num">작업</th>
            <th className="num">GPU</th>
            <th className="num">메모리 점유</th>
            <th>노드</th>
            <th className="num">미확인 세션</th>
          </tr>
        </thead>
        <tbody>
          {data.users.map((u) => {
            const key = u.username ?? UNKNOWN_USER;
            return (
              <tr key={key}>
                <td>
                  <Swatch paint={userPaint(u)} />{" "}
                  <Link to={`/users/${encodeURIComponent(key)}`} className={u.username ? "" : "owner unknown"}>
                    {u.display_name}
                  </Link>
                  {u.username && u.username !== u.display_name && (
                    <span className="muted"> {u.username}</span>
                  )}
                  {u.username && !u.registered && <span className="badge"> 미등록</span>}
                </td>
                <td className="num">{u.workloads}</td>
                <td className="num">{u.gpus}</td>
                <td className="num">{fmtGb(u.mem_used_mb)}</td>
                <td>{u.nodes.join(", ") || "—"}</td>
                <td className="num">{u.unknown_sessions || "—"}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </>
  );
}
