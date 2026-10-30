import { useMemo } from "react";
import { useSearchParams } from "react-router-dom";
import { useOverview } from "../api";
import { legendEntries } from "../colors";
import { NodeCard } from "../components/NodeCard";
import { ErrorBox, Loading } from "../components/Status";
import { Swatch } from "../components/Swatch";
import type { Gpu, Node } from "../types";

function matchSearch(node: Node, g: Gpu, q: string): boolean {
  if (!q) return true;
  const hay = [
    node.display_name,
    node.hostname,
    node.key,
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
  const [params] = useSearchParams();
  const q = (params.get("q") ?? "").trim().toLowerCase();

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

  return (
    <>
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
            <NodeCard key={node.key} node={node} gpus={gpus} />
          ))}
        </div>
      )}
    </>
  );
}
