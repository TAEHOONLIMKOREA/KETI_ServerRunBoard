"""SQLAlchemy 모델 (PLAN 5.1). MVP 는 시작 시 create_all, Alembic 은 v0.2.

PLAN 과 다른 점: 작업·세션 ID 는 정수 autoincrement 대신 uuid4 hex 문자열이다.
메모리에서 먼저 만들고 DB 왕복 없이 연결하기 위해서다.
"""

from __future__ import annotations

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class NodeRow(Base):
    __tablename__ = "nodes"

    name: Mapped[str] = mapped_column(String(64), primary_key=True)
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
    name: Mapped[str] = mapped_column(String(200))
    container_id: Mapped[str | None] = mapped_column(String(80))
    container_name: Mapped[str | None] = mapped_column(String(200))
    image: Mapped[str | None] = mapped_column(String(300))
    project: Mapped[str | None] = mapped_column(String(100))
    job: Mapped[str | None] = mapped_column(String(100))
    owner: Mapped[str | None] = mapped_column(String(64))
    owner_source: Mapped[str] = mapped_column(String(16), default="none")
    service_name: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    first_seen: Mapped[float] = mapped_column(Float)
    last_seen: Mapped[float] = mapped_column(Float)
    end_ts: Mapped[float | None] = mapped_column(Float)


class SessionRow(Base):
    __tablename__ = "gpu_sessions"
    __table_args__ = (
        UniqueConstraint("gpu_uuid", "boot_id", "pid", "proc_start_ts", name="uq_session_key"),
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
