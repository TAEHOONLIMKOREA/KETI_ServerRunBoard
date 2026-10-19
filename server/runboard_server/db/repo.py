"""저장소 계층. 엔진은 이 클래스만 알고 SQLAlchemy 를 모른다 (PLAN 4.2 'repository 패턴').

SQLite 기본. URL 만 바꾸면 PostgreSQL 로 간다.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from runboard_server.core.sessions import Session, Workload
from runboard_server.core.state import NodeState
from runboard_server.db.models import (
    Base,
    GpuRow,
    GpuSampleRow,
    NodeRow,
    SessionRow,
    WorkloadRow,
)

log = logging.getLogger(__name__)

_WORKLOAD_FIELDS = [f.name for f in dataclasses.fields(Workload) if f.name != "dirty"]
_SESSION_FIELDS = [f.name for f in dataclasses.fields(Session) if f.name != "dirty"]


class Repository:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        kwargs: dict = {}
        if db_url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
            if db_url in ("sqlite://", "sqlite:///:memory:"):
                kwargs["poolclass"] = StaticPool
            else:
                path = db_url.removeprefix("sqlite:///")
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(db_url, future=True, **kwargs)
        if db_url.startswith("sqlite"):

            @event.listens_for(self.engine, "connect")
            def _pragmas(dbapi_conn, _record):  # pragma: no cover - 드라이버 콜백
                cursor = dbapi_conn.cursor()
                cursor.execute("PRAGMA journal_mode=WAL")
                cursor.execute("PRAGMA synchronous=NORMAL")
                cursor.close()

        Base.metadata.create_all(self.engine)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()

    # ------------------------------------------------------------------ 노드·GPU·샘플

    def upsert_node(self, node: NodeState) -> None:
        with self._sessions.begin() as db:
            row = db.get(NodeRow, node.info.name) or NodeRow(
                name=node.info.name, first_seen=node.first_seen
            )
            row.ip = node.info.ip
            row.boot_id = node.info.boot_id
            row.driver_version = node.info.driver_version
            row.cuda_version = node.info.cuda_version
            row.agent_version = node.info.agent_version
            row.last_received_ts = node.received_ts
            row.last_measured_ts = node.measured_ts
            row.clock_skew_s = node.clock_skew_s
            row.conn_status = node.conn_status
            db.add(row)
            for uuid, gpu in node.gpus.items():
                grow = db.get(GpuRow, uuid) or GpuRow(uuid=uuid, first_seen=gpu.first_seen)
                grow.node_name = node.info.name
                grow.index = gpu.report.index
                grow.name = gpu.report.name
                grow.memory_total_mb = gpu.report.mem_total_mb
                grow.mig_enabled = gpu.report.mig_enabled
                db.add(grow)

    def update_node_status(self, name: str, status: str) -> None:
        with self._sessions.begin() as db:
            row = db.get(NodeRow, name)
            if row is not None:
                row.conn_status = status

    def insert_samples(self, node: NodeState, ts: float, received_ts: float) -> int:
        rows = [
            GpuSampleRow(
                gpu_uuid=uuid,
                ts=ts,
                received_ts=received_ts,
                util_gpu=g.report.util_gpu,
                util_mem=g.report.util_mem,
                mem_used_mb=g.report.mem_used_mb,
                temp_c=g.report.temp_c,
                power_w=g.report.power_w,
            )
            for uuid, g in node.gpus.items()
        ]
        if rows:
            with self._sessions.begin() as db:
                db.add_all(rows)
        return len(rows)

    # ------------------------------------------------------------------ 작업·세션

    def save(self, workloads: Iterable[Workload], sessions: Iterable[Session]) -> None:
        workloads = list(workloads)
        sessions = list(sessions)
        if not workloads and not sessions:
            return
        with self._sessions.begin() as db:
            for w in workloads:
                db.merge(WorkloadRow(**{f: getattr(w, f) for f in _WORKLOAD_FIELDS}))
            for s in sessions:
                db.merge(SessionRow(**{f: getattr(s, f) for f in _SESSION_FIELDS}))

    def load_open(self) -> tuple[list[Workload], list[Session]]:
        """재시작 복원용: 끝나지 않은 작업·세션."""
        with self._sessions() as db:
            wrows = db.scalars(select(WorkloadRow).where(WorkloadRow.status != "ended")).all()
            srows = db.scalars(select(SessionRow).where(SessionRow.status != "ended")).all()
        workloads = [Workload(**{f: getattr(r, f) for f in _WORKLOAD_FIELDS}) for r in wrows]
        sessions = [Session(**{f: getattr(r, f) for f in _SESSION_FIELDS}) for r in srows]
        return workloads, sessions

    # ------------------------------------------------------------------ 조회·테스트 보조

    def count(self, model) -> int:
        with self._sessions() as db:
            return db.scalar(select(func.count()).select_from(model)) or 0

    def orm(self) -> OrmSession:
        return self._sessions()
