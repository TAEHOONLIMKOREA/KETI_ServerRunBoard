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
  key: string;
  display_name: string;
  hostname: string;
  node_id: string | null;
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

/** 관리용 노드 목록 한 줄 (/api/v1/admin/nodes, runboard-server nodes list). GPU 는 수만. */
export interface NodeBrief {
  key: string;
  display_name: string;
  hostname: string;
  node_id: string | null;
  seq: number | null; // 처음 본 순서. 목록은 이 순서
  ip: string | null;
  gpu_count: number;
  conn_status: ConnStatus;
  last_received_ts: number;
  age_s: number;
}

export interface Summary {
  nodes: number;
  nodes_offline: number;
  gpus: number;
  gpus_free: number;
  gpus_unknown_owner: number;
  workloads: number;
}

export type NoticeLevel = "info" | "warning";

/** 헤더 아래 공지 한 줄. */
export interface Notice {
  id: number;
  text: string;
  level: NoticeLevel;
  until: number | null; // epoch 초. 지나면 내려간다. null 이면 지울 때까지
}

export interface Overview {
  generated_ts: number;
  summary: Summary;
  nodes: Node[];
  notices: Notice[]; // 기한 전인 것만. 주의가 먼저, 같은 수준은 먼저 등록한 것부터
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
  node_display: string;
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

// 이력 (/api/v1/nodes/{key}/history, /api/v1/gpus/{uuid}/history)

export type HistoryRange = "24h" | "7d";
export type HistoryStep = "raw" | "5m" | "1h";

/** ts 는 구간 시작(원본은 측정 시각). 데이터가 없는 칸은 값이 전부 null 이다. */
export interface HistoryPoint {
  ts: number;
  util_avg: number | null;
  util_max: number | null;
  mem_avg_mb: number | null;
  mem_max_mb: number | null;
  temp_max: number | null;
  power_avg: number | null;
  occupied_ratio: number | null; // 열린 세션이 있던 샘플의 비율 0~1
}

export interface GpuHistory {
  gpu_uuid: string;
  step: HistoryStep;
  points: HistoryPoint[]; // 데이터가 있는 점만, 시각 순
}

export interface GpuSeries {
  uuid: string;
  index: number;
  name: string;
  mem_total_mb: number;
  points: HistoryPoint[];
}

export interface NodeHistory {
  key: string;
  display_name: string;
  range: HistoryRange;
  step_s: number; // 24h 는 300 (288점), 7d 는 3600 (168점)
  from_ts: number; // 첫 점의 구간 시작
  to_ts: number; // 마지막 점의 구간 끝. 집계가 끝난 구간까지만
  gpus: GpuSeries[]; // index 순. 점은 빈 칸 없는 고정 격자
}

// 관리 API (/api/v1/admin). server/runboard_server/registry.py 와 맞춘다.

export interface UserFields {
  display_name: string | null;
  email: string | null;
  slack_id: string | null;
  aliases: string[];
}

export interface AdminUser extends UserFields {
  username: string;
}

export interface MappingFields {
  node: string | null; // 노드 식별자·호스트명·화면 이름. null 이면 모든 노드
  uid: number;
  username: string;
}

export interface AccountMapping extends MappingFields {
  id: number;
}

export interface ServiceFields {
  name: string;
  match_exec_account: string | null;
  match_container_pattern: string | null;
  match_image_pattern: string | null;
  owner: string | null;
  idle_exempt: boolean;
}

export interface ServiceEntry extends ServiceFields {
  id: number;
}

// 공지 (/api/v1/admin/notices). server/runboard_server/notices.py 와 맞춘다.

export interface NoticeFields {
  text: string; // 한 줄, 200자 이하
  level: NoticeLevel;
  until: number | null; // epoch 초. 지난 시각이면 바로 내려간다
}

export interface NoticeEntry extends NoticeFields {
  id: number;
  created_ts: number; // 등록 시각. 고쳐도 바뀌지 않는다
}
