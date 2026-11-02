"""세션과 작업의 상태 전이.

세션 = (프로세스, GPU) 한 쌍의 관측된 점유 구간. 키는 (노드, GPU UUID, 부팅 ID, PID, 프로세스 시작 시각).
작업 = 세션의 묶음 (컨테이너 또는 프로세스 그룹).
프로세스 하나(키에서 노드를 뺀 것)에 열린 세션은 노드가 달라도 하나다. DB 의 uq_session_open_key 와 같다.
재부팅 없이 에이전트의 식별자가 바뀌면 같은 프로세스가 새 노드 키로 보이므로 새 노드가 옛 세션을 이어받는다.
그래서 GPU 에 열린 세션은 노드가 아니라 GPU UUID 로 찾는다 (open_sessions_on_gpu).

| 사건 | 전이 |
|---|---|
| 보고에 새 키 등장 | 세션 생성 active, 작업에 연결 |
| 새 키의 프로세스에 다른 노드의 unknown 세션이 열려 있음 | 그 세션 ended, 사유 node_replaced. 그다음 세션 생성 |
| 새 키의 프로세스에 다른 노드의 active 세션이 열려 있음 | 세션을 만들지 않는다. 그 노드가 offline 이 된 뒤의 보고가 이어받는다 |
| 같은 키가 N회 연속 보고에서 빠짐 | ended, 사유 exited, end_ts = 마지막 측정 시각 |
| 노드 offline | 그 노드의 active 세션 → unknown |
| 노드 복귀, 같은 키 있음 | unknown → active, 공백을 unknown_seconds 에 누적 |
| 노드 복귀, 같은 키 없음 | ended, 사유 lost_while_offline |
| 부팅 ID 변경 | 열린 세션 전부 ended, 사유 node_rebooted |
| offline 24시간 초과 | unknown 세션 ended, 사유 node_offline_timeout |
| 관리 화면에서 노드 삭제 | 열린 세션 전부 ended, 사유 node_removed |

메모리 점유량(GB·시간)과 평균 사용률은 보고마다 세션에 누적한다. unknown 구간은 누적하지 않는다.
등록 정보(사용자·매핑·서비스)가 바뀌면 열린 작업의 유형·소유자를 다음 보고 때 다시 판정한다.
DB 에 쓰지 못한 것은 requeue 로 돌려받아 다음 전체 take_dirty 나 전이가 있는 take_dirty 가 함께 다시 내준다.
"""

from __future__ import annotations

import logging
import uuid
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Literal

from runboard_common.schemas import NodeReport, ProcessReport
from runboard_server.core.owner import OwnerDecision
from runboard_server.core.workloads import workload_key, workload_name

log = logging.getLogger(__name__)

SessionStatus = Literal["active", "unknown", "ended"]
EndedReason = Literal[
    "exited",
    "lost_while_offline",
    "node_rebooted",
    "node_offline_timeout",
    "node_removed",
    "node_replaced",
]
SessionKey = tuple[str, str, str, int, float | None]
ProcKey = tuple[str, str, int, float | None]  # (GPU UUID, 부팅 ID, PID, 프로세스 시작 시각)

MAX_ACCUMULATE_DT_S = 60.0


def _new_id() -> str:
    return uuid.uuid4().hex


