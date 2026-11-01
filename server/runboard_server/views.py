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
        default=None, description="대시보드 색 번호 0~7 (등록 사용자 순서). 없으면 중립색"
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
    key: str = Field(description="노드 식별자. 에이전트 node_id, 없으면 노드 이름. API 경로에 쓴다")
    display_name: str = Field(description="화면 이름. 처음 보고한 순서대로 Server_n 을 자동 배정")
    hostname: str = Field(description="에이전트가 보고한 노드 이름(호스트명)")
    node_id: str | None = None
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


class NodeBrief(BaseModel):
    """관리용 노드 목록 한 줄 (GET /api/v1/admin/nodes, runboard-server nodes list). GPU 는 수만 센다."""

    key: str = Field(description="노드 식별자. 관리 API 경로에 쓴다")
    display_name: str
    hostname: str
    node_id: str | None
    seq: int | None = Field(description="처음 본 순서. 목록은 대시보드처럼 이 순서다")
    ip: str | None
    gpu_count: int
    conn_status: str
    last_received_ts: float
    age_s: float = Field(description="마지막 보고 뒤 지난 초 (서버 시계)")


class Summary(BaseModel):
    nodes: int
    nodes_offline: int
    gpus: int
    gpus_free: int
    gpus_unknown_owner: int
    workloads: int


class NoticeOut(BaseModel):
    """헤더 아래 공지 한 줄 (notices.py)."""

    id: int
    text: str
    level: str = Field(description="info(안내) | warning(주의)")
    until: float | None = Field(
        description="epoch 초. 이 시각이 지나면 내려간다. null 이면 지울 때까지"
    )


class OverviewOut(BaseModel):
    generated_ts: float
    summary: Summary
    nodes: list[NodeOut]
    notices: list[NoticeOut] = Field(
        description="기한이 지나지 않은 공지만. 주의가 안내보다 먼저, 같은 수준은 먼저 등록한 것부터"
    )


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
    node_display: str = Field(description="노드 화면 이름")
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
        default=None, description="대시보드 색 번호 0~7 (등록 사용자 순서). 없으면 중립색"
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


class HistoryPoint(BaseModel):
    """이력 한 점. ts 는 구간 시작(원본은 측정 시각)이다. 데이터가 없는 칸은 값이 전부 null 이다."""

    ts: float
    util_avg: float | None = Field(description="GPU 사용률 평균 %. 원본은 그 샘플 값")
    util_max: int | None
    mem_avg_mb: float | None
    mem_max_mb: int | None
    temp_max: int | None
    power_avg: float | None
    occupied_ratio: float | None = Field(
        description="열린 세션(작업)이 있던 샘플의 비율 0~1. 원본은 0 또는 1"
    )


class GpuHistoryOut(BaseModel):
    gpu_uuid: str
    step: str = Field(description="raw | 5m | 1h")
    points: list[HistoryPoint] = Field(description="데이터가 있는 점만, 시각 순")


class GpuSeries(BaseModel):
    uuid: str
    index: int
    name: str
    mem_total_mb: int
    points: list[HistoryPoint]


class NodeHistoryOut(BaseModel):
    key: str
    display_name: str
    range: str = Field(description="24h | 7d")
    step_s: int = Field(description="점 간격(초). 24h 는 300 (288점), 7d 는 3600 (168점)")
    from_ts: float = Field(description="첫 점의 구간 시작")
    to_ts: float = Field(description="마지막 점의 구간 끝. 집계가 끝난(닫힌) 구간까지만 담는다")
    gpus: list[GpuSeries] = Field(
        description="gpus 표의 GPU, index 순 (같으면 uuid 순). 점은 빈 칸 없이 고정 격자"
    )
