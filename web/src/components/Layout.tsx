import { Link, Outlet, useNavigate, useSearchParams } from "react-router-dom";
import { useOverview } from "../api";

/** 상단 헤더 한 줄: 제목, 검색창, 연결된 노드 수. 검색어는 URL ?q= 로 대시보드에 전달한다. */
export default function Layout() {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const { data } = useOverview();
  const q = params.get("q") ?? "";
  const connected = data ? data.summary.nodes - data.summary.nodes_offline : null;

  function onSearch(value: string) {
    const search = value.trim() ? `?q=${encodeURIComponent(value)}` : "";
    navigate({ pathname: "/", search }, { replace: true });
  }

  return (
    <div className="container">
      <header className="topbar">
        <Link to="/" className="brand">
          KETI Server RunBoard
        </Link>
        <input
          className="search"
          placeholder="노드, 사용자, 작업, 컨테이너 검색"
          value={q}
          onChange={(e) => onSearch(e.target.value)}
        />
        <span className="nodecount" title="정상·지연 노드 / 알려진 노드 전체">
          연결된 노드 <b>{connected ?? "—"}</b>
          {data && <span className="muted"> / {data.summary.nodes}</span>}
        </span>
      </header>
      <Outlet />
    </div>
  );
}
