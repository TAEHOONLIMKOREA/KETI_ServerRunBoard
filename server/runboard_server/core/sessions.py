"""세션과 작업의 상태 전이 (PLAN 5.2).

세션 = (프로세스, GPU) 한 쌍의 관측된 점유 구간. 키는 (노드, GPU UUID, 부팅 ID, PID, 프로세스 시작 시각).
작업 = 세션의 묶음 (컨테이너 또는 프로세스 그룹).

| 사건 | 전이 |
|---|---|
| 보고에 새 키 등장 | 세션 생성 active, 작업에 연결 |
| 같은 키가 N회 연속 보고에서 빠짐 | ended, 사유 exited, end_ts = 마지막 측정 시각 |
| 노드 offline | 그 노드의 active 세션 → unknown |
| 노드 복귀, 같은 키 있음 | unknown → active, 공백을 unknown_seconds 에 누적 |
| 노드 복귀, 같은 키 없음 | ended, 사유 lost_while_offline |
| 부팅 ID 변경 | 열린 세션 전부 ended, 사유 node_rebooted |
| offline 24시간 초과 | unknown 세션 ended, 사유 node_offline_timeout |
| 관리 화면에서 노드 삭제 | 열린 세션 전부 ended, 사유 node_removed |

메모리 점유량(GB·시간)과 평균 사용률은 보고마다 세션에 누적한다. unknown 구간은 누적하지 않는다.
등록 정보(사용자·매핑·서비스)가 바뀌면 열린 작업의 유형·소유자를 다음 보고 때 다시 판정한다.
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from runboard_common.schemas import NodeReport, ProcessReport
from runboard_server.core.owner import OwnerDecision
from runboard_server.core.workloads import workload_key, workload_name

SessionStatus = Literal["active", "unknown", "ended"]
EndedReason = Literal[
    "exited", "lost_while_offline", "node_rebooted", "node_offline_timeout", "node_removed"
]
SessionKey = tuple[str, str, str, int, float | None]

MAX_ACCUMULATE_DT_S = 60.0


def _new_id() -> str:
    return uuid.uuid4().hex


@dataclass
class Workload:
    id: str
    key: str
    node: str
    type: str
    name: str
    first_seen: float
    last_seen: float
    container_id: str | None = None
    container_name: str | None = None
    image: str | None = None
    project: str | None = None
    job: str | None = None
    owner: str | None = None
    owner_source: str = "none"
    service_name: str | None = None
    status: SessionStatus = "active"
    end_ts: float | None = None
    dirty: bool = True


@dataclass
class Session:
    id: str
    node: str
    gpu_uuid: str
    boot_id: str
    pid: int
    proc_start_ts: float | None
    workload_id: str
    start_ts: float
    last_seen_ts: float
    exec_username: str | None = None
    exec_uid: int | None = None
    cmdline: str = ""
    proc_type: str = "C"
    status: SessionStatus = "active"
    end_ts: float | None = None
    ended_reason: EndedReason | None = None
    unknown_since: float | None = None
    unknown_seconds: float = 0.0
    mem_used_mb: int | None = None
    mem_max_mb: int = 0
    mem_gb_hours: float = 0.0
    util_sm: int | None = None
    util_sum: float = 0.0
    util_samples: int = 0
    miss_count: int = 0
    dirty: bool = field(default=True, compare=False)

    @property
    def key(self) -> SessionKey:
        return (self.node, self.gpu_uuid, self.boot_id, self.pid, self.proc_start_ts)

    @property
    def util_avg(self) -> float | None:
        return self.util_sum / self.util_samples if self.util_samples else None

    @property
    def is_open(self) -> bool:
        return self.status != "ended"

    @property
    def started_ts(self) -> float:
        """화면에 보여줄 시작 시각. 프로세스 시작 시각이 있으면 그것, 없으면 첫 관측."""
        return self.proc_start_ts if self.proc_start_ts is not None else self.start_ts


class SessionTracker:
    def __init__(
        self,
        miss_reports: int = 2,
        offline_close_after_s: float = 86400.0,
        max_dt_s: float = MAX_ACCUMULATE_DT_S,
    ) -> None:
        self.miss_reports = max(1, miss_reports)
        self.offline_close_after_s = offline_close_after_s
        self.max_dt_s = max_dt_s
        self.sessions: dict[SessionKey, Session] = {}
        self.workloads: dict[str, Workload] = {}
        self._wl_by_key: dict[str, Workload] = {}
        self._sessions_by_wl: dict[str, set[SessionKey]] = defaultdict(set)
        self._open_by_node: dict[str, set[SessionKey]] = defaultdict(set)
        self._node_boot: dict[str, str] = {}
        # 상태가 바뀐 것(생성·종료·미확인·복귀). take_dirty(transitions_only=True) 가 이것만 쓴다
        self._transitions: set[SessionKey] = set()
        self._wl_transitions: set[str] = set()
        # 등록 정보가 바뀌어 다음 보고 때 다시 판정할 열린 작업
        self._reclassify: set[str] = set()

    # ------------------------------------------------------------------ 조회

    def open_sessions(self, node: str | None = None) -> list[Session]:
        if node is None:
            return [s for s in self.sessions.values() if s.is_open]
        return [self.sessions[k] for k in self._open_by_node.get(node, ())]

    def open_sessions_for_gpu(self, node: str, gpu_uuid: str) -> list[Session]:
        return [s for s in self.open_sessions(node) if s.gpu_uuid == gpu_uuid]

    def open_workloads(self, node: str | None = None) -> list[Workload]:
        return [
            w
            for w in self.workloads.values()
            if w.status != "ended" and (node is None or w.node == node)
        ]

    def sessions_of_workload(self, workload_id: str, open_only: bool = True) -> list[Session]:
        keys = self._sessions_by_wl.get(workload_id, ())
        out = [self.sessions[k] for k in keys if k in self.sessions]
        return [s for s in out if s.is_open] if open_only else out

    # ------------------------------------------------------------------ 전이

    def apply_report(
        self,
        report: NodeReport,
        ts: float,
        decide: Callable[[ProcessReport], OwnerDecision],
        node: str | None = None,
    ) -> None:
        """node 는 노드 식별자. 없으면 보고의 노드 이름을 쓴다 (옛 호출·테스트)."""
        node = node or report.node.name
        boot = report.node.boot_id
        previous_boot = self._node_boot.get(node)
        if previous_boot is not None and previous_boot != boot:
            self._end_all(node, "node_rebooted")
        self._node_boot[node] = boot

        seen: set[SessionKey] = set()
        recheck: dict[str, list[ProcessReport]] = defaultdict(list)
        for gpu in report.gpus:
            for proc in gpu.processes:
                key: SessionKey = (node, gpu.uuid, boot, proc.pid, proc.proc_start_ts)
                seen.add(key)
                session = self.sessions.get(key)
                if session is None:
                    decision = decide(proc)
                    workload = self._get_or_create_workload(node, boot, proc, decision, ts)
                    session = Session(
                        id=_new_id(),
                        node=node,
                        gpu_uuid=gpu.uuid,
                        boot_id=boot,
                        pid=proc.pid,
                        proc_start_ts=proc.proc_start_ts,
                        workload_id=workload.id,
                        start_ts=ts,
                        last_seen_ts=ts,
                        exec_username=proc.exec_username,
                        exec_uid=proc.exec_uid,
                        cmdline=proc.cmdline,
                        proc_type=proc.type,
                    )
                    self.sessions[key] = session
                    self._sessions_by_wl[workload.id].add(key)
                    self._open_by_node[node].add(key)
                    self._transitions.add(key)
                    self._touch(session, proc, ts, first=True)
                else:
                    self._touch(session, proc, ts)
                    if session.workload_id in self._reclassify:
                        recheck[session.workload_id].append(proc)

        for workload_id, procs in recheck.items():
            self._reclassify.discard(workload_id)
            if workload_id in self.workloads:
                self._redecide(self.workloads[workload_id], procs, decide)

        for key in list(self._open_by_node.get(node, ())):
            if key in seen:
                continue
            session = self.sessions[key]
            if session.status == "unknown":
                self._end(session, "lost_while_offline")
            else:
                session.miss_count += 1
                session.dirty = True
                if session.miss_count >= self.miss_reports:
                    self._end(session, "exited")

        self._refresh_workloads(node)

    def mark_reclassify(self) -> None:
        """등록 정보가 바뀌었다. 열린 작업마다 다음 보고의 프로세스로 유형·소유자를 다시 판정한다.

        세션은 처음 볼 때만 판정하므로, 이것이 없으면 매핑을 추가해도 이미 돌고 있는 작업은
        프로세스가 끝날 때까지 미확인으로 남는다.
        """
        self._reclassify = {w.id for w in self.workloads.values() if w.status != "ended"}

    def end_node(self, node: str, reason: EndedReason) -> None:
        """노드를 지울 때. 열린 세션을 전부 끝낸다. 같은 노드가 다시 보고하면 새 세션으로 시작한다."""
        self._end_all(node, reason)
        self._node_boot.pop(node, None)

    def mark_node_offline(self, node: str, now: float) -> None:
        for key in self._open_by_node.get(node, ()):
            session = self.sessions[key]
            if session.status == "active":
                session.status = "unknown"
                session.unknown_since = session.last_seen_ts
                session.dirty = True
                self._transitions.add(key)
        self._refresh_workloads(node)

    def close_offline_timeouts(self, node: str, now: float) -> int:
        closed = 0
        for key in list(self._open_by_node.get(node, ())):
            session = self.sessions[key]
            since = session.unknown_since
            if (
                session.status == "unknown"
                and since is not None
                and now - since > self.offline_close_after_s
            ):
                self._end(session, "node_offline_timeout")
                closed += 1
        if closed:
            self._refresh_workloads(node)
        return closed

    # ------------------------------------------------------------------ 영속화 보조

    def take_dirty(self, transitions_only: bool = False) -> tuple[list[Workload], list[Session]]:
        """바뀐 작업·세션을 돌려주고 플래그를 내린다. 끝난 세션·작업은 메모리에서 뺀다.

        transitions_only 면 상태가 바뀐 것(생성·종료·미확인·복귀, 작업 상태 변화)만 돌려준다. 누적값만
        바뀐 것은 dirty 로 남겨 두었다가 다음 전체 호출 때 쓴다. 보고가 1초마다 올 때 DB 쓰기를 줄이기 위해서다.
        """
        if transitions_only:
            workloads = [self.workloads[i] for i in self._wl_transitions if i in self.workloads]
            sessions = [self.sessions[k] for k in self._transitions if k in self.sessions]
        else:
            workloads = [w for w in self.workloads.values() if w.dirty]
            sessions = [s for s in self.sessions.values() if s.dirty]
        self._wl_transitions.clear()
        self._transitions.clear()
        for w in workloads:
            w.dirty = False
        for s in sessions:
            s.dirty = False
            if s.status == "ended":
                self.sessions.pop(s.key, None)
                self._sessions_by_wl[s.workload_id].discard(s.key)
        for w in workloads:
            if w.status == "ended" and not self._sessions_by_wl.get(w.id):
                self.workloads.pop(w.id, None)
                self._wl_by_key.pop(w.key, None)
                self._sessions_by_wl.pop(w.id, None)
        return workloads, sessions

    def restore(self, workloads: Iterable[Workload], sessions: Iterable[Session]) -> None:
        """재시작 시 DB 의 열린 작업·세션을 되살린다. 실제 상태는 모르므로 전부 unknown 으로 둔다."""
        for w in workloads:
            w.dirty = False
            self.workloads[w.id] = w
            self._wl_by_key[w.key] = w
        for s in sessions:
            if s.status == "ended":
                continue
            if s.status == "active":
                s.status = "unknown"
                s.unknown_since = s.last_seen_ts
            s.dirty = True
            self._transitions.add(s.key)
            self.sessions[s.key] = s
            self._sessions_by_wl[s.workload_id].add(s.key)
            self._open_by_node[s.node].add(s.key)
            self._node_boot.setdefault(s.node, s.boot_id)
        for node in list(self._open_by_node):
            self._refresh_workloads(node)

    # ------------------------------------------------------------------ 내부

    def _get_or_create_workload(
        self, node: str, boot: str, proc: ProcessReport, decision: OwnerDecision, ts: float
    ) -> Workload:
        key = workload_key(node, boot, proc)
        workload = self._wl_by_key.get(key)
        if workload is not None and workload.status != "ended":
            if workload.owner is None and decision.owner is not None:
                workload.owner = decision.owner
                workload.owner_source = decision.source
                workload.dirty = True
                self._wl_transitions.add(workload.id)
            return workload
        container = proc.container
        workload = Workload(
            id=_new_id(),
            key=key,
            node=node,
            type=decision.workload_type,
            name=workload_name(proc, decision),
            first_seen=ts,
            last_seen=ts,
            container_id=container.id if container else None,
            container_name=container.name if container else None,
            image=container.image if container else None,
            project=decision.project,
            job=decision.job,
            owner=decision.owner,
            owner_source=decision.source,
            service_name=decision.service_name,
        )
        self.workloads[workload.id] = workload
        self._wl_by_key[key] = workload
        self._wl_transitions.add(workload.id)
        return workload

    def _redecide(
        self,
        workload: Workload,
        procs: list[ProcessReport],
        decide: Callable[[ProcessReport], OwnerDecision],
    ) -> None:
        """작업을 만들 때처럼 소유자가 나오는 프로세스를 먼저 쓴다. 바뀐 것이 있으면 전이로 저장한다."""
        decisions = [(proc, decide(proc)) for proc in procs]
        proc, decision = next(((p, d) for p, d in decisions if d.owner is not None), decisions[0])
        values = {
            "type": decision.workload_type,
            "name": workload_name(proc, decision),
            "owner": decision.owner,
            "owner_source": decision.source,
            "service_name": decision.service_name,
            "project": decision.project,
            "job": decision.job,
        }
        if all(getattr(workload, k) == v for k, v in values.items()):
            return
        for k, v in values.items():
            setattr(workload, k, v)
        workload.dirty = True
        self._wl_transitions.add(workload.id)

    def _touch(self, session: Session, proc: ProcessReport, ts: float, first: bool = False) -> None:
        if not first:
            if session.status == "unknown":
                gap_start = session.unknown_since or session.last_seen_ts
                session.unknown_seconds += max(0.0, ts - gap_start)
                session.unknown_since = None
                session.status = "active"
                self._transitions.add(session.key)
            else:
                dt = min(self.max_dt_s, max(0.0, ts - session.last_seen_ts))
                if proc.mem_used_mb is not None:
                    session.mem_gb_hours += (proc.mem_used_mb / 1024) * dt / 3600
        session.miss_count = 0
        session.last_seen_ts = ts
        session.mem_used_mb = proc.mem_used_mb
        if proc.mem_used_mb is not None:
            session.mem_max_mb = max(session.mem_max_mb, proc.mem_used_mb)
        session.util_sm = proc.util_sm
        if proc.util_sm is not None:
            session.util_sum += proc.util_sm
            session.util_samples += 1
        session.dirty = True

    def _end(self, session: Session, reason: EndedReason) -> None:
        session.status = "ended"
        session.ended_reason = reason
        session.end_ts = session.last_seen_ts
        session.unknown_since = None
        session.dirty = True
        self._transitions.add(session.key)
        self._open_by_node[session.node].discard(session.key)

    def _end_all(self, node: str, reason: EndedReason) -> None:
        for key in list(self._open_by_node.get(node, ())):
            self._end(self.sessions[key], reason)
        self._refresh_workloads(node)

    def _refresh_workloads(self, node: str) -> None:
        for workload in self.workloads.values():
            if workload.node != node or workload.status == "ended":
                continue
            sessions = [
                self.sessions[k]
                for k in self._sessions_by_wl.get(workload.id, ())
                if k in self.sessions
            ]
            statuses = {s.status for s in sessions}
            if "active" in statuses:
                new_status: SessionStatus = "active"
            elif "unknown" in statuses:
                new_status = "unknown"
            else:
                new_status = "ended"
            last_seen = max((s.last_seen_ts for s in sessions), default=workload.last_seen)
            if new_status != workload.status:
                self._wl_transitions.add(workload.id)
            if new_status != workload.status or last_seen != workload.last_seen:
                workload.status = new_status
                workload.last_seen = last_seen
                if new_status == "ended":
                    workload.end_ts = max(
                        (s.end_ts for s in sessions if s.end_ts is not None), default=last_seen
                    )
                workload.dirty = True
