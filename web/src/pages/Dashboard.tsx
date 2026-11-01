import { useMemo } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useOverview } from "../api";
import { Legend } from "../components/Legend";
import { NodeCard } from "../components/NodeCard";
import { ErrorBox, Loading } from "../components/Status";
import { FOLDED_KEY, PINNED_KEY, useStoredKeys } from "../prefs";
import { matchSearch, searchSuffix, useSearchQuery } from "../search";

/** 노드 카드 격자. 고정한 카드가 먼저 오고, 두 묶음 안에서는 서버가 준 순서(처음 본 순서)를 지킨다. */
export default function Dashboard() {
  const { data, error, isLoading } = useOverview();
  const [params] = useSearchParams();
  const q = useSearchQuery();
  const [pinned, togglePin] = useStoredKeys(PINNED_KEY);
  const [folded, toggleFold] = useStoredKeys(FOLDED_KEY);

  const cards = useMemo(() => {
    if (!data) return [];
    const shown = data.nodes
      .map((node) => ({ node, gpus: node.gpus.filter((g) => matchSearch(node, g, q)) }))
      .filter(({ node, gpus }) => gpus.length > 0 || (!q && node.gpus.length === 0));
    return [...shown.filter((c) => pinned.has(c.node.key)), ...shown.filter((c) => !pinned.has(c.node.key))];
  }, [data, q, pinned]);

  if (isLoading) return <Loading />;
  if (error) return <ErrorBox error={error} />;
  if (!data) return null;

  return (
    <>
      {data.nodes.length > 0 && (
        <Legend overview={data}>
          <Link to={{ pathname: "/gpus", search: searchSuffix(params) }} className="view-link">
            표로 보기
          </Link>
        </Legend>
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
            <NodeCard
              key={node.key}
              node={node}
              gpus={gpus}
              searching={q !== ""}
              pinned={pinned.has(node.key)}
              folded={folded.has(node.key)}
              onPin={() => togglePin(node.key)}
              onFold={() => toggleFold(node.key)}
            />
          ))}
        </div>
      )}
    </>
  );
}
