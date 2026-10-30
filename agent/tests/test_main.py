import json
import threading

from runboard_agent.config import AgentConfig, FakeOptions
from runboard_agent.main import run_loop


def _cfg(gpu_count: int) -> AgentConfig:
    return AgentConfig(collector="fake", fake=FakeOptions(gpu_count=gpu_count))


def test_run_loop_once_declares_active_interval(capsys):
    assert run_loop(_cfg(8), once=True, print_json=True, stop=threading.Event()) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["node"]["report_interval_s"] == 1.0, "fake 노드는 프로세스가 있어 동작 중"


def test_run_loop_once_idle_node_declares_idle_interval(capsys):
    assert run_loop(_cfg(0), once=True, print_json=True, stop=threading.Event()) == 0
    assert json.loads(capsys.readouterr().out)["node"]["report_interval_s"] == 30.0
