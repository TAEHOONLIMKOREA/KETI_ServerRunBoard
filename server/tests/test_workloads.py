import pytest

from runboard_common.schemas import ContainerInfo, ProcessReport
from runboard_server.core.owner import OwnerDecision
from runboard_server.core.workloads import script_name, workload_key, workload_name

CID = "ab12cd34ef56" + "0" * 52


def decision(**kw) -> OwnerDecision:
    base = dict(workload_type="personal", owner=None, source="none")
    base.update(kw)
    return OwnerDecision(**base)


def test_container_processes_share_key_regardless_of_pgid():
    c = ContainerInfo(id=CID, name="taehoon_torch")
    a = ProcessReport(pid=1, pgid=10, container=c)
    b = ProcessReport(pid=2, pgid=20, container=c)
    assert workload_key("gpu01", "boot", a) == workload_key("gpu01", "boot", b)


def test_host_processes_group_by_pgid_and_boot():
    a = ProcessReport(pid=5100, pgid=5100)
    b = ProcessReport(pid=5101, pgid=5100)
    c = ProcessReport(pid=6000, pgid=6000)
    assert workload_key("gpu01", "boot", a) == workload_key("gpu01", "boot", b)
    assert workload_key("gpu01", "boot", a) != workload_key("gpu01", "boot", c)
    assert workload_key("gpu01", "boot", a) != workload_key("gpu01", "boot2", a)
    assert workload_key("gpu01", "boot", a) != workload_key("gpu02", "boot", a)


def test_missing_pgid_falls_back_to_pid():
    a = ProcessReport(pid=77)
    assert workload_key("n", "b", a).endswith(":77")


@pytest.mark.parametrize(
    "cmdline, expected",
    [
        ("python train.py --cfg a.yaml", "train.py"),
        ("python -m torch.distributed.run train.py --rank 0", "train.py"),
        ("/home/x/.venv/bin/python -m ipykernel_launcher -f k.json", "ipykernel_launcher"),
        ("/usr/local/bin/ollama serve", "ollama"),
        ("bash /opt/run.sh", "run.sh"),
        ("", None),
    ],
)
def test_script_name(cmdline, expected):
    assert script_name(cmdline) == expected


def test_workload_name_priority():
    c = ContainerInfo(id=CID, name="taehoon_torch")
    p = ProcessReport(pid=1, cmdline="python train.py", container=c)
    assert workload_name(p, decision(job="seg-train")) == "seg-train"
    assert workload_name(p, decision(service_name="vllm")) == "vllm"
    assert workload_name(p, decision()) == "taehoon_torch"
    host = ProcessReport(pid=1, cmdline="python train.py")
    assert workload_name(host, decision()) == "train.py"
    assert workload_name(ProcessReport(pid=9, cmdline=""), decision()) == "pid 9"
