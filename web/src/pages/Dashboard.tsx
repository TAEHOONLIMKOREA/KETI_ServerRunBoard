import { useMemo, useState } from "react";
import { useOverview } from "../api";
import { legendEntries } from "../colors";
import { NodeCard } from "../components/NodeCard";
import { ErrorBox, Loading } from "../components/Status";
import { Swatch } from "../components/Swatch";
import type { Gpu, Node } from "../types";

function matchSearch(node: Node, g: Gpu, q: string): boolean {
  if (!q) return true;
  const hay = [
    node.name,
    ...g.workloads.flatMap((w) => [
      w.name,
      w.owner,
      w.owner_display,
      w.exec_username,
      w.container_name,
      w.project,
      w.cmdline,
    ]),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return hay.includes(q);
}

export default function Dashboard() {
  const { data, error, isLoading } = useOverview();
  const [search, setSearch] = useState("");
  const q = search.trim().toLowerCase();

  const cards = useMemo(() => {
    if (!data) return [];
    return data.nodes
      .map((node) => ({ node, gpus: node.gpus.filter((g) => matchSearch(node, g, q)) }))
      .filter(({ node, gpus }) => gpus.length > 0 || (!q && node.gpus.length === 0));
  }, [data, q]);
  const legend = useMemo(() => (data ? legendEntries(data) : []), [data]);

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;
  const s = data.summary;

  return (
    <>
      <div className="controls">
        <input
          className="search"
          placeholder="노드, 사용자, 작업, 컨테이너 검색"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      <div className="summary">
        <div className={`stat ${s.nodes_offline ? "warn" : ""}`}>
          <b>
            {s.nodes}
            {s.nodes_offline > 0 && <small> (끊김 {s.nodes_offline})</small>}
          </b>
          <span>노드</span>
        </div>
        <div className="stat">
          <b>{s.gpus}</b>
          <span>GPU</span>
        </div>
        <div className="stat">
          <b>{s.gpus_free}</b>
          <span>빈 GPU</span>
        </div>
        <div className="stat">
          <b>{s.workloads}</b>
          <span>작업</span>
        </div>
        <div className={`stat ${s.gpus_unknown_owner ? "warn" : ""}`}>
          <b>{s.gpus_unknown_owner}</b>
          <span>소유자 미확인 GPU</span>
        </div>
      </div>

      {data.nodes.length > 0 && (
        <div className="legend" aria-label="행 색 범례">
          {legend.map((e) => (
            <span key={e.key} className="legend-item">
              <Swatch paint={e.paint} />
              <b>{e.label}</b>
            </span>
          ))}
        </div>
      )}

      {cards.length === 0 ? (
        <div className="empty">
          {data.nodes.length === 0
            ? "아직 보고한 노드가 없습니다. 에이전트를 실행하세요."
            : "검색에 맞는 GPU 가 없습니다."}
        </div>
      ) : (
        <div className="node-grid">
          {cards.map(({ node, gpus }) => (
            <NodeCard key={node.name} node={node} gpus={gpus} />
          ))}
        </div>
      )}
    </>
  );
}
