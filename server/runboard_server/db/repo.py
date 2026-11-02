"""저장소 계층 (repository 패턴). 엔진은 이 클래스만 알고 SQLAlchemy 를 모른다.

PostgreSQL 전용이다. 접속 정보는 settings.ini 에 있고, 테스트는 RUNBOARD_TEST_DB_URL 의 서버에 임시 DB 를 만든다.
"""

from __future__ import annotations

import dataclasses
import logging
import math
from collections import defaultdict
from collections.abc import Collection, Iterable, Iterator
from contextlib import contextmanager

from pydantic import BaseModel, ValidationError
from sqlalchemy import create_engine, delete, exists, func, inspect, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import DataError, DBAPIError, IntegrityError, StatementError
from sqlalchemy.orm import Session as OrmSession
from sqlalchemy.orm import sessionmaker

from runboard_common.schemas import GpuReport, NodeInfo
from runboard_server.core.history import BUCKET_S, Point, Sample, aggregate, bucket_start
from runboard_server.core.sessions import Session, Workload
from runboard_server.core.state import GpuState, NodeState, free_name
from runboard_server.db.models import (
    AccountMappingRow,
    Base,
    GpuRow,
    GpuSample5mRow,
    GpuSampleRow,
    NodeRow,
    NoticeRow,
    ServiceRow,
    SessionRow,
    UserRow,
    WorkloadRow,
)
from runboard_server.errors import Conflict, Invalid
from runboard_server.notices import NoticeEntry, NoticeFields
from runboard_server.registry import (
    AccountMapping,
    MappingFields,
    Registry,
    ServiceEntry,
    ServiceFields,
    UserEntry,
    UserFields,
)

log = logging.getLogger(__name__)

_WORKLOAD_FIELDS = [f.name for f in dataclasses.fields(Workload) if f.name != "dirty"]
_SESSION_FIELDS = [f.name for f in dataclasses.fields(Session) if f.name != "dirty"]
_SAMPLE_COLUMNS = [getattr(GpuSampleRow, f.name) for f in dataclasses.fields(Sample)]
_POINT_FIELDS = [f.name for f in dataclasses.fields(Point) if f.name != "ts"]  # 5분 행의 값 열

# create_all 은 기존 표에 열을 더하지 않는다. 나중에 생긴 열은 전부 NULL 허용이라 ADD COLUMN 만 하면 된다
_ADDED_COLUMNS = {
    "nodes": {"hostname": "VARCHAR(64)", "display_name": "VARCHAR(64)", "seq": "INTEGER"},
    "gpu_samples": {"occupied": "BOOLEAN"},
}


def _bucket_bound(ts: float) -> int:
    """bucket_ts(정수 열)와 비교할 경계. 실수와 비교하면 PostgreSQL 이 열 쪽을 바꿔 인덱스를 못 쓴다.

    정수 b 에 대해 b < ts 와 b < ceil(ts), b >= ts 와 b >= ceil(ts) 는 같은 뜻이다.
    """
    return math.ceil(ts)


def _workload_row(w: Workload) -> WorkloadRow:
    return WorkloadRow(**{f: getattr(w, f) for f in _WORKLOAD_FIELDS})


def _session_row(s: Session) -> SessionRow:
    return SessionRow(**{f: getattr(s, f) for f in _SESSION_FIELDS})


def _row_fault(exc: Exception) -> bool:
    """그 행의 값 탓인 실패인가. 다시 해도 같으므로 그 행만 건너뛴다.

    제약 위반(IntegrityError), DB 가 받지 않는 값(DataError), DB 에 보내기 전에 값을 바꾸다 난 예외가 그렇다.
    짝 없는 서로게이트(JSON 의 "\\udcff")는 psycopg 가 UnicodeEncodeError 를 감싸지 않고 그대로 올린다. 이런 행을
    돌려주면 다음 저장마다 다시 실패해 모든 노드의 저장이 멈춘다. 연결 끊김, 권한·스키마 문제 같은 나머지 DB
    오류와 SQLAlchemy 자체 오류는 행 탓이 아니다.
    """
    if isinstance(exc, (IntegrityError, DataError)):
        return True
    if isinstance(exc, DBAPIError):
        return False
    if isinstance(exc, StatementError):  # 바인드 값 처리에서 난 예외를 SQLAlchemy 가 감싼 것
        exc = exc.orig
    return isinstance(exc, (ValueError, TypeError))


