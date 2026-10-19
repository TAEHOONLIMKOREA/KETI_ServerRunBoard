"""수집기 팩토리."""

from __future__ import annotations

from runboard_agent.collector.base import Collector
from runboard_agent.config import AgentConfig


def make_collector(cfg: AgentConfig) -> Collector:
    if cfg.collector == "fake":
        from runboard_agent.collector.fake import FakeCollector

        return FakeCollector(
            node_name=cfg.node_name,
            gpu_count=cfg.fake.gpu_count,
            churn=cfg.fake.churn,
            boot_id=cfg.fake.boot_id,
        )
    if cfg.collector == "nvml":
        from runboard_agent.collector.nvml import NvmlCollector

        return NvmlCollector(node_name=cfg.node_name)
    raise ValueError(f"알 수 없는 collector: {cfg.collector}")


__all__ = ["Collector", "make_collector"]
