// 대시보드 행 색. styles.css 의 --series-* 변수를 가리킨다.
// 사용자: 서버가 준 owner_slot(관리 화면의 사용자 순서, 앞 8명)으로 범주색. 그 밖의 사용자와 공용 서비스는 중립색.
// 소유자 미확인: 빗금. 빈 GPU: 초록(상태색). 색은 보조 수단이고 이름은 항상 같이 적는다.
import type { Gpu, Overview, User, WorkloadOnGpu } from "./types";

export const MAX_SLOTS = 8;

export type PaintKind = "user" | "neutral" | "service" | "unknown" | "free";
export interface Paint {
  kind: PaintKind;
  color: string; // CSS 색 값
}

const NEUTRAL = "var(--series-neutral)";
const FREE: Paint = { kind: "free", color: "var(--series-free)" };

export function slotColor(slot: number | null | undefined): string | null {
  if (slot === null || slot === undefined || slot < 0 || slot >= MAX_SLOTS) return null;
  return `var(--series-${slot + 1})`;
}

export function workloadPaint(w: Pick<WorkloadOnGpu, "type" | "owner" | "owner_slot">): Paint {
  if (w.type === "shared_service") return { kind: "service", color: NEUTRAL };
  if (w.owner === null) return { kind: "unknown", color: NEUTRAL };
  const color = slotColor(w.owner_slot);
  return color ? { kind: "user", color } : { kind: "neutral", color: NEUTRAL };
}

export function userPaint(u: Pick<User, "username" | "color_slot">): Paint {
  if (u.username === null) return { kind: "unknown", color: NEUTRAL };
  const color = slotColor(u.color_slot);
  return color ? { kind: "user", color } : { kind: "neutral", color: NEUTRAL };
}

/** GPU 행의 색을 정하는 작업. 사람이 쓰는 작업이 있으면 그것, 없으면 미확인, 그다음 공용 서비스. 빈 GPU 는 null. */
export function gpuOwner(gpu: Gpu): WorkloadOnGpu | null {
  if (gpu.workloads.length === 0) return null;
  const person = gpu.workloads.find((w) => w.type !== "shared_service" && w.owner !== null);
  return person ?? gpu.workloads.find((w) => w.owner === null) ?? gpu.workloads[0];
}

/** GPU 행 하나의 색 (gpuOwner 의 색). 작업이 없으면 빈 GPU. */
export function gpuPaint(gpu: Gpu): Paint {
  const owner = gpuOwner(gpu);
  return owner ? workloadPaint(owner) : FREE;
}

export interface LegendEntry {
  key: string;
  label: string;
  paint: Paint;
  slot: number | null;
}

/** 지금 화면에 있는 것만 범례에 올린다. 사용자는 색 번호 순, 빈 GPU 는 항상 마지막에. */
export function legendEntries(ov: Overview): LegendEntry[] {
  const users = new Map<string, LegendEntry>();
  let service = false;
  let unknown = false;
  for (const node of ov.nodes) {
    for (const gpu of node.gpus) {
      for (const w of gpu.workloads) {
        if (w.type === "shared_service") {
          service = true;
        } else if (w.owner === null) {
          unknown = true;
        } else if (!users.has(w.owner)) {
          users.set(w.owner, {
            key: `user:${w.owner}`,
            label: w.owner_display ?? w.owner,
            paint: workloadPaint(w),
            slot: w.owner_slot,
          });
        }
      }
    }
  }
  const out = [...users.values()].sort((a, b) => {
    const sa = a.slot ?? MAX_SLOTS;
    const sb = b.slot ?? MAX_SLOTS;
    return sa - sb || a.label.localeCompare(b.label, "ko");
  });
  if (service) out.push({ key: "service", label: "공용 서비스", paint: { kind: "service", color: NEUTRAL }, slot: null });
  if (unknown) out.push({ key: "unknown", label: "소유자 미확인", paint: { kind: "unknown", color: NEUTRAL }, slot: null });
  out.push({ key: "free", label: "빈 GPU", paint: FREE, slot: null });
  return out;
}
