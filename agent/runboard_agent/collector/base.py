"""수집기 인터페이스. nvml 과 fake 두 구현이 있다."""

from __future__ import annotations

from typing import Protocol

from runboard_common.schemas import NodeReport


class Collector(Protocol):
    def collect(self) -> NodeReport:
        """현재 노드 상태를 보고 1건으로 만든다. 실패한 항목은 errors 에 담고 예외를 던지지 않는다."""
        ...

    def close(self) -> None: ...
