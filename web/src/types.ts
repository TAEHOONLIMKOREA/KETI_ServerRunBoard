// server/runboard_server/views.py 와 1:1 로 맞춘다.

export type ConnStatus = "online" | "delayed" | "offline";
export type Occupancy = "free" | "occupied" | "service";
export type Activity = "active" | "low" | "unknown";
export type Health = "ok" | "temp_warn" | "collect_error";
export type Color = "gray" | "green" | "blue" | "yellow" | "red" | "orange";
export type WorkloadType = "personal" | "container" | "shared_service";
export type WorkloadStatus = "active" | "unknown" | "ended";

export interface ReportError {
  scope: string;
  item: string;
  code: string;
  message: string | null;
}

export interface WorkloadOnGpu {
  workload_id: string;
  name: string;
  type: WorkloadType;
  owner: string | null;
  owner_display: string | null;
  owner_source: string;
  owner_slot: number | null;
  service_name: string | null;
  project: string | null;
  status: WorkloadStatus;
  started_ts: number;
  elapsed_s: number;
  mem_used_mb: number | null;
  util_sm: number | null;
  gpu_count: number;
  pids: number[];
  exec_username: string | null;
  container_name: string | null;
  cmdline: string;
}

export interface Gpu {
  index: number;
  uuid: string;
  name: string;
  mem_total_mb: number;
  mem_used_mb: number | null;
  util_gpu: number | null;
  util_mem: number | null;
  temp_c: number | null;
  power_w: number | null;
  power_limit_w: number | null;
  mig_enabled: boolean;
  occupancy: Occupancy;
  activity: Activity;
  health: Health;
  color: Color;
  badges: string[];
  workloads: WorkloadOnGpu[];
}

export interface Node {
  name: string;
  ip: string | null;
  conn_status: ConnStatus;
  last_received_ts: number;
  last_measured_ts: number;
  age_s: number;
  report_interval_s: number | null;
  boot_id: string;
  driver_version: string | null;
  cuda_version: string | null;
  agent_version: string;
  cpu_percent: number | null;
  mem_used_mb: number | null;
  mem_total_mb: number | null;
  clock_skew_s: number;
  clock_skew_flag: boolean;
  errors: ReportError[];
  gpu_count: number;
  free_gpu_count: number;
  gpus: Gpu[];
}

export interface Summary {
  nodes: number;
  nodes_offline: number;
  gpus: number;
  gpus_free: number;
  gpus_unknown_owner: number;
  workloads: number;
}

export interface Overview {
  generated_ts: number;
  summary: Summary;
  nodes: Node[];
}

export interface Session {
  id: string;
  node: string;
  gpu_uuid: string;
  gpu_index: number | null;
  pid: number;
  proc_start_ts: number | null;
  exec_username: string | null;
  exec_uid: number | null;
  cmdline: string;
  proc_type: string;
  status: WorkloadStatus;
  start_ts: number;
  last_seen_ts: number;
  end_ts: number | null;
  ended_reason: string | null;
  unknown_seconds: number;
  mem_used_mb: number | null;
  mem_max_mb: number;
  mem_gb_hours: number;
  util_sm: number | null;
  util_avg: number | null;
}

export interface Workload {
  id: string;
  key: string;
  node: string;
  type: WorkloadType;
  name: string;
  owner: string | null;
  owner_display: string | null;
  owner_source: string;
  service_name: string | null;
  project: string | null;
  job: string | null;
  container_id: string | null;
  container_name: string | null;
  image: string | null;
  status: WorkloadStatus;
  first_seen: number;
  last_seen: number;
  started_ts: number;
  elapsed_s: number;
  gpu_uuids: string[];
  gpu_indexes: number[];
  sessions: Session[];
}

export interface WorkloadsOut {
  generated_ts: number;
  workloads: Workload[];
}

export interface User {
  username: string | null;
  display_name: string;
  registered: boolean;
  color_slot: number | null;
  workloads: number;
  gpus: number;
  mem_used_mb: number;
  nodes: string[];
  unknown_sessions: number;
}

export interface UsersOut {
  generated_ts: number;
  users: User[];
}

export interface UserDetail {
  generated_ts: number;
  user: User;
  workloads: Workload[];
}

export const UNKNOWN_USER = "_unknown";
