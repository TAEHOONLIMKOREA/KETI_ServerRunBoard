import { Link, NavLink, Outlet, useLocation, useNavigate, useSearchParams } from "react-router-dom";
import { useOverview } from "../api";
import { NoticeBanner } from "./Notices";

/**
 * 상단 헤더 한 줄: 제목, 검색창, 연결된 노드 수, 관리 링크. 검색어는 URL ?q= 로 대시보드에 전달한다.
 * 전체 GPU 표(/gpus)에서는 그 표를 그 자리에서 거른다 (정렬은 그대로).
 * 그 아래 공지 배너는 헤더가 이미 읽는 overview 로 그린다 (요청을 더 하지 않는다).
 */
export default function Layout() {
  const [params, setParams] = useSearchParams();
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const { data } = useOverview();
  const q = params.get("q") ?? "";
  const connected = data ? data.summary.nodes - data.summary.nodes_offline : null;

  function onSearch(value: string) {
    if (pathname === "/gpus") {
      setParams(
        (prev) => {
          if (value.trim()) prev.set("q", value);
          else prev.delete("q");
          return prev;
        },
        { replace: true },
      );
      return;
    }
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
        <NavLink to="/admin" className="navlink">
          관리
        </NavLink>
      </header>
      <NoticeBanner notices={data?.notices ?? []} />
      <Outlet />
    </div>
  );
}
