import os
import sys

import pytest

from runboard_agent.enrich import Enricher, container_id_from_cgroup

CID = "ab12cd34ef5601234567890123456789012345678901234567890123456789ab"


@pytest.mark.parametrize(
    "text",
    [
        f"12:pids:/docker/{CID}\n11:memory:/docker/{CID}\n",  # cgroup v1
        f"0::/system.slice/docker-{CID}.scope\n",  # cgroup v2 + systemd
        f"0::/kubepods.slice/kubepods-burstable.slice/kubepods-burstable-pod1.slice/cri-containerd-{CID}.scope\n",
        f"0::/kubepods/burstable/pod1234/{CID}\n",
        f"0::/machine.slice/libpod-{CID}.scope\n",
        f"3:cpu:/containerd/{CID}\n",
    ],
)
def test_container_id_from_cgroup(text):
    assert container_id_from_cgroup(text) == CID


@pytest.mark.parametrize(
    "text",
    [
        "0::/\n",
        "0::/user.slice/user-1000.slice/session-3.scope\n",
        "12:pids:/system.slice/sshd.service\n",
        "",
    ],
)
def test_no_container(text):
    assert container_id_from_cgroup(text) is None


def test_facts_for_current_process():
    enricher = Enricher(docker_enabled=False)
    facts = enricher.facts(os.getpid(), "gpu:0/pid:self")
    assert facts.ppid is not None and facts.proc_start_ts is not None
    assert facts.exec_username
    assert "python" in facts.cmdline.lower() or "pytest" in facts.cmdline.lower()
    if sys.platform.startswith("linux"):
        assert facts.pgid is not None and facts.exec_uid is not None
    else:
        assert facts.pgid is None and facts.container is None


def test_facts_for_missing_process():
    enricher = Enricher(docker_enabled=False)
    facts = enricher.facts(2**22 - 1, "gpu:0/pid:ghost")
    assert facts.exec_username is None and facts.cmdline == ""
    assert facts.errors and facts.errors[0].item == "process"