def _entries(model: type[BaseModel], rows: Iterable) -> list:
    """DB 행을 등록 정보·공지 모델로. 손으로 고쳐 값이 틀린 행은 경고만 남기고 건너뛴다."""
    out = []
    for row in rows:
        try:
            out.append(model.model_validate(row, from_attributes=True))
        except ValidationError as exc:
            log.warning("%s 행을 건너뛴다 (값이 올바르지 않다): %s", row.__tablename__, exc)
    return out


class Repository:
    def __init__(self, db_url: str) -> None:
        self.db_url = db_url
        # DB 가 재시작하면 풀의 연결이 끊겨 있다. 꺼낼 때 확인해서 저장이 한 번 실패하는 것을 막는다
        self.engine = create_engine(db_url, future=True, pool_pre_ping=True)
        Base.metadata.create_all(self.engine)
        self._migrate()
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def close(self) -> None:
        self.engine.dispose()

    def _migrate(self) -> None:
        """create_all 은 기존 표를 바꾸지 않는다. Alembic 은 쓰지 않고 필요한 변경을 여기서 한다.

        빠진 열(_ADDED_COLUMNS)을 더하고, 옛 세션 키 유일 제약(uq_session_key)을 지우고, 없는 인덱스를 만든다.
        """
        insp = inspect(self.engine)
        missing: list[tuple[str, str, str]] = []
        for table, wanted in _ADDED_COLUMNS.items():
            existing = {c["name"] for c in insp.get_columns(table)}
            missing += [(table, col, ddl) for col, ddl in wanted.items() if col not in existing]
        old_key = any(
            c["name"] == "uq_session_key" for c in insp.get_unique_constraints("gpu_sessions")
        )
        with self.engine.begin() as conn:
            for table, col, ddl in missing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}"))
            if old_key:  # 끝난 세션까지 막던 유일 제약 → 열린 세션만 막는 부분 인덱스
                conn.execute(text("ALTER TABLE gpu_sessions DROP CONSTRAINT uq_session_key"))
        for table in Base.metadata.sorted_tables:
            for index in table.indexes:
                index.create(self.engine, checkfirst=True)
        if missing:
            log.info("표에 열 추가: %s", ", ".join(f"{t}.{col}" for t, col, _ in missing))
        if old_key:
            log.info("gpu_sessions 세션 키 유일 제약을 열린 세션에만 건다")

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

    def insert_samples(
        self, node: NodeState, ts: float, received_ts: float, busy: Collection[str]
    ) -> int:
        """busy 는 지금 열린 세션이 있는 GPU 의 uuid. 샘플의 occupied 가 된다."""
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
                occupied=uuid in busy,
            )
            for uuid, g in node.gpus.items()
        ]
        if rows:
            with self._sessions.begin() as db:
                db.add_all(rows)
        return len(rows)

    # ------------------------------------------------------------------ 작업·세션

    def save(self, workloads: Iterable[Workload], sessions: Iterable[Session]) -> None:
        """작업, 끝난 세션, 나머지 세션 순서로 한 트랜잭션에 쓴다.

        열린 세션의 유일 인덱스(uq_session_open_key)는 문장마다 검사한다. 같은 프로세스의 옛 세션을 끝내는
        UPDATE 가 새 세션의 INSERT 보다 먼저 나가야 한다 (다른 노드가 이어받을 때, 돌려받은 종료와 다시
        나타난 프로세스가 함께 올 때). 그래서 끝난 세션까지 쓰고 flush 한 뒤 나머지를 쓴다. 세션의 작업과
        돌려받은 옛 종료는 SessionTracker.take_dirty 가 늘 같은 저장에 함께 낸다.

        행 탓인 실패(_row_fault)면 같은 순서로 한 행씩 다시 쓰고 그래도 안 되는 행만 건너뛴다 (_save_each).
        다른 실패(연결 끊김 등)는 그대로 올린다. 엔진이 꺼낸 것을 전부 tracker 에 돌려준다.
        """
        workloads = list(workloads)
        sessions = list(sessions)
        if not workloads and not sessions:
            return
        first = [_workload_row(w) for w in workloads]
        first += [_session_row(s) for s in sessions if s.status == "ended"]
        rest = [_session_row(s) for s in sessions if s.status != "ended"]
        try:
            with self._sessions.begin() as db:
                for row in first:
                    db.merge(row)
                db.flush()
                for row in rest:
                    db.merge(row)
        except Exception as exc:
            if not _row_fault(exc):
                raise
            self._save_each(first + rest)

    def _save_each(self, rows: list[WorkloadRow | SessionRow]) -> None:
        """한 트랜잭션 안에서 행마다 savepoint 를 두고 쓴다. 행 탓으로 실패하는 행은 경고를 남기고 건너뛴다.

        그 행의 값 탓이라 다시 해도 같으므로 건너뛴 행은 돌려주지 않는다. 열린 세션은 다음 보고 때 다시
        dirty 가 되어 또 시도한다. 세션의 작업 행을 건너뛰었으면 그 세션도 외래 키 위반으로 건너뛴다.
        다른 실패(연결 끊김 등)는 아무것도 쓰지 않고 올린다.
        """
        with self._sessions.begin() as db:
            for row in rows:
                try:
                    with db.begin_nested():
                        db.merge(row)
                except Exception as exc:
                    if not _row_fault(exc):
                        raise
                    log.warning(
                        "%s 행을 쓰지 못해 건너뛴다 (id %s): %s",
                        row.__tablename__,
                        row.id,
                        getattr(exc, "orig", None) or exc,
                    )

    def load_nodes(self, prefix: str = "Server_") -> list[NodeState]:
        """재시작 복원용: 알려진 노드 전부. GPU 는 마지막 샘플 값으로 채우고 연결 상태는 끊김으로 둔다.

        에이전트가 한 번이라도 보고한 노드는 서버가 재시작돼도 사라지지 않고 '비활성화' 로 남는다.
        화면 이름이 없는 행(열을 추가하기 전 데이터)은 처음 본 순서대로 Server_n 을 붙여 저장한다.
        """
        out: list[NodeState] = []
        with self._sessions.begin() as db:
            rows = db.scalars(select(NodeRow).order_by(NodeRow.first_seen, NodeRow.name)).all()
            next_seq = max((r.seq or 0 for r in rows), default=0) + 1
            taken = {x for r in rows for x in (r.display_name, r.name, r.hostname) if x}
            for n in rows:
                if n.seq is None or not n.display_name:
                    n.seq = next_seq
                    n.display_name = free_name(prefix, next_seq, taken)
                    taken.add(n.display_name)
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

    # ------------------------------------------------------------------ 등록 정보 (관리 API)

    def load_registry(self) -> Registry:
        with self._sessions() as db:
            users = db.scalars(select(UserRow).order_by(UserRow.position, UserRow.username)).all()
            mappings = db.scalars(select(AccountMappingRow).order_by(AccountMappingRow.id)).all()
            services = db.scalars(select(ServiceRow).order_by(ServiceRow.id)).all()
            return Registry(
                users=_entries(UserEntry, users),
                account_mappings=_entries(AccountMapping, mappings),
                services=_entries(ServiceEntry, services),
            )

    @contextmanager
    def _writing(self) -> Iterator[OrmSession]:
        """관리 쓰기 트랜잭션. API 가 먼저 검사하므로 제약 위반은 동시에 고친 경우뿐이다. 409 로 돌려준다.

        DB 가 받지 않는 값(DataError)은 422 로 돌려준다. 공지와 노드 화면 이름의 제어 문자는 입력 모델이 먼저
        막고(registry.check_chars), 등록 정보 문자열과 경로의 사용자 이름에 든 NUL 이 여기까지 온다.
        """
        try:
            with self._sessions.begin() as db:
                yield db
        except IntegrityError as exc:
            log.warning("등록 정보 제약 위반: %s", exc.orig)
            raise Conflict("다른 요청과 겹쳤다. 목록을 새로 읽고 다시 한다") from exc
        except DataError as exc:
            log.warning("DB 가 받지 않는 값: %s", exc.orig)
            raise Invalid("DB 에 넣을 수 없는 값이다 (NUL 같은 제어 문자 등)") from exc

    def _update(self, model, key, values: dict) -> bool:
        """기본 키는 바꾸지 않는다. 호출하는 쪽이 values 에서 뺀다."""
        with self._writing() as db:
            row = db.get(model, key)
            if row is None:
                return False
            for name, value in values.items():
                setattr(row, name, value)
            return True

    def _delete(self, model, key) -> bool:
        with self._writing() as db:
            row = db.get(model, key)
            if row is None:
                return False
            db.delete(row)
            return True

    def add_user(self, user: UserEntry) -> None:
        """맨 뒤에 붙인다 (색 순서도 맨 뒤)."""
        with self._writing() as db:
            last = db.scalar(select(func.max(UserRow.position)))
            db.add(UserRow(**user.model_dump(), position=0 if last is None else last + 1))

    def update_user(self, username: str, fields: UserFields) -> bool:
        return self._update(UserRow, username, fields.model_dump(exclude={"username"}))

    def delete_user(self, username: str) -> bool:
        """이 사용자의 계정 매핑도 지워지고, 담당 서비스는 담당자가 비워진다 (외래 키)."""
        return self._delete(UserRow, username)

    def reorder_users(self, usernames: list[str]) -> bool:
        """usernames 순서로 색 순서를 다시 매긴다. 목록이 지금 사용자 전체와 다르면 False."""
        with self._writing() as db:
            rows = {r.username: r for r in db.scalars(select(UserRow)).all()}
            if len(usernames) != len(rows) or set(usernames) != set(rows):
                return False
            for position, username in enumerate(usernames):
                rows[username].position = position
            return True

    def add_mapping(self, mapping: MappingFields) -> int:
        with self._writing() as db:
            row = AccountMappingRow(**mapping.model_dump(exclude={"id"}))
            db.add(row)
            db.flush()
            return row.id

    def update_mapping(self, mapping_id: int, mapping: MappingFields) -> bool:
        return self._update(AccountMappingRow, mapping_id, mapping.model_dump(exclude={"id"}))

    def delete_mapping(self, mapping_id: int) -> bool:
        return self._delete(AccountMappingRow, mapping_id)

    def add_service(self, service: ServiceFields) -> int:
        with self._writing() as db:
            row = ServiceRow(**service.model_dump(exclude={"id"}))
            db.add(row)
            db.flush()
            return row.id

    def update_service(self, service_id: int, service: ServiceFields) -> bool:
        return self._update(ServiceRow, service_id, service.model_dump(exclude={"id"}))

    def delete_service(self, service_id: int) -> bool:
        return self._delete(ServiceRow, service_id)

    # ------------------------------------------------------------------ 공지 (관리 API)

    def load_notices(self) -> list[NoticeEntry]:
        """기한이 지난 것까지 전부, 등록 순."""
        with self._sessions() as db:
            return _entries(NoticeEntry, db.scalars(select(NoticeRow).order_by(NoticeRow.id)).all())

    def add_notice(self, notice: NoticeFields, created_ts: float) -> NoticeEntry:
        with self._writing() as db:
            row = NoticeRow(**notice.model_dump(), created_ts=created_ts)
            db.add(row)
            db.flush()
            return NoticeEntry.model_validate(row, from_attributes=True)

    def update_notice(self, notice_id: int, notice: NoticeFields) -> NoticeEntry | None:
        """내용·수준·기한을 바꾼다. 등록 시각(배너 순서)은 그대로다. 없는 공지면 None."""
        with self._writing() as db:
            row = db.get(NoticeRow, notice_id)
            if row is None:
                return None
            for name, value in notice.model_dump().items():
                setattr(row, name, value)
            return NoticeEntry.model_validate(row, from_attributes=True)

    def delete_notice(self, notice_id: int) -> bool:
        return self._delete(NoticeRow, notice_id)

    # ------------------------------------------------------------------ 노드 관리

    def rename_node(self, key: str, display_name: str) -> None:
        """DB 가 받지 않는 이름이면 Invalid. 엔진은 이것이 성공한 뒤에 메모리 이름을 바꾼다."""
        with self._writing() as db:
            row = db.get(NodeRow, key)
            if row is not None:
                row.display_name = display_name

    def delete_node(self, key: str) -> None:
        """nodes·gpus 행만 지운다. 작업·세션·샘플은 이력이라 남긴다."""
        with self._sessions.begin() as db:
            db.execute(delete(GpuRow).where(GpuRow.node_name == key))
            db.execute(delete(NodeRow).where(NodeRow.name == key))

    # ------------------------------------------------------------------ 이력: 5분 집계·보존 정리

    def aggregate_samples(self, start: int, end: int) -> int:
        """버킷 경계 [start, end) 의 원본 샘플을 GPU·5분 버킷별로 묶어 gpu_samples_5m 에 쓴다.

        같은 구간을 다시 해도 결과가 같다 (있는 행은 덮어쓴다). 쓴 행 수를 돌려준다.
        """
        groups: dict[tuple[str, int], list[Sample]] = defaultdict(list)
        query = select(GpuSampleRow.gpu_uuid, *_SAMPLE_COLUMNS).where(
            GpuSampleRow.ts >= start, GpuSampleRow.ts < end
        )
        with self.engine.connect() as conn:
            for uuid, *values in conn.execute(query):
                sample = Sample(*values)
                groups[(uuid, bucket_start(sample.ts))].append(sample)
        rows = []
        for (uuid, bucket), samples in groups.items():
            point = aggregate(bucket, samples)
            rows.append(
                {"gpu_uuid": uuid, "bucket_ts": bucket}
                | {f: getattr(point, f) for f in _POINT_FIELDS}
            )
        if rows:
            stmt = pg_insert(GpuSample5mRow)
            stmt = stmt.on_conflict_do_update(
                index_elements=["gpu_uuid", "bucket_ts"],
                set_={f: stmt.excluded[f] for f in _POINT_FIELDS},
            )
            with self.engine.begin() as conn:
                conn.execute(stmt, rows)
        return len(rows)

    def aggregation_resume(self) -> int | None:
        """집계를 이어 할 버킷 시작. 마지막 집계 버킷의 끝, 집계가 없으면 가장 오래된 원본 샘플의 버킷.

        둘 다 없으면 None.
        """
        with self.engine.connect() as conn:
            last = conn.scalar(select(func.max(GpuSample5mRow.bucket_ts)))
            if last is not None:
                return last + BUCKET_S
            first = conn.scalar(select(func.min(GpuSampleRow.ts)))
        return None if first is None else bucket_start(first)

    def purge(self, raw_before: float, agg_before: float, sessions_before: float) -> dict[str, int]:
        """보존 기간이 지난 행을 지우고 표별로 지운 수를 돌려준다. 노드·GPU 행은 지우지 않는다.

        원본 샘플은 ts, 5분 집계는 bucket_ts 로 본다. 세션은 끝난 것 중 end_ts 가 기한 전인 것만,
        작업은 끝났고 end_ts 가 기한 전이며 남은 세션이 없는 것만 지운다 (세션이 작업을 참조한다).
        """
        statements = {
            "gpu_samples": delete(GpuSampleRow).where(GpuSampleRow.ts < raw_before),
            "gpu_samples_5m": delete(GpuSample5mRow).where(
                GpuSample5mRow.bucket_ts < _bucket_bound(agg_before)
            ),
            "gpu_sessions": delete(SessionRow).where(
                SessionRow.status == "ended", SessionRow.end_ts < sessions_before
            ),
            "workloads": delete(WorkloadRow).where(
                WorkloadRow.status == "ended",
                WorkloadRow.end_ts < sessions_before,
                ~exists().where(SessionRow.workload_id == WorkloadRow.id),
            ),
        }
        with self.engine.begin() as conn:
            return {table: conn.execute(stmt).rowcount for table, stmt in statements.items()}

    # ------------------------------------------------------------------ 이력 조회

    def has_gpu(self, gpu_uuid: str) -> bool:
        with self._sessions() as db:
            return db.get(GpuRow, gpu_uuid) is not None

    def node_gpus(self, key: str) -> list[GpuReport]:
        """노드의 GPU (gpus 표, index 순). 지금 보고에서 빠진 GPU 도 들어 있다. 측정값은 비어 있다.

        빠진 GPU 는 예전 index 를 그대로 가지므로 뒤 GPU 의 번호가 당겨지면 같은 index 가 둘이 된다.
        그때도 요청마다 순서가 같도록 uuid 로 가른다.
        """
        with self._sessions() as db:
            rows = db.scalars(
                select(GpuRow).where(GpuRow.node_name == key).order_by(GpuRow.index, GpuRow.uuid)
            ).all()
            return [
                GpuReport(
                    index=g.index,
                    uuid=g.uuid,
                    name=g.name,
                    mem_total_mb=g.memory_total_mb,
                    mig_enabled=g.mig_enabled,
                )
                for g in rows
            ]

    def samples(self, gpu_uuid: str, start: float, end: float) -> list[Sample]:
        """GPU 하나의 원본 샘플 [start, end), 시각 순."""
        query = (
            select(*_SAMPLE_COLUMNS)
            .where(
                GpuSampleRow.gpu_uuid == gpu_uuid,
                GpuSampleRow.ts >= start,
                GpuSampleRow.ts < end,
            )
            .order_by(GpuSampleRow.ts)
        )
        with self.engine.connect() as conn:
            return [Sample(*row) for row in conn.execute(query)]

    def buckets(
        self, gpu_uuids: Collection[str], start: float, end: float
    ) -> dict[str, list[Point]]:
        """GPU 별 5분 집계 점. bucket_ts 가 [start, end) 인 것, 시각 순. 행이 없는 GPU 는 빈 목록."""
        out: dict[str, list[Point]] = {uuid: [] for uuid in gpu_uuids}
        if not out:
            return out
        values = [getattr(GpuSample5mRow, f) for f in _POINT_FIELDS]
        query = (
            select(GpuSample5mRow.gpu_uuid, GpuSample5mRow.bucket_ts, *values)
            .where(
                GpuSample5mRow.gpu_uuid.in_(list(out)),
                GpuSample5mRow.bucket_ts >= _bucket_bound(start),
                GpuSample5mRow.bucket_ts < _bucket_bound(end),
            )
            .order_by(GpuSample5mRow.bucket_ts)
        )
        with self.engine.connect() as conn:
            for uuid, bucket, *rest in conn.execute(query):
                out[uuid].append(Point(bucket, *rest))
        return out

    # ------------------------------------------------------------------ 조회·테스트 보조

    def count(self, model) -> int:
        with self._sessions() as db:
            return db.scalar(select(func.count()).select_from(model)) or 0

    def orm(self) -> OrmSession:
        return self._sessions()
