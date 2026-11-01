import { useSearchParams } from "react-router-dom";
import type { Gpu, Node } from "./types";

// 헤더 검색 (URL ?q=). 대시보드 카드와 전체 GPU 표가 같은 규칙으로 GPU 를 거른다.

/** 비교용 검색어. 앞뒤 공백을 빼고 소문자로. 없으면 "" */
export function useSearchQuery(): string {
  const [params] = useSearchParams();
  return (params.get("q") ?? "").trim().toLowerCase();
}

/** 다른 보기로 갈 때 붙이는 검색어 ("?q=…", 없으면 ""). 정렬 같은 그 화면만의 값은 빼고 검색어만 넘긴다. */
export function searchSuffix(params: URLSearchParams): string {
  const q = params.get("q") ?? "";
  return q.trim() ? `?q=${encodeURIComponent(q)}` : "";
}

/** 노드 이름·호스트명·식별자, 그 GPU 의 작업 이름·소유자·실행 계정·컨테이너·프로젝트·명령어에서 찾는다. */
export function matchSearch(node: Node, g: Gpu, q: string): boolean {
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
