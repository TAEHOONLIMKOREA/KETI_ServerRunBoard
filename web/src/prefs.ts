import { useEffect, useState } from "react";

// 보는 사람별 화면 설정 (대시보드 카드 고정·접기). 이 브라우저의 localStorage 에만 두고 서버로 보내지 않는다.
// 저장소를 막은 브라우저에서는 localStorage 를 읽기만 해도 예외가 나므로 접근을 전부 감싼다.
// 그때는 쓰지 못한 값을 unsaved 에 두어 페이지를 새로 읽기 전까지 이 탭에서 기억한다 (다른 화면에 갔다 와도 남는다).
// 탭을 여러 개 열어도 서로의 설정을 지우지 않게, 바꿀 때는 저장된 목록에서 출발하고 다른 탭이 바꾸면 따라간다.

export const PINNED_KEY = "runboard.pinned";
export const FOLDED_KEY = "runboard.folded";

/** 저장소에 쓰지 못한 값. 저장에 성공하면 지운다 */
const unsaved = new Map<string, string[]>();

function load(storageKey: string): string[] {
  const kept = unsaved.get(storageKey);
  if (kept) return kept;
  try {
    const parsed: unknown = JSON.parse(window.localStorage.getItem(storageKey) ?? "[]");
    return Array.isArray(parsed) ? parsed.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function save(storageKey: string, keys: string[]): void {
  try {
    window.localStorage.setItem(storageKey, JSON.stringify(keys));
    unsaved.delete(storageKey);
  } catch {
    unsaved.set(storageKey, keys); // 저장소가 막혔거나 가득 찼다. 이 탭에서만 기억한다
  }
}

/** localStorage 에 배열로 두는 노드 key 집합. toggle 은 없으면 넣고 있으면 뺀다. */
export function useStoredKeys(storageKey: string): [ReadonlySet<string>, (key: string) => void] {
  const [keys, setKeys] = useState<ReadonlySet<string>>(() => new Set(load(storageKey)));

  // 다른 탭이 바꾸면 따라간다. storage 이벤트는 바꾼 탭이 아닌 다른 탭에만 오고, 전부 지우면 key 가 null 이다
  useEffect(() => {
    function onStorage(e: StorageEvent) {
      if (e.key === storageKey || e.key === null) setKeys(new Set(load(storageKey)));
    }
    window.addEventListener("storage", onStorage);
    return () => window.removeEventListener("storage", onStorage);
  }, [storageKey]);

  function toggle(key: string) {
    const next = new Set(load(storageKey)); // 화면에 든 값이 아니라 저장된 목록에서 출발한다
    if (!next.delete(key)) next.add(key);
    setKeys(next);
    save(storageKey, [...next]);
  }

  return [keys, toggle];
}
