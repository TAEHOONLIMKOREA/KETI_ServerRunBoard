"""보고 1건을 처리하는 파이프라인과 조회 뷰.

ingest: 검증된 보고 → 시계 오차 검사 → 최신 상태 갱신 → 유형·소유자 판정 → 세션·작업 전이 → 영속화
tick  : 연결 판정 → offline 노드의 세션을 unknown 으로 → 24시간 초과 정리 → 영속화 → 5분 집계·보존 정리
관리  : 등록 정보 교체(set_registry), 공지 교체(set_notices), 노드 이름 변경·삭제
이력  : 노드·GPU 이력 조회 (5분 집계 gpu_samples_5m, 원본 gpu_samples)
공지  : 메모리 사본만 읽는다. overview 는 1초마다 불리므로 DB 를 읽지 않고 그때의 시각으로 기한을 거른다

락 하나로 직렬화한다. 서버 프로세스는 하나다. 집계·보존 정리와 이력 조회는 DB 만 보므로 락 밖에서 한다.
메모리를 먼저 바꾸고 DB 에 쓴다. 쓰다 실패하면 꺼낸 작업·세션을 tracker 에 돌려주어 다음에 DB 에 쓸 때(tick,
샘플 저장, 전이가 있는 보고) 함께 다시 쓴다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable, Iterable, Iterator
from contextlib import contextmanager

from runboard_common.schemas import NodeReport
from runboard_server.config import ServerConfig
from runboard_server.core.detector import (
    activity,
    badges,
    conn_status,
    health,
    occupancy,
    summary_color,
)
from runboard_server.core.history import (
    BUCKET_S,
    DAY_S,
    RANGES,
    STEP_S,
    Point,
    Range,
    Step,
    bucket_start,
    closed_until,
    fill,
    raw_point,
    rollup,
)
from runboard_server.core.owner import OwnerResolver
from runboard_server.core.sessions import Session, SessionTracker, Workload
from runboard_server.core.state import GpuState, NodeState, StateStore, free_name, node_key
from runboard_server.errors import Conflict
from runboard_server.notices import NoticeEntry, banner_order
from runboard_server.registry import Registry
from runboard_server.views import (
    GpuHistoryOut,
    GpuOut,
    GpuSeries,
    HistoryPoint,
    NodeBrief,
    NodeHistoryOut,
    NodeOut,
    NoticeOut,
    OverviewOut,
    SessionOut,
    Summary,
    UserDetailOut,
    UserOut,
    UsersOut,
    WorkloadOnGpu,
    WorkloadOut,
    WorkloadsOut,
)

log = logging.getLogger(__name__)

UNKNOWN_USER = "_unknown"
# 밀린 집계를 한 번에 읽는 범위. 오래 밀려도 원본 샘플을 한꺼번에 메모리에 올리지 않는다
AGG_CHUNK_S = 6 * 3600
PURGE_INTERVAL_S = 3600.0


def _history_point(point: Point) -> HistoryPoint:
    return HistoryPoint.model_validate(point, from_attributes=True)


class Engine:
    def __init__(
        self,
        cfg: ServerConfig,
        repo=None,
        clock: Callable[[], float] = time.time,
        registry: Registry | None = None,
    ):
        """registry 를 주지 않으면 repo(DB)에서 읽는다. DB 도 없으면 등록 정보 없이 시작한다."""
        self.cfg = cfg
        self.th = cfg.thresholds
        self.repo = repo
        self.clock = clock
        self.state = StateStore(util_history_s=self.th.util_history_s)
        self.tracker = SessionTracker(
            miss_reports=self.th.session_miss_reports,
            offline_close_after_s=self.th.offline_close_after_s,
        )
        self.lock = threading.RLock()
        self._agg_next: int | None = None  # 다음에 집계할 5분 버킷의 시작. None 이면 DB 에서 찾는다
        self._purge_due = 0.0  # 다음 보존 정리 시각 (엔진 시계)
        # 공지: 기한이 지난 것까지 전부, 배너 순서. restore 와 관리 API 가 DB 에서 다시 읽어 넣는다
        self.notices: list[NoticeEntry] = []
        if registry is None:
            registry = repo.load_registry() if repo is not None else Registry()
        self._apply_registry(registry)

    def _apply_registry(self, registry: Registry) -> None:
        self.registry = registry
        self.resolver = OwnerResolver(self.cfg.owner, registry)
        self._registered = {u.username for u in registry.users}

    # ------------------------------------------------------------------ 쓰기 경로

    def restore(self) -> None:
        if self.repo is None:
            return
        nodes = self.repo.load_nodes(prefix=self.cfg.server.display_name_prefix)
        workloads, sessions = self.repo.load_open()
        notices = self.repo.load_notices()
        with self.lock:
            self.state.restore(nodes)
            self.tracker.restore(workloads, sessions)
        self.set_notices(notices)
        log.info(
            "복원: 노드 %d (비활성화로 시작), 열린 작업 %d, 세션 %d (모두 미확인으로 시작), 공지 %d",
            len(nodes),
            len(workloads),
            len(sessions),
            len(notices),
        )
        try:  # 꺼져 있던 동안 밀린 집계를 채우고 보존 기간이 지난 행을 지운다
            self._maintain(self.clock())
        except Exception:
            log.exception("시작 때 이력 집계·보존 정리 실패. tick 에서 다시 한다")

    def ingest(self, report: NodeReport, received_ts: float | None = None) -> None:
        with self.lock:
            self._drop_ignored(report)
            received = self.clock() if received_ts is None else received_ts
            skew = received - report.node.measured_ts
            skew_flag = abs(skew) > self.th.clock_skew_max_s
            ts = received if skew_flag else report.node.measured_ts
            key = node_key(report.node)
            node = self.state.update(key, report, received, ts, skew, skew_flag)
            if node.display_name is None:
                # 처음 본 노드: 순번과 화면 이름을 붙인다. 이름은 rename_node 와 같은 규칙으로 다른 노드가
                # 쓰는 이름을 피한다. 첫 보고는 바로 DB 에 쓰이므로 재시작 뒤에도 남는다
                node.seq = self.state.next_seq()
                node.display_name = free_name(
                    self.cfg.server.display_name_prefix, node.seq, self.state.names_in_use(key)
                )
                log.info(
                    "새 노드 등록: %s (호스트 %s) → %s", key, report.node.name, node.display_name
                )
            if skew_flag:
                log.warning("노드 %s 시계 오차 %.0fs: 수신 시각으로 대체", node.display_name, skew)
            self.tracker.apply_report(report, ts, self.resolver.classify, node=key)
            self._persist(node, ts, received)

    def _drop_ignored(self, report: NodeReport) -> None:
        """owner.ignore_commands 의 프로세스(기본 Xorg)를 보고에서 뺀다. 세션을 만들지 않고 GPU 점유에도 세지 않는다.

        그것만 있던 GPU 는 비어 있는 GPU 다. 전에 열린 세션이 있었다면(설정을 바꾸고 다시 시작한 뒤) 보고에서
        사라진 프로세스와 같이 끝난다. 에이전트가 보내는 것은 그대로라 에이전트의 보고 주기에는 영향이 없다.
        """
        for gpu in report.gpus:
            if gpu.processes:
                gpu.processes = [p for p in gpu.processes if not self.resolver.ignored(p)]

    def set_registry(self, registry: Registry) -> None:
        """관리 API 가 DB 에 쓴 뒤 다시 읽어 넘긴다. 열린 작업은 다음 보고 때 유형·소유자를 다시 판정한다."""
        with self.lock:
            self._apply_registry(registry)
            self.tracker.mark_reclassify()

    def set_notices(self, notices: Iterable[NoticeEntry]) -> None:
        """시작할 때와 관리 API 가 DB 에 쓴 뒤 전부 다시 읽어 넘긴다. 판정과 무관해 재판정하지 않는다."""
        ordered = sorted(notices, key=banner_order)
        with self.lock:
            self.notices = ordered

    def rename_node(self, key: str, display_name: str) -> NodeOut | None:
        """화면 이름은 다른 노드의 화면 이름·식별자·호스트명과 겹치면 안 된다.

        runboard-server nodes 의 <노드> 는 셋 중 어느 것으로도 노드를 가리킨다.
        """
        with self.lock:
            node = self.state.nodes.get(key)
            if node is None:
                return None
            taken = next(
                (
                    n
                    for n in self.state.nodes.values()
                    if n.key != key and display_name in (n.display_name, n.key, n.info.name)
                ),
                None,
            )
            if taken is not None:
                raise Conflict(f"다른 노드({taken.info.name})가 이미 쓰는 이름이다: {display_name}")
            if self.repo is not None:
                self.repo.rename_node(key, display_name)
            log.info("노드 %s 이름 변경: %s → %s", key, node.display_name, display_name)
            node.display_name = display_name
            return self._node_out(node, self.clock())

    def delete_node(self, key: str, now: float | None = None) -> bool:
        """끊긴 노드만 지운다. 열린 세션은 node_removed 로 끝내고, 작업·세션·샘플 이력은 DB 에 남긴다.

        보고 중인 노드를 지우면 다음 보고에서 새 이름으로 바로 다시 등록되므로 막는다.
        DB 에 쓰다 실패하면 예외를 올리고 노드는 남는다. 세션은 이미 끝났으므로 그 종료는 다음 저장 때 쓴다.
        다시 지우면 된다.
        """
        with self.lock:
            node = self.state.nodes.get(key)
            if node is None:
                return False
            now = self.clock() if now is None else now
            if (
                conn_status(node.received_ts, now, self.th, node.info.report_interval_s or 0.0)
                != "offline"
            ):
                raise Conflict(
                    "보고 중인 노드는 지울 수 없다. 에이전트를 멈추고 '비활성화' 가 된 뒤에 지운다"
                )
            self.tracker.end_node(key, "node_removed")
            workloads, sessions = self.tracker.take_dirty()
            if self.repo is not None:
                with self._requeue_on_error(workloads, sessions):
                    self.repo.save(workloads, sessions)
                    self.repo.delete_node(key)
            del self.state.nodes[key]
            log.info("노드 삭제: %s (%s)", node.display_name, key)
            return True

    def tick(self, now: float | None = None) -> None:
        with self.lock:
            now = self.clock() if now is None else now
            for node in self.state.nodes.values():
                name = node.key
                status = conn_status(
                    node.received_ts, now, self.th, node.info.report_interval_s or 0.0
                )
                if status != node.conn_status:
                    log.info("노드 %s 연결 상태 %s → %s", name, node.conn_status, status)
                    node.conn_status = status
                    if status == "offline":
                        self.tracker.mark_node_offline(name, now)
                    if self.repo is not None:
                        self.repo.update_node_status(name, status)
                if status == "offline":
                    closed = self.tracker.close_offline_timeouts(name, now)
                    if closed:
                        log.info("노드 %s: 24시간 넘게 끊겨 세션 %d개 종료", name, closed)
            workloads, sessions = self.tracker.take_dirty()
            if self.repo is not None:
                with self._requeue_on_error(workloads, sessions):
                    self.repo.save(workloads, sessions)
        self._maintain(now)

    def _persist(self, node: NodeState, ts: float, received: float) -> None:
        """보고마다 호출된다. 샘플·노드 행과 누적값은 persist_interval 마다, 상태 전이는 바로 쓴다.

        보고가 1초마다 오므로 전부 매번 쓰면 DB 쓰기가 수십 배가 된다. 전이(생성·종료·미확인·복귀)만
        바로 쓰고, GB·시간 같은 누적값은 tick(10초)과 샘플 저장 때 모아서 쓴다.

        DB 가 멈추면 전이도, 샘플 저장도, 호스트명 변경(info_dirty)도 없는 보고는 DB 에 닿지 않아 204 다.
        샘플 저장 때가 됐거나 호스트명이 바뀐 노드의 보고는 그 저장에 성공할 때까지 보고마다 DB 에 쓰려다
        실패해 500 이 된다.
        """
        due = (
            node.last_sample_persist_ts == 0.0
            or ts - node.last_sample_persist_ts >= self.cfg.server.persist_interval_s
        )
        workloads, sessions = self.tracker.take_dirty(transitions_only=not due)
        if self.repo is None:
            return
        with self._requeue_on_error(workloads, sessions):
            if due or node.info_dirty:
                self.repo.upsert_node(node)
                node.info_dirty = False
            if due:
                busy = {uuid for uuid in node.gpus if self._gpu_sessions(node.key, uuid)}
                self.repo.insert_samples(node, ts, received, busy)
                node.last_sample_persist_ts = ts
            self.repo.save(workloads, sessions)

    @contextmanager
    def _requeue_on_error(
        self, workloads: list[Workload], sessions: list[Session]
    ) -> Iterator[None]:
        """take_dirty 로 꺼낸 것을 쓰는 구간. 무엇이 실패하든 꺼낸 것을 tracker 에 돌려주고 예외는 그대로 올린다.

        끝난 세션·작업은 꺼낼 때 메모리에서 빠지므로, 돌려주지 않으면 그 종료가 DB 에 쓰이지 않는다.
        돌려준 것은 다음에 DB 에 쓸 때(tick, 샘플 저장, 전이가 있는 보고) 함께 쓴다. 보고는 500, tick 은
        'tick 실패' 로 끝난다.
        """
        try:
            yield
        except BaseException:
            self.tracker.requeue(workloads, sessions)
            raise

    # ------------------------------------------------------------------ 이력 집계·보존 정리

    def _maintain(self, now: float) -> None:
        """닫힌 5분 버킷을 집계하고, 1시간마다 보존 기간이 지난 행을 지운다.

        DB 만 보므로 락 밖에서 해 보고 처리를 막지 않는다. restore 와 tick 만 부르고 둘은 겹치지 않는다.
        집계가 먼저다. 아직 집계하지 않은 원본 샘플이 있으면 지우기 전에 5분 행으로 옮긴다.
        """
        if self.repo is None:
            return
        self._aggregate(now)
        if now >= self._purge_due:
            self._purge(now)
            self._purge_due = now + PURGE_INTERVAL_S

    def _aggregate(self, now: float) -> None:
        """마지막으로 집계한 버킷 다음부터 지금 닫힌 버킷까지. 버킷은 끝나고 clock_skew_max_s 가 더 지나야 닫힌다.

        시계가 그만큼 어긋난 에이전트의 샘플은 측정 시각으로 저장되므로 버킷이 끝난 뒤에 들어올 수 있다.
        더 어긋나면 수신 시각으로 저장된다 (ingest). 밀린 구간은 AGG_CHUNK_S 씩 나눠 읽는다.
        """
        until = closed_until(now, BUCKET_S, self.th.clock_skew_max_s)
        if self._agg_next is None:
            resume = self.repo.aggregation_resume()
            # 5분 행도 agg_days 가 지나면 지우므로 그보다 오래된 샘플은 집계하지 않는다
            oldest = bucket_start(now - self.cfg.server.retention.agg_days * DAY_S)
            self._agg_next = until if resume is None else max(resume, oldest)
        start, rows = self._agg_next, 0
        while self._agg_next < until:
            end = min(until, self._agg_next + AGG_CHUNK_S)
            rows += self.repo.aggregate_samples(self._agg_next, end)
            self._agg_next = end
        if until - start > BUCKET_S:
            log.info("밀린 5분 집계: 버킷 %d개, 행 %d개", (until - start) // BUCKET_S, rows)

    def _purge(self, now: float) -> None:
        keep = self.cfg.server.retention
        deleted = self.repo.purge(
            raw_before=now - keep.raw_days * DAY_S,
            agg_before=now - keep.agg_days * DAY_S,
            sessions_before=now - keep.sessions_days * DAY_S,
        )
        if any(deleted.values()):
            log.info("보존 기간 정리: %s", ", ".join(f"{t} {n}행" for t, n in deleted.items() if n))

    # ------------------------------------------------------------------ 읽기 경로

    def overview(self, now: float | None = None) -> OverviewOut:
        with self.lock:
            now = self.clock() if now is None else now
            nodes = [self._node_out(n, now) for n in self._nodes_in_order()]
            summary = Summary(
                nodes=len(nodes),
                nodes_offline=sum(1 for n in nodes if n.conn_status == "offline"),
                gpus=sum(n.gpu_count for n in nodes),
                gpus_free=sum(n.free_gpu_count for n in nodes),
                gpus_unknown_owner=sum(
                    1 for n in nodes for g in n.gpus if any(w.owner is None for w in g.workloads)
                ),
                workloads=len(self.tracker.open_workloads()),
            )
            notices = [
                NoticeOut(id=n.id, text=n.text, level=n.level, until=n.until)
                for n in self.notices
                if n.shown(now)
            ]
            return OverviewOut(generated_ts=now, summary=summary, nodes=nodes, notices=notices)

    def node_view(self, name: str, now: float | None = None) -> NodeOut | None:
        with self.lock:
            node = self.state.nodes.get(name)
            if node is None:
                return None
            return self._node_out(node, self.clock() if now is None else now)

    def node_list(self, now: float | None = None) -> list[NodeBrief]:
        """관리용 노드 목록. 대시보드와 같은 순서이고 GPU 는 수만 센다."""
        with self.lock:
            now = self.clock() if now is None else now
            return [
                NodeBrief(
                    key=n.key,
                    display_name=n.display_name or n.key,
                    hostname=n.info.name,
                    node_id=n.info.node_id,
                    seq=n.seq,
                    ip=n.info.ip,
                    gpu_count=len(n.gpus),
                    conn_status=conn_status(
                        n.received_ts, now, self.th, n.info.report_interval_s or 0.0
                    ),
                    last_received_ts=n.received_ts,
                    age_s=max(0.0, now - n.received_ts),
                )
                for n in self._nodes_in_order()
            ]

    def workloads_view(
        self,
        now: float | None = None,
        *,
        owner: str | None = None,
        node: str | None = None,
        type_: str | None = None,
        project: str | None = None,
        status: str | None = None,
    ) -> WorkloadsOut:
        with self.lock:
            now = self.clock() if now is None else now
            out = []
            for w in self.tracker.open_workloads():
                if owner == UNKNOWN_USER:
                    if w.owner is not None:
                        continue
                elif owner is not None and w.owner != owner:
                    continue
                if node is not None and w.node != node:
                    continue
                if type_ is not None and w.type != type_:
                    continue
                if project is not None and w.project != project:
                    continue
                if status is not None and w.status != status:
                    continue
                out.append(self._workload_out(w, now))
            out.sort(key=lambda w: (w.node, w.started_ts))
            return WorkloadsOut(generated_ts=now, workloads=out)

    def workload_view(self, workload_id: str, now: float | None = None) -> WorkloadOut | None:
        with self.lock:
            w = self.tracker.workloads.get(workload_id)
            if w is None:
                return None
            return self._workload_out(w, self.clock() if now is None else now)

    def users_view(self, now: float | None = None) -> UsersOut:
        with self.lock:
            now = self.clock() if now is None else now
            users = self._user_rows()
            return UsersOut(generated_ts=now, users=users)

    def user_view(self, username: str, now: float | None = None) -> UserDetailOut | None:
        with self.lock:
            now = self.clock() if now is None else now
            key = None if username == UNKNOWN_USER else username
            row = next((u for u in self._user_rows() if u.username == key), None)
            if row is None:
                return None
            workloads = self.workloads_view(now, owner=username).workloads
            return UserDetailOut(generated_ts=now, user=row, workloads=workloads)

    # ------------------------------------------------------------------ 이력 조회

    def gpu_history(self, uuid: str, start: float, end: float, step: Step) -> GpuHistoryOut | None:
        """GPU 하나의 [start, end) 이력. 데이터가 있는 점만 돌려준다. gpus 표에 없는 GPU 면 None.

        raw 는 원본 샘플, 5m·1h 는 그 구간에 걸친 버킷이다. DB 만 읽으므로 락을 잡지 않는다.
        """
        if self.repo is None or not self.repo.has_gpu(uuid):
            return None
        if step == "raw":
            points = [raw_point(s) for s in self.repo.samples(uuid, start, end)]
        else:
            size = STEP_S[step]
            points = self.repo.buckets([uuid], bucket_start(start, size), end)[uuid]
            if size != BUCKET_S:
                points = rollup(points, size)
        return GpuHistoryOut(gpu_uuid=uuid, step=step, points=[_history_point(p) for p in points])

    def node_history(
        self, key: str, range_: Range, now: float | None = None
    ) -> NodeHistoryOut | None:
        """노드의 GPU 별 이력. 24h 는 5분 288점, 7d 는 1시간 168점이고 집계까지 끝난 마지막 구간에서 끝난다.

        닫힌 버킷은 다음 tick 이 집계하므로 그 사이(최대 10초)에는 한 칸 앞에서 끝난다. 1시간은 5분 버킷
        12개가 다 집계돼야 넣는다. 데이터가 없는 칸도 값이 null 인 점으로 채운다. GPU 는 gpus 표에서
        읽으므로 지금 보고에서 빠진 GPU 도 나온다. 모르는 노드면 None. 노드 정보만 락 안에서 읽고
        DB 는 락 밖에서 읽는다.
        """
        with self.lock:
            node = self.state.nodes.get(key)
            if node is None or self.repo is None:
                return None
            display_name = node.display_name or key
            now = self.clock() if now is None else now
        size, count = RANGES[range_]
        end = closed_until(now, size, self.th.clock_skew_max_s)
        done = self._agg_next  # 이 앞 버킷은 집계가 끝났다. 시작 때 집계 위치를 못 찾았으면 None
        if done is not None:
            end = min(end, bucket_start(done, size))
        start = end - size * count
        gpus = self.repo.node_gpus(key)
        rows = self.repo.buckets([g.uuid for g in gpus], start, end)
        series = []
        for g in gpus:
            points = rows[g.uuid] if size == BUCKET_S else rollup(rows[g.uuid], size)
            series.append(
                GpuSeries(
                    uuid=g.uuid,
                    index=g.index,
                    name=g.name,
                    mem_total_mb=g.mem_total_mb,
                    points=[_history_point(p) for p in fill(points, start, size, count)],
                )
            )
        return NodeHistoryOut(
            key=key,
            display_name=display_name,
            range=range_,
            step_s=size,
            from_ts=start,
            to_ts=end,
            gpus=series,
        )

    # ------------------------------------------------------------------ 뷰 조립

    def _nodes_in_order(self) -> list[NodeState]:
        """대시보드 순서: 처음 본 순서(seq), 같으면 식별자."""
        return sorted(self.state.nodes.values(), key=lambda n: (n.seq or 0, n.key))

    def _node_out(self, node: NodeState, now: float) -> NodeOut:
        conn = conn_status(node.received_ts, now, self.th, node.info.report_interval_s or 0.0)
        gpus = [
            self._gpu_out(node, g, conn, now)
            for g in sorted(node.gpus.values(), key=lambda g: g.report.index)
        ]
        return NodeOut(
            key=node.key,
            display_name=node.display_name or node.key,
            hostname=node.info.name,
            node_id=node.info.node_id,
            ip=node.info.ip,
            conn_status=conn,
            last_received_ts=node.received_ts,
            last_measured_ts=node.measured_ts,
            age_s=max(0.0, now - node.received_ts),
            report_interval_s=node.info.report_interval_s,
            boot_id=node.info.boot_id,
            driver_version=node.info.driver_version,
            cuda_version=node.info.cuda_version,
            agent_version=node.info.agent_version,
            cpu_percent=node.info.cpu_percent,
            mem_used_mb=node.info.mem_used_mb,
            mem_total_mb=node.info.mem_total_mb,
            clock_skew_s=node.clock_skew_s,
            clock_skew_flag=node.clock_skew_flag,
            errors=list(node.errors),
            gpu_count=len(gpus),
            free_gpu_count=sum(1 for g in gpus if g.occupancy == "free"),
            gpus=gpus,
        )

    def _gpu_sessions(self, node: str, gpu_uuid: str) -> list[Session]:
        """화면과 샘플의 occupied 가 보는 GPU 의 열린 세션: 이 노드의 것과, 다른 노드가 보고 중인(active) 것.

        프로세스 하나에 열린 세션은 노드가 달라도 하나다 (SessionTracker.apply_report). 식별자가 바뀐 직후 옛
        노드가 아직 보고 중이거나 한 머신에서 에이전트가 둘 돌면 이 노드에는 그 프로세스의 세션이 없고, 새 노드가
        이어받은 뒤의 옛 노드도 같다. 그래도 같은 GPU 이므로 쓰이는 GPU 로 보인다. 끊긴 다른 노드의 미확인
        세션은 이 노드가 보고하지 않은 프로세스라 넣지 않는다.
        """
        return [
            s
            for s in self.tracker.open_sessions_on_gpu(gpu_uuid)
            if s.node == node or s.status == "active"
        ]

    def _gpu_out(self, node: NodeState, gpu: GpuState, conn: str, now: float) -> GpuOut:
        r = gpu.report
        sessions = self._gpu_sessions(node.key, r.uuid)
        by_workload: dict[str, list[Session]] = defaultdict(list)
        for s in sorted(sessions, key=lambda s: s.pid):
            by_workload[s.workload_id].append(s)
        workloads = [
            self._workload_on_gpu(self.tracker.workloads[wid], group, now)
            for wid, group in by_workload.items()
            if wid in self.tracker.workloads
        ]
        workloads.sort(key=lambda w: w.started_ts)

        occ = occupancy(w.type for w in workloads)
        act = activity(gpu.util_history, now, self.th)
        has_error = node.has_node_error() or bool(node.gpu_errors(r.index))
        hlth = health(r.temp_c, has_error, self.th)
        color = summary_color(conn, occ, r.mem_used_mb, r.mem_total_mb, r.util_gpu, False, self.th)
        # 끊긴 노드는 샘플이 오래돼 활동이 unknown 으로 나온다. 그건 수집 불가가 아니므로 뱃지에서 뺀다.
        # 빈 GPU 의 낮은 활동은 당연한 것이라 뱃지로 알릴 필요가 없다.
        act_for_badges = act if (conn != "offline" and occ != "free") else "active"
        bdg = badges(
            conn,
            act_for_badges,
            hlth,
            has_unknown_owner=any(w.owner is None for w in workloads),
            has_unknown_session=any(s.status == "unknown" for s in sessions),
            idle_hog=False,
        )
        return GpuOut(
            index=r.index,
            uuid=r.uuid,
            name=r.name,
            mem_total_mb=r.mem_total_mb,
            mem_used_mb=r.mem_used_mb,
            util_gpu=r.util_gpu,
            util_mem=r.util_mem,
            temp_c=r.temp_c,
            power_w=r.power_w,
            power_limit_w=r.power_limit_w,
            mig_enabled=r.mig_enabled,
            occupancy=occ,
            activity=act,
            health=hlth,
            color=color,
            badges=bdg,
            workloads=workloads,
        )

    def _workload_on_gpu(self, w: Workload, group: list[Session], now: float) -> WorkloadOnGpu:
        all_sessions = self.tracker.sessions_of_workload(w.id)
        mems = [s.mem_used_mb for s in group if s.mem_used_mb is not None]
        utils = [s.util_sm for s in group if s.util_sm is not None]
        started = min((s.started_ts for s in all_sessions), default=w.first_seen)
        head = group[0]
        return WorkloadOnGpu(
            workload_id=w.id,
            name=w.name,
            type=w.type,
            owner=w.owner,
            owner_display=self.resolver.display_name(w.owner),
            owner_source=w.owner_source,
            owner_slot=self.resolver.color_slot(w.owner),
            service_name=w.service_name,
            project=w.project,
            status=w.status,
            started_ts=started,
            elapsed_s=max(0.0, now - started),
            mem_used_mb=sum(mems) if mems else None,
            util_sm=max(utils) if utils else None,
            gpu_count=len({s.gpu_uuid for s in all_sessions}),
            pids=[s.pid for s in group],
            exec_username=head.exec_username,
            container_name=w.container_name,
            cmdline=head.cmdline,
        )

    def _gpu_index(self, node: str, gpu_uuid: str) -> int | None:
        state = self.state.nodes.get(node)
        if state is None:
            return None
        gpu = state.gpus.get(gpu_uuid)
        return gpu.report.index if gpu else None

    def _session_out(self, s: Session) -> SessionOut:
        return SessionOut(
            id=s.id,
            node=s.node,
            gpu_uuid=s.gpu_uuid,
            gpu_index=self._gpu_index(s.node, s.gpu_uuid),
            pid=s.pid,
            proc_start_ts=s.proc_start_ts,
            exec_username=s.exec_username,
            exec_uid=s.exec_uid,
            cmdline=s.cmdline,
            proc_type=s.proc_type,
            status=s.status,
            start_ts=s.start_ts,
            last_seen_ts=s.last_seen_ts,
            end_ts=s.end_ts,
            ended_reason=s.ended_reason,
            unknown_seconds=s.unknown_seconds,
            mem_used_mb=s.mem_used_mb,
            mem_max_mb=s.mem_max_mb,
            mem_gb_hours=s.mem_gb_hours,
            util_sm=s.util_sm,
            util_avg=s.util_avg,
        )

    def _workload_out(self, w: Workload, now: float) -> WorkloadOut:
        sessions = sorted(
            self.tracker.sessions_of_workload(w.id), key=lambda s: (s.gpu_uuid, s.pid)
        )
        started = min((s.started_ts for s in sessions), default=w.first_seen)
        uuids = sorted({s.gpu_uuid for s in sessions})
        indexes = sorted(i for i in (self._gpu_index(w.node, u) for u in uuids) if i is not None)
        return WorkloadOut(
            id=w.id,
            key=w.key,
            node=w.node,
            node_display=self._display_of(w.node),
            type=w.type,
            name=w.name,
            owner=w.owner,
            owner_display=self.resolver.display_name(w.owner),
            owner_source=w.owner_source,
            service_name=w.service_name,
            project=w.project,
            job=w.job,
            container_id=w.container_id,
            container_name=w.container_name,
            image=w.image,
            status=w.status,
            first_seen=w.first_seen,
            last_seen=w.last_seen,
            started_ts=started,
            elapsed_s=max(0.0, now - started),
            gpu_uuids=uuids,
            gpu_indexes=indexes,
            sessions=[self._session_out(s) for s in sessions],
        )

    def _display_of(self, key: str) -> str:
        node = self.state.nodes.get(key)
        return (node.display_name or key) if node else key

    def _user_rows(self) -> list[UserOut]:
        groups: dict[str | None, dict] = {}

        def bucket(key: str | None) -> dict:
            return groups.setdefault(
                key, {"workloads": 0, "gpus": set(), "mem": 0, "nodes": set(), "unknown": 0}
            )

        for w in self.tracker.open_workloads():
            sessions = self.tracker.sessions_of_workload(w.id)
            g = bucket(w.owner)
            g["workloads"] += 1
            g["gpus"] |= {(w.node, s.gpu_uuid) for s in sessions}
            g["mem"] += sum(s.mem_used_mb or 0 for s in sessions)
            g["nodes"].add(w.node)
            g["unknown"] += sum(1 for s in sessions if s.status == "unknown")
        for username in self._registered:
            bucket(username)

        rows = [
            UserOut(
                username=key,
                display_name=self.resolver.display_name(key) if key else "미확인",
                registered=key in self._registered,
                color_slot=self.resolver.color_slot(key),
                workloads=g["workloads"],
                gpus=len(g["gpus"]),
                mem_used_mb=g["mem"],
                nodes=sorted(self._display_of(k) for k in g["nodes"]),
                unknown_sessions=g["unknown"],
            )
            for key, g in groups.items()
        ]
        rows.sort(key=lambda u: (u.username is None, -u.gpus, -u.mem_used_mb, u.username or ""))
        return rows
