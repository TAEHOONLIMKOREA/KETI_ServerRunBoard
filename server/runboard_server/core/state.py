"""최신 상태 in-memory (PLAN 4.2 '최신 상태는 메모리').

대시보드 조회는 여기만 읽는다. 세션·작업은 SessionTracker 가 따로 들고 있다.
서버 프로세스가 하나(uvicorn 워커 1개)라는 전제다.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from runboard_common.schemas import GpuReport, NodeInfo, NodeReport, ReportError


@dataclass
class GpuState:
    report: GpuReport
    first_seen: float
    util_history: deque[tuple[float, int | None]] = field(default_factory=deque)

    def record(self, ts: float, history_s: float) -> None:
        self.util_history.append((ts, self.report.util_gpu))
        while self.util_history and ts - self.util_history[0][0] > history_s:
            self.util_history.popleft()


@dataclass
class NodeState:
    info: NodeInfo
    first_seen: float
    received_ts: float
    measured_ts: float
    ts: float
    clock_skew_s: float = 0.0
    clock_skew_flag: bool = False
    conn_status: str = "online"
    gpus: dict[str, GpuState] = field(default_factory=dict)
    errors: list[ReportError] = field(default_factory=list)
    last_sample_persist_ts: float = 0.0

    def has_node_error(self) -> bool:
        return any(e.scope == "node" for e in self.errors)

    def gpu_errors(self, index: int) -> list[ReportError]:
        prefix = f"gpu:{index}"
        return [e for e in self.errors if e.scope == prefix or e.scope.startswith(prefix + "/")]


class StateStore:
    def __init__(self, util_history_s: float = 120.0) -> None:
        self.nodes: dict[str, NodeState] = {}
        self.util_history_s = util_history_s

    def update(
        self,
        report: NodeReport,
        received_ts: float,
        ts: float,
        clock_skew_s: float,
        clock_skew_flag: bool,
    ) -> NodeState:
        name = report.node.name
        node = self.nodes.get(name)
        if node is None:
            node = NodeState(
                info=report.node,
                first_seen=received_ts,
                received_ts=received_ts,
                measured_ts=report.node.measured_ts,
                ts=ts,
            )
            self.nodes[name] = node
        node.info = report.node
        node.received_ts = received_ts
        node.measured_ts = report.node.measured_ts
        node.ts = ts
        node.clock_skew_s = clock_skew_s
        node.clock_skew_flag = clock_skew_flag
        node.conn_status = "online"
        node.errors = list(report.errors)

        seen: set[str] = set()
        for gpu in report.gpus:
            seen.add(gpu.uuid)
            state = node.gpus.get(gpu.uuid)
            if state is None:
                state = GpuState(report=gpu, first_seen=ts)
                node.gpus[gpu.uuid] = state
            state.report = gpu
            state.record(ts, self.util_history_s)
        for uuid in list(node.gpus):
            if uuid not in seen:
                del node.gpus[uuid]
        return node
