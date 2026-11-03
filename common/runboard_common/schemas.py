"""RunBoard 에이전트 ↔ 서버 보고 스키마.

에이전트가 POST /api/v1/report 로 보내는 보고 JSON 을 pydantic 모델로 정의한다 (README_DEV.md '6. API').
에이전트는 보내기 전에, 서버는 받은 뒤에 같은 모델로 검증한다.

원칙:
- 에이전트는 사실만 보낸다. 소유자 판정은 서버가 한다.
- 수집할 수 없는 값은 0 이 아니라 None 이다. 왜 못 얻었는지는 errors 에 남긴다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

SCHEMA_VERSION = 1

# 에이전트의 Docker 소켓 중계(docker-proxy)가 새 컨테이너에 붙이는 실행 계정 라벨.
# 이 라벨이 있으면 서버는 컨테이너 이름으로 소유자를 짐작하지 않는다
LAUNCHER_LABEL = "runboard.launcher"

ProcessType = Literal["C", "G", "C+G"]


class ContainerInfo(BaseModel):
    """프로세스가 속한 컨테이너. 에이전트가 cgroup 과 Docker 조회로 채운다."""

    id: str = Field(min_length=12, description="컨테이너 ID (전체 또는 12자 이상 접두)")
    name: str | None = None
    image: str | None = None
    labels: dict[str, str] = Field(default_factory=dict)


class ProcessReport(BaseModel):
    """GPU 를 점유 중인 프로세스 하나. 호스트 PID 네임스페이스 기준."""

    pid: int = Field(ge=1)
    ppid: int | None = None
    pgid: int | None = Field(default=None, description="프로세스 그룹 ID. 작업 묶음 키")
    type: ProcessType = "C"
    mem_used_mb: int | None = Field(default=None, ge=0, description="None = 수집 불가")
    util_sm: int | None = Field(default=None, ge=0, le=100, description="None = 수집 불가")
    exec_username: str | None = Field(default=None, description="실행 계정. 사실이며 소유자가 아님")
    exec_uid: int | None = None
    cmdline: str = Field(default="", max_length=200, description="마스킹·절단된 명령어")
    proc_start_ts: float | None = Field(default=None, description="프로세스 시작 시각 (epoch)")
    container: ContainerInfo | None = None


class GpuReport(BaseModel):
    index: int = Field(ge=0)
    uuid: str = Field(min_length=1)
    name: str
    mem_total_mb: int = Field(ge=0)
    mem_used_mb: int | None = Field(default=None, ge=0)
    util_gpu: int | None = Field(default=None, ge=0, le=100)
    util_mem: int | None = Field(default=None, ge=0, le=100)
    temp_c: int | None = None
    power_w: float | None = None
    power_limit_w: float | None = None
    mig_enabled: bool = False
    processes: list[ProcessReport] = Field(default_factory=list)


class NodeInfo(BaseModel):
    name: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._-]+$")
    node_id: str | None = Field(
        default=None,
        max_length=64,
        pattern=r"^[A-Za-z0-9._:-]+$",
        description="재부팅·재설치 뒤에도 같은 노드임을 알리는 고정 식별자 (설치 때 만든 UUID 등). 없으면 name 이 식별자",
    )
    ip: str | None = None
    boot_id: str = Field(min_length=1, description="재부팅마다 바뀌는 식별자")
    driver_version: str | None = None
    cuda_version: str | None = None
    agent_version: str
    measured_ts: float = Field(description="에이전트가 수집한 시각 (epoch)")
    report_interval_s: float | None = Field(
        default=None,
        gt=0,
        description="다음 보고까지의 최대 간격(초). 동작 중 1초, 유휴 30초. 서버는 연결 판정에 더한다",
    )
    cpu_percent: float | None = None
    mem_used_mb: int | None = None
    mem_total_mb: int | None = None


class ReportError(BaseModel):
    """수집 실패·미지원 항목. UI 에서 '수집 불가' 와 건강 축 판정에 쓴다."""

    scope: str = Field(description='"node", "gpu:<index>", "gpu:<index>/pid:<pid>"')
    item: str = Field(description='"nvml_init", "process_util", "container", ...')
    code: str = Field(description='"NOT_SUPPORTED", "NO_PERMISSION", "ERROR"')
    message: str | None = None


class NodeReport(BaseModel):
    """에이전트가 보내는 보고 1건. 항상 노드의 최신 상태 전체다.

    주기는 적응형이다: GPU 동작 중 1초, 유휴 30초 (에이전트 설정). 그 값을 node.report_interval_s 에 적는다.
    """

    schema_version: int = SCHEMA_VERSION
    node: NodeInfo
    gpus: list[GpuReport] = Field(default_factory=list)
    errors: list[ReportError] = Field(default_factory=list)
