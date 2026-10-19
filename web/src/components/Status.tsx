import { useEffect, useState } from "react";

export function Loading() {
  return <div className="empty">불러오는 중…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="error-box">서버에서 데이터를 가져오지 못했습니다: {msg}</div>;
}

/** 마지막 갱신으로부터 흐른 시간을 1초마다 다시 그린다. */
export function RefreshedAgo({ updatedAt }: { updatedAt: number }) {
  const [, tick] = useState(0);
  useEffect(() => {
    const id = setInterval(() => tick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, []);
  if (!updatedAt) return null;
  const s = Math.max(0, Math.floor((Date.now() - updatedAt) / 1000));
  return <span className="refresh">갱신 {s}초 전</span>;
}
