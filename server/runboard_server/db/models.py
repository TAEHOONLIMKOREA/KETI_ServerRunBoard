"""SQLAlchemy 모델 (PLAN 5.1). MVP 는 시작 시 create_all, Alembic 은 v0.2.

PLAN 과 다른 점: 작업·세션 ID 는 정수 autoincrement 대신 uuid4 hex 문자열이다.
메모리에서 먼저 만들고 DB 왕복 없이 연결하기 위해서다.
"""

from __future__ import annotations

from sqlalchemy import (
    JSON,
    Boolean,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class Clipped(TypeDecorator):
    """길이를 넘는 문자열을 잘라서 쓰는 VARCHAR(n).

    PostgreSQL 은 길이를 넘는 값이 하나라도 있으면 그 트랜잭션 전체를 거부한다.
    컨테이너 이름·이미지·라벨처럼 에이전트가 길이 제한 없이 보내는 표시용 열에만 쓴다. 식별자 열은 자르지 않는다.
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return value[: self.impl.length] if value else value


class NodeRow(Base):
    __tablename__ = "nodes"

    name: Mapped[str] = mapped_column(
        String(64), primary_key=True
    )  # 식별자: 에이전트 node_id, 없으면 노드 이름
    hostname: Mapped[str | None] = mapped_column(String(64))  # 에이전트가 보고한 이름
    display_name: Mapped[str | None] = mapped_column(String(64))  # 화면 이름. 처음 보면 Server_n
    seq: Mapped[int | None] = mapped_column(Integer)  # 처음 본 순서
    ip: Mapped[str | None] = mapped_column(String(64))
    boot_id: Mapped[str | None] = mapped_column(String(64))
    driver_version: Mapped[str | None] = mapped_column(String(32))
    cuda_version: Mapped[str | None] = mapped_column(String(32))
    agent_version: Mapped[str | None] = mapped_column(String(32))
    first_seen: Mapped[float] = mapped_column(Float)
    last_received_ts: Mapped[float] = mapped_column(Float)
    last_measured_ts: Mapped[float] = mapped_column(Float)
    clock_skew_s: Mapped[float] = mapped_column(Float, default=0.0)
    conn_status: Mapped[str] = mapped_column(String(16), default="online")


class GpuRow(Base):
    __tablename__ = "gpus"

    uuid: Mapped[str] = mapped_column(String(80), primary_key=True)
    node_name: Mapped[str] = mapped_column(ForeignKey("nodes.name"), index=True)
    index: Mapped[int] = mapped_column(Integer)
    name: Mapped[str] = mapped_column(String(128))
    memory_total_mb: Mapped[int] = mapped_column(Integer)
    mig_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen: Mapped[float] = mapped_column(Float)


class GpuSampleRow(Base):
    __tablename__ = "gpu_samples"
    __table_args__ = (Index("ix_gpu_samples_gpu_ts", "gpu_uuid", "ts"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    gpu_uuid: Mapped[str] = mapped_column(String(80))
    ts: Mapped[float] = mapped_column(Float)
    received_ts: Mapped[float] = mapped_column(Float)
    util_gpu: Mapped[int | None] = mapped_column(Integer)
    util_mem: Mapped[int | None] = mapped_column(Integer)
    mem_used_mb: Mapped[int | None] = mapped_column(Integer)
    temp_c: Mapped[int | None] = mapped_column(Integer)
    power_w: Mapped[float | None] = mapped_column(Float)


class WorkloadRow(Base):
    __tablename__ = "workloads"
    __table_args__ = (
        Index("ix_workloads_owner_first_seen", "owner", "first_seen"),
        Index("ix_workloads_project_first_seen", "project", "first_seen"),
        Index("ix_workloads_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key: Mapped[str] = mapped_column(String(160), index=True)
    node: Mapped[str] = mapped_column(String(64), index=True)
    type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(Clipped(200))
    container_id: Mapped[str | None] = mapped_column(String(80))
    container_name: Mapped[str | None] = mapped_column(Clipped(200))
    image: Mapped[str | None] = mapped_column(Clipped(300))
    project: Mapped[str | None] = mapped_column(Clipped(100))  # 라벨 값
    job: Mapped[str | None] = mapped_column(Clipped(100))  # 라벨 값
    owner: Mapped[str | None] = mapped_column(Clipped(64))  # 미등록 라벨 소유자는 원문 그대로 온다
    owner_source: Mapped[str] = mapped_column(String(16), default="none")
    service_name: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    first_seen: Mapped[float] = mapped_column(Float)
    last_seen: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float | None] = mapped_column(Float)


class SessionRow(Base):
    __tablename__ = "gpu_sessions"
    __table_args__ = (
        # 프로세스 하나에 열린 세션은 하나. 끝난 세션은 같은 키가 또 있을 수 있다: 노드를 지운 뒤 다시
        # 등록되거나 24시간 넘게 끊겼다 돌아왔는데 같은 프로세스가 아직 돌고 있으면 새 세션으로 시작한다
        Index(
            "uq_session_open_key",
            "gpu_uuid",
            "boot_id",
            "pid",
            "proc_start_ts",
            unique=True,
            postgresql_where=text("status <> 'ended'"),
        ),
        Index("ix_sessions_gpu_start", "gpu_uuid", "start_ts"),
        Index("ix_sessions_status", "status"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    workload_id: Mapped[str] = mapped_column(ForeignKey("workloads.id"), index=True)
    node: Mapped[str] = mapped_column(String(64), index=True)
    gpu_uuid: Mapped[str] = mapped_column(String(80))
    boot_id: Mapped[str] = mapped_column(String(64))
    pid: Mapped[int] = mapped_column(Integer)
    proc_start_ts: Mapped[float | None] = mapped_column(Float)
    exec_username: Mapped[str | None] = mapped_column(String(64))
    exec_uid: Mapped[int | None] = mapped_column(Integer)
    cmdline: Mapped[str] = mapped_column(Text, default="")
    proc_type: Mapped[str] = mapped_column(String(4), default="C")
    status: Mapped[str] = mapped_column(String(16))
    start_ts: Mapped[float] = mapped_column(Float)
    last_seen_ts: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float | None] = mapped_column(Float)
    ended_reason: Mapped[str | None] = mapped_column(String(32))
    unknown_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    unknown_since: Mapped[float | None] = mapped_column(Float)
    miss_count: Mapped[int] = mapped_column(Integer, default=0)
    mem_used_mb: Mapped[int | None] = mapped_column(Integer)
    mem_max_mb: Mapped[int] = mapped_column(Integer, default=0)
    mem_gb_hours: Mapped[float] = mapped_column(Float, default=0.0)
    util_sm: Mapped[int | None] = mapped_column(Integer)
    util_sum: Mapped[float] = mapped_column(Float, default=0.0)
    util_samples: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------------- 등록 정보 (registry.py)


class UserRow(Base):
    """등록 사용자. position 순서대로 앞 8명이 대시보드 고유색을 받는다."""

    __tablename__ = "users"

    username: Mapped[str] = mapped_column(String(64), primary_key=True)
    display_name: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(128))
    slack_id: Mapped[str | None] = mapped_column(String(64))
    aliases: Mapped[list[str]] = mapped_column(JSON, default=list)
    position: Mapped[int] = mapped_column(Integer, default=0)


class AccountMappingRow(Base):
    """(노드, uid) → 사용자. node 가 NULL 이면 모든 노드다. 사용자를 지우면 같이 지워진다."""

    __tablename__ = "account_mappings"
    __table_args__ = (
        # NULL(모든 노드)끼리도 같은 값으로 본다 (PostgreSQL 15+)
        UniqueConstraint(
            "node", "uid", name="uq_account_mapping", postgresql_nulls_not_distinct=True
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    node: Mapped[str | None] = mapped_column(String(64))
    uid: Mapped[int] = mapped_column(Integer)
    username: Mapped[str] = mapped_column(
        ForeignKey("users.username", ondelete="CASCADE"), index=True
    )


class ServiceRow(Base):
    """공용 서비스 레지스트리. id 순서대로 매칭해서 먼저 맞는 것이 이긴다. 담당자를 지우면 담당자만 비운다."""

    __tablename__ = "services"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    match_exec_account: Mapped[str | None] = mapped_column(String(64))
    match_container_pattern: Mapped[str | None] = mapped_column(String(200))
    match_image_pattern: Mapped[str | None] = mapped_column(String(200))
    owner: Mapped[str | None] = mapped_column(ForeignKey("users.username", ondelete="SET NULL"))
    idle_exempt: Mapped[bool] = mapped_column(Boolean, default=True)


# ---------------------------------------------------------------------- 공지 (notices.py)


class NoticeRow(Base):
    """헤더 아래 공지 배너. until 이 지나면 화면에서 내려가고 행은 지울 때까지 남는다."""

    __tablename__ = "notices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    text: Mapped[str] = mapped_column(String(200))
    level: Mapped[str] = mapped_column(String(16))  # info | warning
    until: Mapped[float | None] = mapped_column(Float)  # NULL 이면 지울 때까지 보인다
    created_ts: Mapped[float] = mapped_column(Float)
