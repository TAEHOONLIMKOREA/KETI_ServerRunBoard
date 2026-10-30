"""저장소 계층. 엔진은 이 클래스만 알고 SQLAlchemy 를 모른다 (PLAN 4.2 'repository 패턴').

SQLite 기본. URL 만 바꾸면 PostgreSQL 로 간다.
"""

from __future__ import annotations

import dataclasses
import logging
from collections.abc import Iterable
from pathlib import Path

from sqlalchemy import create_engine, event, func, inspect, select, text
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from runboard_common.schemas import GpuReport, NodeInfo
from runboard_server.core.sessions import Session, Workload
from runboard_server.core.state import GpuState, NodeState
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
        self._add_missing_columns()
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()

    def _add_missing_columns(self) -> None:
        """create_all 은 기존 테이블에 열을 추가하지 않는다. Alembic(v0.2) 전까지 nullable 열만 ADD COLUMN 한다."""
        wanted = {"hostname": "VARCHAR(64)", "display_name": "VARCHAR(64)", "seq": "INTEGER"}
        existing = {c["name"] for c in inspect(self.engine).get_columns("nodes")}
        missing = [(col, ddl) for col, ddl in wanted.items() if col not in existing]
        if not missing:
            return
        with self.engine.begin() as conn:
            for col, ddl in missing:
                conn.execute(text(f"ALTER TABLE nodes ADD COLUMN {col} {ddl}"))
        log.info("nodes 표에 열 추가: %s", ", ".join(col for col, _ in missing))

    # ------------------------------------------------------------------ 노드·GPU·샘플

    def upsert_node(self, node: NodeState) -> None:
        with self._sessions.begin() as db:
            row = db.get(NodeRow, node.key) or NodeRow(name=node.key, first_seen=node.first_seen)
            row.hostname = node.info.name
            row.display_name = node.display_name
            row.seq = node.seq
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
                grow.node_name = node.key
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

    def load_nodes(self, prefix: str = "Server_") -> list[NodeState]:
        """재시작 복원용: 알려진 노드 전부. GPU 는 마지막 샘플 값으로 채우고 연결 상태는 끊김으로 둔다.

        에이전트가 한 번이라도 보고한 노드는 서버가 재시작돼도 사라지지 않고 '비활성화' 로 남는다.
        화면 이름이 없는 행(열을 추가하기 전 데이터)은 처음 본 순서대로 Server_n 을 붙여 저장한다.
        """
        out: list[NodeState] = []
        with self._sessions.begin() as db:
            rows = db.scalars(select(NodeRow).order_by(NodeRow.first_seen, NodeRow.name)).all()
            next_seq = max((r.seq or 0 for r in rows), default=0) + 1
            for n in rows:
                if n.seq is None or not n.display_name:
                    n.seq = next_seq
                    n.display_name = f"{prefix}{next_seq}"
                    next_seq += 1
                hostname = n.hostname or n.name
                info = NodeInfo(
                    name=hostname,
                    node_id=None if n.name == hostname else n.name,
                    ip=n.ip,
                    boot_id=n.boot_id or "unknown",
                    driver_version=n.driver_version,
                    cuda_version=n.cuda_version,
                    agent_version=n.agent_version or "?",
                    measured_ts=n.last_measured_ts,
                )
                node = NodeState(
                    key=n.name,
                    info=info,
                    first_seen=n.first_seen,
                    received_ts=n.last_received_ts,
                    measured_ts=n.last_measured_ts,
                    ts=n.last_measured_ts,
                    clock_skew_s=n.clock_skew_s,
                    conn_status="offline",
                    display_name=n.display_name,
                    seq=n.seq,
                )
                gpu_rows = db.scalars(
                    select(GpuRow).where(GpuRow.node_name == n.name).order_by(GpuRow.index)
                ).all()
                for g in gpu_rows:
                    s = db.scalars(
                        select(GpuSampleRow)
                        .where(GpuSampleRow.gpu_uuid == g.uuid)
                        .order_by(GpuSampleRow.ts.desc())
                        .limit(1)
                    ).first()
                    report = GpuReport(
                        index=g.index,
                        uuid=g.uuid,
                        name=g.name,
                        mem_total_mb=g.memory_total_mb,
                        mem_used_mb=s.mem_used_mb if s else None,
                        util_gpu=s.util_gpu if s else None,
                        util_mem=s.util_mem if s else None,
                        temp_c=s.temp_c if s else None,
                        power_w=s.power_w if s else None,
                        mig_enabled=g.mig_enabled,
                    )
                    node.gpus[g.uuid] = GpuState(report=report, first_seen=g.first_seen)
                out.append(node)
        out.sort(key=lambda n: (n.seq or 0, n.key))
        return out

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
