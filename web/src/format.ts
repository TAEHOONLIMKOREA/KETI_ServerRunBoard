import type { ConnStatus, HistoryRange, NoticeLevel, WorkloadType } from "./types";

export function fmtElapsed(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const d = Math.floor(s / 86400);
  const h = Math.floor((s % 86400) / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (d > 0) return `${d}d ${h}h`;
  if (h > 0) return `${h}h ${m}m`;
  if (m > 0) return `${m}m`;
  return `${s}s`;
}

export function fmtAgo(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  if (s < 60) return `${s}초 전`;
  if (s < 3600) return `${Math.floor(s / 60)}분 전`;
  if (s < 86400) return `${Math.floor(s / 3600)}시간 전`;
  return `${Math.floor(s / 86400)}일 전`;
}

export function fmtGb(mb: number | null | undefined, digits = 1): string {
  if (mb === null || mb === undefined) return "—";
  return `${(mb / 1024).toFixed(digits)} GB`;
}

export function fmtMemPair(used: number | null, total: number): string {
  const u = used === null ? "—" : (used / 1024).toFixed(0);
  return `${u} / ${(total / 1024).toFixed(0)} GB`;
}

export function fmtPct(v: number | null | undefined): string {
  return v === null || v === undefined ? "—" : `${v}%`;
}

export function fmtTs(ts: number | null | undefined): string {
  if (!ts) return "—";
  return new Date(ts * 1000).toLocaleString("ko-KR", { hour12: false });
}

export function fmtGbHours(v: number): string {
  return `${v.toFixed(v >= 100 ? 0 : 1)} GB·h`;
}

// 이력 차트의 시각
const clockFormat = new Intl.DateTimeFormat("ko-KR", { hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
const dayFormat = new Intl.DateTimeFormat("ko-KR", { month: "numeric", day: "numeric" });
const stampFormat = new Intl.DateTimeFormat("ko-KR", {
  month: "numeric",
  day: "numeric",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  hourCycle: "h23",
});

/** "14:05" */
export function fmtClock(ts: number): string {
  return clockFormat.format(ts * 1000);
}

/** "11. 2." */
export function fmtDay(ts: number): string {
  return dayFormat.format(ts * 1000);
}

/** "11. 2. (월) 14:05" */
export function fmtStamp(ts: number): string {
  return stampFormat.format(ts * 1000);
}

export const rangeLabel: Record<HistoryRange, string> = {
  "24h": "24시간",
  "7d": "7일",
};

export const typeLabel: Record<WorkloadType, string> = {
  personal: "개인",
  container: "컨테이너",
  shared_service: "공용 서비스",
};

export const connLabel: Record<ConnStatus, string> = {
  online: "정상",
  delayed: "지연",
  offline: "비활성화",
};

export const noticeLevelLabel: Record<NoticeLevel, string> = {
  info: "안내",
  warning: "주의",
};

export const sourceLabel: Record<string, string> = {
  label: "라벨",
  container_rule: "컨테이너명 규칙",
  service: "서비스 담당자",
  account_map: "계정 매핑",
  none: "미확인",
};

export const statusLabel: Record<string, string> = {
  active: "실행 중",
  unknown: "미확인",
  ended: "종료",
};

export const endedReasonLabel: Record<string, string> = {
  exited: "종료됨",
  lost_while_offline: "끊김 중 사라짐",
  node_rebooted: "노드 재부팅",
  node_offline_timeout: "끊김 24시간 초과",
  node_removed: "노드 삭제",
};

export const WARN_BADGES = new Set(["유휴 의심", "온도", "수집 오류", "미확인", "미확인 소유자", "지연"]);
