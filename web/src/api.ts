import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import type {
  AdminUser,
  HistoryRange,
  Node,
  NodeHistory,
  NoticeEntry,
  NoticeFields,
  Overview,
  ServiceEntry,
  ServiceFields,
  UserDetail,
  UserFields,
  UsersOut,
  Workload,
  WorkloadsOut,
} from "./types";

export const REFRESH_MS = 1000;

async function getJson<T>(url: string): Promise<T> {
  const res = await fetch(url, { headers: { Accept: "application/json" } });
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText}`);
  }
  return (await res.json()) as T;
}

const live = { refetchInterval: REFRESH_MS, staleTime: 500, retry: 1 } as const;

export function useOverview() {
  return useQuery({ queryKey: ["overview"], queryFn: () => getJson<Overview>("/api/v1/overview"), ...live });
}

export function useNode(name: string) {
  return useQuery({
    queryKey: ["node", name],
    queryFn: () => getJson<Node>(`/api/v1/nodes/${encodeURIComponent(name)}`),
    ...live,
  });
}

export function useUsers() {
  return useQuery({ queryKey: ["users"], queryFn: () => getJson<UsersOut>("/api/v1/users"), ...live });
}

export function useUser(username: string) {
  return useQuery({
    queryKey: ["user", username],
    queryFn: () => getJson<UserDetail>(`/api/v1/users/${encodeURIComponent(username)}`),
    ...live,
  });
}

export function useWorkload(id: string) {
  return useQuery({
    queryKey: ["workload", id],
    queryFn: () => getJson<Workload>(`/api/v1/workloads/${encodeURIComponent(id)}`),
    ...live,
  });
}

export function useWorkloads(params: Record<string, string | undefined>) {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) {
    if (v) qs.set(k, v);
  }
  const suffix = qs.toString() ? `?${qs}` : "";
  return useQuery({
    queryKey: ["workloads", suffix],
    queryFn: () => getJson<WorkloadsOut>(`/api/v1/workloads${suffix}`),
    ...live,
  });
}

// 이력은 5분(7일은 1시간)마다 한 칸 늘어나므로 1분마다만 다시 읽는다.
export const HISTORY_REFRESH_MS = 60_000;

/** 노드의 GPU 별 이력. 기간을 바꾸는 동안은 앞 기간의 차트를 그대로 둔다 (isPlaceholderData). */
export function useNodeHistory(key: string, range: HistoryRange) {
  return useQuery({
    queryKey: ["node-history", key, range],
    queryFn: () => getJson<NodeHistory>(`/api/v1/nodes/${encodeURIComponent(key)}/history?range=${range}`),
    refetchInterval: HISTORY_REFRESH_MS,
    staleTime: HISTORY_REFRESH_MS / 2,
    placeholderData: keepPreviousData,
    retry: 1,
  });
}

// ---------------------------------------------------------------- 관리 (/admin)

const FIELD_LABEL: Record<string, string> = {
  username: "사용자 이름",
  display_name: "표시 이름",
  aliases: "별칭",
  email: "이메일",
  slack_id: "Slack ID",
  node: "노드",
  uid: "uid",
  name: "이름",
  match_exec_account: "실행 계정",
  match_container_pattern: "컨테이너명 패턴",
  match_image_pattern: "이미지 패턴",
  owner: "담당자",
  text: "내용",
  level: "수준",
  until: "표시 기한",
};

interface Issue {
  loc?: (string | number)[];
  msg?: string;
}

/** 서버 검증 메시지. 서버가 직접 쓴 문장(Value error)은 그대로, pydantic 기본 문장은 항목 이름을 앞에 붙인다. */
function issueText(issue: Issue): string {
  const msg = issue.msg ?? "";
  if (msg.startsWith("Value error, ")) return msg.slice("Value error, ".length);
  const field = issue.loc && issue.loc.length > 1 ? String(issue.loc[issue.loc.length - 1]) : "";
  return field ? `${FIELD_LABEL[field] ?? field}: ${msg}` : msg;
}

async function errorMessage(res: Response): Promise<string> {
  try {
    const body = (await res.json()) as { detail?: unknown };
    if (typeof body.detail === "string") return body.detail;
    if (Array.isArray(body.detail)) return (body.detail as Issue[]).map(issueText).join(" / ");
  } catch {
    // 본문이 JSON 이 아니다
  }
  return `${res.status} ${res.statusText}`;
}

async function send<T>(method: string, url: string, body?: unknown): Promise<T> {
  const res = await fetch(url, {
    method,
    headers:
      body === undefined
        ? { Accept: "application/json" }
        : { Accept: "application/json", "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  if (!res.ok) throw new Error(await errorMessage(res));
  return (res.status === 204 ? undefined : await res.json()) as T;
}

// 등록 정보는 관리 화면에서만 바뀌므로 1초 폴링하지 않는다. 고친 뒤에는 useAdminAction 이 전부 다시 읽는다.
export function useAdminUsers() {
  return useQuery({ queryKey: ["admin", "users"], queryFn: () => getJson<AdminUser[]>("/api/v1/admin/users") });
}

export function useServices() {
  return useQuery({ queryKey: ["admin", "services"], queryFn: () => getJson<ServiceEntry[]>("/api/v1/admin/services") });
}

/** 기한이 지난 것까지 전부. 배너에 보이는 것은 overview 의 notices 다. */
export function useNotices() {
  return useQuery({ queryKey: ["admin", "notices"], queryFn: () => getJson<NoticeEntry[]>("/api/v1/admin/notices") });
}

const ADMIN = "/api/v1/admin";
const enc = encodeURIComponent;

export const admin = {
  createUser: (user: AdminUser) => send<AdminUser>("POST", `${ADMIN}/users`, user),
  updateUser: (username: string, fields: UserFields) => send<AdminUser>("PUT", `${ADMIN}/users/${enc(username)}`, fields),
  deleteUser: (username: string) => send<void>("DELETE", `${ADMIN}/users/${enc(username)}`),
  reorderUsers: (usernames: string[]) => send<AdminUser[]>("PUT", `${ADMIN}/user-order`, { usernames }),
  createService: (fields: ServiceFields) => send<ServiceEntry>("POST", `${ADMIN}/services`, fields),
  updateService: (id: number, fields: ServiceFields) => send<ServiceEntry>("PUT", `${ADMIN}/services/${id}`, fields),
  deleteService: (id: number) => send<void>("DELETE", `${ADMIN}/services/${id}`),
  createNotice: (fields: NoticeFields) => send<NoticeEntry>("POST", `${ADMIN}/notices`, fields),
  updateNotice: (id: number, fields: NoticeFields) => send<NoticeEntry>("PUT", `${ADMIN}/notices/${id}`, fields),
  deleteNotice: (id: number) => send<void>("DELETE", `${ADMIN}/notices/${id}`),
  renameNode: (key: string, displayName: string) =>
    send<Node>("PATCH", `${ADMIN}/nodes/${enc(key)}`, { display_name: displayName }),
  deleteNode: (key: string) => send<void>("DELETE", `${ADMIN}/nodes/${enc(key)}`),
};

/** 관리 쓰기. 성공하면 대시보드까지 모든 조회를 다시 읽는다 (이름·색·소유자가 바뀐다). */
export function useAdminAction<V = void>(action: (vars: V) => Promise<unknown>) {
  const queryClient = useQueryClient();
  return useMutation({ mutationFn: action, onSuccess: () => queryClient.invalidateQueries() });
}
