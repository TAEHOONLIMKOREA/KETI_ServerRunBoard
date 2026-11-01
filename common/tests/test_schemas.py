"""보고 스키마가 에이전트 보고 예시 JSON 을 그대로 받아들이는지 확인한다."""

import json

import pytest
from pydantic import ValidationError

from runboard_common.schemas import SCHEMA_VERSION, NodeReport

PLAN_EXAMPLE = {
    "node": {
        "name": "gpu01",
        "ip": "10.0.0.11",
        "boot_id": "6f1c9a2e-0000-0000-0000-000000000000",
        "driver_version": "550.54",
        "cuda_version": "12.4",
        "agent_version": "0.2.0",
        "measured_ts": 1760601000.2,
        "cpu_percent": 12.5,
        "mem_used_mb": 51200,
        "mem_total_mb": 262144,
    },
    "gpus": [
        {
            "index": 0,
            "uuid": "GPU-7b2c0000",
            "name": "NVIDIA A100 80GB PCIe",
            "mem_total_mb": 81920,
            "mem_used_mb": 40200,
            "util_gpu": 87,
            "util_mem": 60,
            "temp_c": 61,
            "power_w": 250.3,
            "power_limit_w": 300,
            "mig_enabled": False,
            "processes": [
                {
                    "pid": 12345,
                    "ppid": 12300,
                    "pgid": 12300,
                    "type": "C",
                    "mem_used_mb": 40000,
                    "util_sm": 85,
                    "exec_username": "root",
                    "exec_uid": 0,
                    "cmdline": "python train.py --cfg configs/a.yaml",
                    "proc_start_ts": 1760600000,
                    "container": {
                        "id": "ab12cd34ef56",
                        "name": "taehoon_torch",
                        "image": "pytorch/pytorch:2.4.0-cuda12.4",
                        "labels": {
                            "runboard.owner": "taehoon",
                            "runboard.project": "pbf-defect",
                            "runboard.job": "seg-train",
                        },
                    },
                }
            ],
        },
        {
            "index": 1,
            "uuid": "GPU-91e00000",
            "name": "NVIDIA A100 80GB PCIe",
            "mem_total_mb": 81920,
            "mem_used_mb": 36800,
            "util_gpu": 3,
            "util_mem": 1,
            "temp_c": 41,
            "power_w": 70.1,
            "power_limit_w": 300,
            "mig_enabled": False,
            "processes": [
                {
                    "pid": 2210,
                    "ppid": 1,
                    "pgid": 2210,
                    "type": "C",
                    "mem_used_mb": 36500,
                    "util_sm": None,
                    "exec_username": "ollama",
                    "exec_uid": 998,
                    "cmdline": "/usr/local/bin/ollama serve",
                    "proc_start_ts": 1760400000,
                    "container": None,
                }
            ],
        },
    ],
    "errors": [{"scope": "gpu:1", "item": "process_util", "code": "NOT_SUPPORTED"}],
}


def test_plan_example_round_trips():
    report = NodeReport.model_validate(PLAN_EXAMPLE)
    assert report.schema_version == SCHEMA_VERSION
    assert report.node.name == "gpu01"
    assert len(report.gpus) == 2
    ollama = report.gpus[1].processes[0]
    assert ollama.util_sm is None, "수집 불가는 0 이 아니라 None"
    assert ollama.container is None

    again = NodeReport.model_validate_json(json.dumps(report.model_dump()))
    assert again == report


def test_unknown_fields_are_ignored_for_forward_compat():
    data = json.loads(json.dumps(PLAN_EXAMPLE))
    data["node"]["future_field"] = 1
    NodeReport.model_validate(data)


@pytest.mark.parametrize(
    "path, bad",
    [
        (("node", "name"), "bad name with spaces"),
        (("gpus", 0, "util_gpu"), 101),
        (("gpus", 0, "processes", 0, "pid"), 0),
        (("gpus", 0, "processes", 0, "container", "id"), "short"),
    ],
)
def test_rejects_invalid_values(path, bad):
    data = json.loads(json.dumps(PLAN_EXAMPLE))
    target = data
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = bad
    with pytest.raises(ValidationError):
        NodeReport.model_validate(data)


def test_report_interval_is_optional_and_positive():
    data = json.loads(json.dumps(PLAN_EXAMPLE))
    assert NodeReport.model_validate(data).node.report_interval_s is None, "옛 에이전트는 안 보낸다"
    data["node"]["report_interval_s"] = 30
    assert NodeReport.model_validate(data).node.report_interval_s == 30.0
    data["node"]["report_interval_s"] = 0
    with pytest.raises(ValidationError):
        NodeReport.model_validate(data)
