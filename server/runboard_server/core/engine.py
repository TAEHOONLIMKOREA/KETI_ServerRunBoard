"""보고 1건을 처리하는 파이프라인과 조회 뷰 (PLAN 4.5).

ingest: 검증된 보고 → 시계 오차 검사 → 최신 상태 갱신 → 유형·소유자 판정 → 세션·작업 전이 → 영속화
tick  : 연결 판정 → offline 노드의 세션을 unknown 으로 → 24시간 초과 정리 → 영속화

락 하나로 직렬화한다. 서버 프로세스는 하나다.
"""

from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict
from collections.abc import Callable

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
from runboard_server.core.owner import OwnerResolver
from runboard_server.core.sessions import Session, SessionTracker, Workload
from runboard_server.core.state import GpuState, NodeState, StateStore
from runboard_server.views import (
    GpuOut,
    NodeOut,
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


class Engine:
    def __init__(self, cfg: ServerConfig, repo=None, clock: Callable[[], float] = time.time):
        self.cfg = cfg
        self.th = cfg.thresholds
        self.repo = repo
        self.clock = clock
        self.resolver = OwnerResolver(cfg)
        self.state = StateStore(util_history_s=self.th.util_history_s)
        self.tracker = SessionTracker(
            miss_reports=self.th.session_miss_reports,
            offline_close_after_s=self.th.offline_close_after_s,
        )
        self.lock = threading.RLock()
        self._registered = {u.username for u in cfg.users}

    # ------------------------------------------------------------------ 쓰기 경로

    def restore(self) -> None:
        if self.repo is None:
            return
        workloads, sessions = self.repo.load_open()
        with self.lock:
            self.tracker.restore(workloads, sessions)
        log.info(
            "복원: 열린 작업 %d, 세션 %d (모두 미확인으로 시작)", len(workloads), len(sessions)
        )

    def ingest(self, report: NodeReport, received_ts: float | None = None) -> None:
        with self.lock:
            received = self.clock() if received_ts is None else received_ts
            skew = received - report.node.measured_ts
            skew_flag = abs(skew) > self.th.clock_skew_max_s
            ts = received if skew_flag else report.node.measured_ts
            node = self.state.update(report, received, ts, skew, skew_flag)
            if skew_flag:
                log.warning("노드 %s 시계 오차 %.0fs: 수신 시각으로 대체", report.node.name, skew)
            name = report.node.name
            self.tracker.apply_report(report, ts, lambda p: self.resolver.classify(p, name))
            self._persist(node, ts, received)

    def tick(self, now: float | None = None) -> None:
        with self.lock:
            now = self.clock() if now is None else now
            for node in self.state.nodes.values():
                name = node.info.name
                status = conn_status(node.received_ts, now, self.th)
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
                self.repo.save(workloads, sessions)

    def _persist(self, node: NodeState, ts: float, received: float) -> None:
        workloads, sessions = self.tracker.take_dirty()
        if self.repo is None:
            return
        due = ts - node.last_sample_persist_ts >= self.cfg.server.persist_interval_s
        if node.last_sample_persist_ts == 0.0 or due:
            self.repo.upsert_node(node)
            self.repo.insert_samples(node, ts, received)
            node.last_sample_persist_ts = ts
        self.repo.save(workloads, sessions)

    # ------------------------------------------------------------------ 읽기 경로

    def overview(self, now: float | None = None) -> OverviewOut:
        with self.lock:
            now = self.clock() if now is None else now
            nodes = [
                self._node_out(n, now)
                for n in sorted(self.state.nodes.values(), key=lambda n: n.info.name)
            ]
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
            return OverviewOut(generated_ts=now, summary=summary, nodes=nodes)

    def node_view(self, name: str, now: float | None = None) -> NodeOut | None:
        with self.lock:
            node = self.state.nodes.get(name)
            if node is None:
                return None
            return self._node_out(node, self.clock() if now is None else now)

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

    # ------------------------------------------------------------------ 뷰 조립

    def _node_out(self, node: NodeState, now: float) -> NodeOut:
        conn = conn_status(node.received_ts, now, self.th)
        gpus = [
            self._gpu_out(node, g, conn, now)
            for g in sorted(node.gpus.values(), key=lambda g: g.report.index)
        ]
        return NodeOut(
            name=node.info.name,
            ip=node.info.ip,
            conn_status=conn,
            last_received_ts=node.received_ts,
            last_measured_ts=node.measured_ts,
            age_s=max(0.0, now - node.received_ts),
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

    def _gpu_out(self, node: NodeState, gpu: GpuState, conn: str, now: float) -> GpuOut:
        r = gpu.report
        sessions = self.tracker.open_sessions_for_gpu(node.info.name, r.uuid)
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
                workloads=g["workloads"],
                gpus=len(g["gpus"]),
                mem_used_mb=g["mem"],
                nodes=sorted(g["nodes"]),
                unknown_sessions=g["unknown"],
            )
            for key, g in groups.items()
        ]
        rows.sort(key=lambda u: (u.username is None, -u.gpus, -u.mem_used_mb, u.username or ""))
        return rows
