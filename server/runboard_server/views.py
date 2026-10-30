"""API 응답 모델. 엔진이 만들고 라우터가 그대로 돌려준다. 프론트는 이 모양에 의존한다."""

from __future__ import annotations

from pydantic import BaseModel, Field

from runboard_common.schemas import ReportError


class WorkloadOnGpu(BaseModel):
    """GPU 행 하나에 표시되는 작업 요약."""

    workload_id: str
    name: str
    type: str
    owner: str | None
    owner_display: str | None
    owner_source: str
    owner_slot: int | None = Field(
        default=None, description="대시보드 색 번호 0~7 (users 순서). 없으면 중립색"
    )
    service_name: str | None = None
    project: str | None = None
    status: str
    started_ts: float
    elapsed_s: float
    mem_used_mb: int | None
    util_sm: int | None
    gpu_count: int = Field(description="이 작업이 걸쳐 있는 GPU 수")
    pids: list[int]
    exec_username: str | None
    container_name: str | None
    cmdline: str


class GpuOut(BaseModel):
    index: int
    uuid: str
    name: str
    mem_total_mb: int
    mem_used_mb: int | None
    util_gpu: int | None
    util_mem: int | None
    temp_c: int | None
    power_w: float | None
    power_limit_w: float | None
    mig_enabled: bool
    occupancy: str
    activity: str
    health: str
    color: str
    badges: list[str]
    workloads: list[WorkloadOnGpu]


class NodeOut(BaseModel):
    name: str
    ip: str | None
    conn_status: str
    last_received_ts: float
    last_measured_ts: float
    age_s: float
    report_interval_s: float | None = Field(
        default=None, description="에이전트가 보고에 적은 다음 보고까지의 최대 간격(초)"
    )
    boot_id: str
    driver_version: str | None
    cuda_version: str | None
    agent_version: str
    cpu_percent: float | None
    mem_used_mb: int | None
    mem_total_mb: int | None
    clock_skew_s: float
    clock_skew_flag: bool
    errors: list[ReportError]
    gpu_count: int
    free_gpu_count: int
    gpus: list[GpuOut]


class Summary(BaseModel):
    nodes: int
    nodes_offline: int
    gpus: int
    gpus_free: int
    gpus_unknown_owner: int
    workloads: int


class OverviewOut(BaseModel):
    generated_ts: float
    summary: Summary
    nodes: list[NodeOut]


class SessionOut(BaseModel):
    id: str
    node: str
    gpu_uuid: str
    gpu_index: int | None
    pid: int
    proc_start_ts: float | None
    exec_username: str | None
    exec_uid: int | None
    cmdline: str
    proc_type: str
    status: str
    start_ts: float
    last_seen_ts: float
    end_ts: float | None
    ended_reason: str | None
    unknown_seconds: float
    mem_used_mb: int | None
    mem_max_mb: int
    mem_gb_hours: float
    util_sm: int | None
    util_avg: float | None


class WorkloadOut(BaseModel):
    id: str
    key: str
    node: str
    type: str
    name: str
    owner: str | None
    owner_display: str | None
    owner_source: str
    service_name: str | None
    project: str | None
    job: str | None
    container_id: str | None
    container_name: str | None
    image: str | None
    status: str
    first_seen: float
    last_seen: float
    started_ts: float
    elapsed_s: float
    gpu_uuids: list[str]
    gpu_indexes: list[int]
    sessions: list[SessionOut]


class WorkloadsOut(BaseModel):
    generated_ts: float
    workloads: list[WorkloadOut]


class UserOut(BaseModel):
    username: str | None = Field(description="None 이면 미확인 묶음")
    display_name: str
    registered: bool
    color_slot: int | None = Field(
        default=None, description="대시보드 색 번호 0~7 (users 순서). 없으면 중립색"
    )
    workloads: int
    gpus: int
    mem_used_mb: int
    nodes: list[str]
    unknown_sessions: int


class UsersOut(BaseModel):
    generated_ts: float
    users: list[UserOut]


class UserDetailOut(BaseModel):
    generated_ts: float
    user: UserOut
    workloads: list[WorkloadOut]