def _proc_key(key: SessionKey) -> ProcKey:
    """세션 키에서 노드를 뺀 것. 노드 식별자가 바뀌어도 같은 프로세스면 같다."""
    return key[1:]


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
        # GPU UUID → 프로세스 → 그 프로세스의 열린 세션. 노드가 달라도 프로세스 하나에 하나만 연다 (apply_report)
        self._open_by_gpu: dict[str, dict[ProcKey, SessionKey]] = defaultdict(dict)
        self._node_boot: dict[str, str] = {}
        # 상태가 바뀐 것(생성·종료·미확인·복귀). take_dirty(transitions_only=True) 가 이것만 쓴다
        self._transitions: set[SessionKey] = set()
        self._wl_transitions: set[str] = set()
        # 등록 정보가 바뀌어 다음 보고 때 다시 판정할 열린 작업
        self._reclassify: set[str] = set()
        # 다른 노드의 active 세션 때문에 세션을 만들지 않는다고 경고한 (노드, 세션을 가진 노드)
        self._blocked_warned: set[tuple[str, str]] = set()
        # DB 에 쓰지 못해 돌려받은 것 (requeue). 다음 전체 take_dirty 나 전이가 있는 take_dirty 가 다시 내준다
        self._retry_workloads: dict[str, Workload] = {}
        self._retry_sessions: dict[str, Session] = {}

    # ------------------------------------------------------------------ 조회

    def open_sessions(self, node: str | None = None) -> list[Session]:
        if node is None:
            return [s for s in self.sessions.values() if s.is_open]
        return [self.sessions[k] for k in self._open_by_node.get(node, ())]

    def open_sessions_on_gpu(self, gpu_uuid: str) -> list[Session]:
        """그 GPU 의 열린 세션. 어느 노드의 세션이든 넣는다. GPU UUID 는 물리 GPU 마다 하나다."""
        return [self.sessions[k] for k in self._open_by_gpu.get(gpu_uuid, {}).values()]

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
        replaced: dict[str, int] = defaultdict(int)  # 세션을 넘겨준 노드 → 이어받은 세션 수
        for gpu in report.gpus:
            for proc in gpu.processes:
                key: SessionKey = (node, gpu.uuid, boot, proc.pid, proc.proc_start_ts)
                seen.add(key)
                session = self.sessions.get(key)
                if session is None:
                    holder = self._holder(key)
                    if holder is not None:
                        if holder.status == "active":
                            # 그 노드가 아직 보고 중이다. 여기서 이어받으면 한 머신에 에이전트가 둘 돌 때
                            # 두 노드가 보고마다 세션을 번갈아 새로 연다. 그 노드가 offline 이 되면 이어받는다
                            self._warn_blocked(node, holder.node)
                            continue
                        self._end(holder, "node_replaced")
                        replaced[holder.node] += 1
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
                    self._open_by_gpu[gpu.uuid][_proc_key(key)] = key
                    self._transitions.add(key)
                    self._touch(session, proc, ts, first=True)
                else:
                    self._touch(session, proc, ts)
                    if session.workload_id in self._reclassify:
                        recheck[session.workload_id].append(proc)

        for old, count in replaced.items():
            log.info(
                "노드 %s 의 세션 %d개를 노드 %s 가 이어받는다 (같은 프로세스. 식별자가 바뀐 것으로 본다)",
                old,
                count,
                node,
            )
            self._blocked_warned.discard((node, old))  # 다시 막히면 또 알린다
            self._refresh_workloads(old)

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

        requeue 로 돌려받은 것은 전체 호출이 늘 함께 내고, transitions_only 호출도 낼 전이가 있으면 함께 낸다.
        작업이 그 세션보다, 옛 세션의 종료가 같은 프로세스의 새 세션보다 같은 저장에서 먼저 쓰여야 외래 키와
        uq_session_open_key 에 걸리지 않는다 (Repository.save). 전이가 없으면 돌려받은 것이 있어도 빈 결과라
        그 보고는 DB 에 닿지 않는다.
        """
        if transitions_only:
            workloads = [self.workloads[i] for i in self._wl_transitions if i in self.workloads]
            sessions = [self.sessions[k] for k in self._transitions if k in self.sessions]
        else:
            workloads = [w for w in self.workloads.values() if w.dirty]
            sessions = [s for s in self.sessions.values() if s.dirty]
        if not transitions_only or workloads or sessions:
            # 돌려받은 것이 그사이 다시 dirty 가 됐을 수 있다. 같은 객체이므로 id 로 한 번만 낸다
            wl_by_id = self._retry_workloads | {w.id: w for w in workloads}
            s_by_id = self._retry_sessions | {s.id: s for s in sessions}
            self._retry_workloads, self._retry_sessions = {}, {}
            workloads, sessions = list(wl_by_id.values()), list(s_by_id.values())
        self._wl_transitions.clear()
        self._transitions.clear()
        for w in workloads:
            w.dirty = False
        # 돌려받은 끝난 세션·작업은 이미 메모리에서 빠졌고, 같은 키로 새 세션·작업이 생겼을 수 있다
        # (사라졌던 프로세스가 다시 보임). 색인이 아직 그 객체를 가리킬 때만 뺀다
        for s in sessions:
            s.dirty = False
            if s.status == "ended" and self.sessions.get(s.key) is s:
                del self.sessions[s.key]
                self._sessions_by_wl[s.workload_id].discard(s.key)
        for w in workloads:
            if (
                w.status == "ended"
                and self.workloads.get(w.id) is w
                and not self._sessions_by_wl.get(w.id)
            ):
                del self.workloads[w.id]
                if self._wl_by_key.get(w.key) is w:
                    del self._wl_by_key[w.key]
                self._sessions_by_wl.pop(w.id, None)
        return workloads, sessions

    def requeue(self, workloads: Iterable[Workload], sessions: Iterable[Session]) -> None:
        """take_dirty 가 돌려준 것을 호출한 쪽이 DB 에 쓰지 못했다. 다음 take_dirty 가 다시 돌려준다.

        같은 객체를 들고 있으므로 그때의 최신 상태가 쓰인다. 끝난 세션·작업은 이미 메모리에서 빠져 여기에만
        남는다. 다음 tick(10초), 샘플 저장, 전이가 있는 보고 중 먼저 오는 것이 쓴다. 전이도 샘플 저장도 없는
        보고는 이것을 쓰지 않는다 (호스트명이 바뀐 보고도 노드 행만 쓴다).
        """
        self._retry_workloads.update((w.id, w) for w in workloads)
        self._retry_sessions.update((s.id, s) for s in sessions)

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
            self._open_by_gpu[s.gpu_uuid][_proc_key(s.key)] = s.key
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

    def _holder(self, key: SessionKey) -> Session | None:
        """key 와 같은 프로세스의 열린 세션을 다른 노드가 가지고 있으면 그 세션."""
        held = self._open_by_gpu.get(key[1], {}).get(_proc_key(key))
        return self.sessions[held] if held is not None and held != key else None

    def _warn_blocked(self, node: str, holder: str) -> None:
        """두 노드 쌍마다 한 번만 남긴다. 이어받고 나서 다시 막히면 또 남긴다."""
        if (node, holder) in self._blocked_warned:
            return
        self._blocked_warned.add((node, holder))
        log.warning(
            "노드 %s 가 보고한 프로세스의 세션이 노드 %s 에 열려 있다. 그 노드가 비활성화되면 이어받는다 "
            "(식별자를 바꾼 것이 아니면 한 머신에서 에이전트가 둘 도는지 본다)",
            node,
            holder,
        )

    def _end(self, session: Session, reason: EndedReason) -> None:
        session.status = "ended"
        session.ended_reason = reason
        session.end_ts = session.last_seen_ts
        session.unknown_since = None
        session.dirty = True
        self._transitions.add(session.key)
        self._open_by_node[session.node].discard(session.key)
        procs = self._open_by_gpu.get(session.gpu_uuid, {})
        proc = _proc_key(session.key)
        if procs.get(proc) == session.key:
            del procs[proc]
            if not procs:
                del self._open_by_gpu[session.gpu_uuid]

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
