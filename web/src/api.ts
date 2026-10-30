import { useQuery } from "@tanstack/react-query";
import type { Node, Overview, UserDetail, UsersOut, Workload, WorkloadsOut } from "./types";

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
