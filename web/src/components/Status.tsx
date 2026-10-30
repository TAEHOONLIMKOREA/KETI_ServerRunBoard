export function Loading() {
  return <div className="empty">불러오는 중…</div>;
}

export function ErrorBox({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return <div className="error-box">서버에서 데이터를 가져오지 못했습니다: {msg}</div>;
}
