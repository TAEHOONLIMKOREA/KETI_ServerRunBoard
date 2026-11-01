import type { ReactNode } from "react";

// 버튼 안의 작은 아이콘 (인라인 SVG, 16 칸 격자). 색은 글자색(currentColor)을 따르고 이름은 버튼이 aria-label 로 단다.

function Icon({ children }: { children: ReactNode }) {
  return (
    <svg
      className="icon"
      viewBox="0 0 16 16"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      focusable="false"
    >
      {children}
    </svg>
  );
}

/** 압정. 고정하면 속을 채운다. */
export function PinIcon({ filled }: { filled: boolean }) {
  return (
    <Icon>
      <path d="M6 2.5h4M6.75 2.5v3.75L4.5 9.25h7L9.25 6.25V2.5Z" fill={filled ? "currentColor" : "none"} />
      <path d="M8 9.25v4.5" />
    </Icon>
  );
}

/** 접기 꺾쇠. 펼친 카드는 위(접기), 접힌 카드는 아래(펼치기). */
export function FoldIcon({ folded }: { folded: boolean }) {
  return <Icon>{folded ? <path d="M4 6.25l4 4 4-4" /> : <path d="M4 9.75l4-4 4 4" />}</Icon>;
}

/** 정렬 방향 화살표. 오름차순은 위, 내림차순은 아래. */
export function SortIcon({ desc }: { desc: boolean }) {
  return <Icon>{desc ? <path d="M8 3v10M4.5 9.5 8 13l3.5-3.5" /> : <path d="M8 13V3M4.5 6.5 8 3l3.5 3.5" />}</Icon>;
}
