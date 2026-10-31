"""최신 상태 in-memory (PLAN 4.2 '최신 상태는 메모리').

대시보드 조회는 여기만 읽는다. 세션·작업은 SessionTracker 가 따로 들고 있다.
서버 프로세스가 하나(uvicorn 워커 1개)라는 전제다.
"""

from __future__ import annotations

from collections import deque
from collections.abc import Iterable
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


def node_key(info: NodeInfo) -> str:
    """노드 식별자. 에이전트가 고정 node_id 를 보내면 그것, 아니면 노드 이름(호스트명)."""
    return info.node_id or info.name


def free_name(prefix: str, start: int, taken: set[str]) -> str:
    """taken 에 없는 첫 화면 이름 {prefix}{n} (n >= start). 순번 이름을 다른 노드가 쓰면 다음 번호로 넘어간다."""
    n = start
    while f"{prefix}{n}" in taken:
        n += 1
    return f"{prefix}{n}"


@dataclass
class NodeState:
    key: str
    info: NodeInfo
    first_seen: float
    received_ts: float
    measured_ts: float
    ts: float
    clock_skew_s: float = 0.0
    clock_skew_flag: bool = False
    conn_status: str = "online"
    display_name: str | None = None  # 화면 이름. 처음 보면 엔진이 Server_n 을 붙인다
    seq: int | None = None  # 처음 본 순서
    info_dirty: bool = (
        False  # 호스트명 등 노드 정보가 바뀌어 persist 간격을 기다리지 않고 저장해야 함
    )
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

    def restore(self, nodes: Iterable[NodeState]) -> None:
        """재시작 복원. 이미 보고한 노드는 덮어쓰지 않는다. 보고와 같은 식별자(node.key)로 넣는다."""
        for node in nodes:
            self.nodes.setdefault(node.key, node)

    def next_seq(self) -> int:
        """새 노드의 순번. 복원된 노드까지 포함해 가장 큰 순번 + 1."""
        return max((n.seq or 0 for n in self.nodes.values()), default=0) + 1

    def names_in_use(self, except_key: str) -> set[str]:
        """except_key 가 아닌 노드들을 가리키는 이름 (화면 이름·식별자·호스트명). 화면 이름은 이것과 겹치면 안 된다."""
        return {
            name
            for n in self.nodes.values()
            if n.key != except_key
            for name in (n.display_name, n.key, n.info.name)
            if name
        }

    def update(
        self,
        key: str,
        report: NodeReport,
        received_ts: float,
        ts: float,
        clock_skew_s: float,
        clock_skew_flag: bool,
    ) -> NodeState:
        node = self.nodes.get(key)
        if node is None:
            node = NodeState(
                key=key,
                info=report.node,
                first_seen=received_ts,
                received_ts=received_ts,
                measured_ts=report.node.measured_ts,
                ts=ts,
            )
            self.nodes[key] = node
        if node.info.name != report.node.name:
            node.info_dirty = True
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
