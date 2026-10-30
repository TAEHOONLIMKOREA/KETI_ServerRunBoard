import pytest

from runboard_common.schemas import ContainerInfo, ProcessReport
from runboard_server.core.owner import OwnerResolver

CID = "ab12cd34ef56" + "0" * 52


def proc(**kw) -> ProcessReport:
    base = dict(pid=100, exec_username="root", exec_uid=0, cmdline="python app.py")
    base.update(kw)
    return ProcessReport(**base)


def container(
    name: str, labels: dict | None = None, image: str = "pytorch/pytorch"
) -> ContainerInfo:
    return ContainerInfo(id=CID, name=name, image=image, labels=labels or {})


@pytest.fixture
def resolver(config) -> OwnerResolver:
    return OwnerResolver(config)


def test_label_wins_and_resolves_alias(resolver):
    d = resolver.classify(proc(container=container("x", {"runboard.owner": "th"})), "gpu01")
    assert (d.workload_type, d.owner, d.source) == ("container", "taehoon", "label")


def test_label_with_unregistered_name_is_kept_as_declared(resolver):
    d = resolver.classify(proc(container=container("x", {"runboard.owner": "someone"})), "gpu01")
    assert (d.owner, d.source) == ("someone", "label")


def test_project_and_job_labels(resolver):
    labels = {"runboard.owner": "taehoon", "runboard.project": "pbf", "runboard.job": "seg"}
    d = resolver.classify(proc(container=container("x", labels)), "gpu01")
    assert (d.project, d.job) == ("pbf", "seg")


def test_container_name_rule_only_for_registered_users(resolver):
    d = resolver.classify(proc(container=container("minsu_dev")), "gpu01")
    assert (d.owner, d.source) == ("minsu", "container_rule")
    d = resolver.classify(proc(container=container("stoic_lovelace")), "gpu01")
    assert (d.workload_type, d.owner, d.source) == ("container", None, "none")


def test_shared_service_by_exec_account_with_service_owner(resolver):
    d = resolver.classify(
        proc(exec_username="ollama", exec_uid=998, cmdline="/usr/local/bin/ollama serve"), "gpu01"
    )
    assert d.workload_type == "shared_service"
    assert d.service_name == "ollama"
    assert (d.owner, d.source) == ("taehoon", "service")


def test_shared_service_by_image_without_owner_is_unknown(resolver):
    d = resolver.classify(
        proc(container=container("stoic_x", image="vllm/vllm-openai:latest")), "gpu01"
    )
    assert (d.workload_type, d.service_name, d.owner, d.source) == (
        "shared_service",
        "vllm",
        None,
        "none",
    )


def test_container_rule_beats_service_owner(resolver):
    d = resolver.classify(
        proc(container=container("taehoon_vllm", image="vllm/vllm-openai")), "gpu01"
    )
    assert d.workload_type == "shared_service"
    assert (d.owner, d.source) == ("taehoon", "container_rule")


def test_account_map_by_uid_then_by_username(resolver):
    d = resolver.classify(proc(exec_username="th", exec_uid=1001), "gpu01")
    assert (d.workload_type, d.owner, d.source) == ("personal", "taehoon", "account_map")
    d = resolver.classify(proc(exec_username="minsu", exec_uid=1002), "gpu01")
    assert (d.owner, d.source) == ("minsu", "account_map")
    d = resolver.classify(proc(exec_username="th", exec_uid=5555), "gpu01")
    assert (d.owner, d.source) == ("taehoon", "account_map"), "별칭도 계정명 매핑에 쓴다"


def test_node_specific_mapping_wins(config):
    from runboard_server.config import AccountMapping

    config.account_mappings.append(AccountMapping(node="gpu02", uid=1001, user="minsu"))
    resolver = OwnerResolver(config)
    assert resolver.classify(proc(exec_username="x", exec_uid=1001), "gpu02").owner == "minsu"
    assert resolver.classify(proc(exec_username="x", exec_uid=1001), "gpu01").owner == "taehoon"


def test_root_without_mapping_is_unknown(resolver):
    d = resolver.classify(proc(exec_username="root", exec_uid=0), "gpu01")
    assert (d.workload_type, d.owner, d.source) == ("personal", None, "none")


def test_unregistered_host_user_is_unknown(resolver):
    d = resolver.classify(proc(exec_username="guest", exec_uid=4242), "gpu01")
    assert (d.owner, d.source) == (None, "none")


def test_display_name(resolver):
    assert resolver.display_name("taehoon") == "임태훈"
    assert resolver.display_name("someone") == "someone"
    assert resolver.display_name(None) is None


def test_color_slot_follows_users_order(resolver):
    assert [resolver.color_slot(u) for u in ("taehoon", "minsu", "jihye")] == [0, 1, 2]
    assert resolver.color_slot("someone") is None and resolver.color_slot(None) is None
