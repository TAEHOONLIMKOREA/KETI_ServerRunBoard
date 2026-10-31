import type { ReactNode } from "react";

/** 관리 폼의 칸 하나: 라벨, 입력, 도움말. wide 면 폼 한 줄을 다 쓴다. */
export function Field({
  label,
  hint,
  wide,
  children,
}: {
  label: string;
  hint?: string;
  wide?: boolean;
  children: ReactNode;
}) {
  return (
    <label className={wide ? "field wide" : "field"}>
      <span>{label}</span>
      {children}
      {hint && <small>{hint}</small>}
    </label>
  );
}

export function FormError({ error }: { error: unknown }) {
  if (!error) return null;
  return <div className="form-error">{error instanceof Error ? error.message : String(error)}</div>;
}

/** 폼의 빈 칸은 null 로 보낸다. 서버도 공백을 지우지만 화면에서 먼저 맞춰 둔다. */
export function blank(value: string): string | null {
  const v = value.trim();
  return v ? v : null;
}

/** "th, taehoon2" 처럼 쉼표나 공백으로 나눈 목록. */
export function splitList(value: string): string[] {
  return value
    .split(/[,\s]+/)
    .map((s) => s.trim())
    .filter(Boolean);
}
