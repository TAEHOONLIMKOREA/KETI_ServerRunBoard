import pytest

from runboard_common.schemas import ContainerInfo, ProcessReport
from runboard_server.config import OwnerRules
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
def resolver(config, registry) -> OwnerResolver:
    return OwnerResolver(config.owner, registry)


def test_label_wins_and_resolves_alias(resolver):
    d = resolver.classify(proc(container=container("x", {"runboard.owner": "th"})))
    assert (d.workload_type, d.owner, d.source) == ("container", "taehoon", "label")


def test_label_with_unregistered_name_is_kept_as_declared(resolver):
    d = resolver.classify(proc(container=container("x", {"runboard.owner": "someone"})))
    assert (d.owner, d.source) == ("someone", "label")


def test_project_and_job_labels(resolver):
    labels = {"runboard.owner": "taehoon", "runboard.project": "pbf", "runboard.job": "seg"}
    d = resolver.classify(proc(container=container("x", labels)))
    assert (d.project, d.job) == ("pbf", "seg")


def test_container_name_rule_only_for_registered_users(resolver):
    d = resolver.classify(proc(container=container("minsu_dev")))
    assert (d.owner, d.source) == ("minsu", "container_rule")
    d = resolver.classify(proc(container=container("stoic_lovelace")))
    assert (d.workload_type, d.owner, d.source) == ("container", None, "none")


def test_shared_service_by_exec_account_with_service_owner(resolver):
    d = resolver.classify(
        proc(exec_username="ollama", exec_uid=998, cmdline="/usr/local/bin/ollama serve")
    )
    assert d.workload_type == "shared_service"
    assert d.service_name == "ollama"
    assert (d.owner, d.source) == ("taehoon", "service")


def test_shared_service_by_image_without_owner_is_unknown(resolver):
    d = resolver.classify(proc(container=container("stoic_x", image="vllm/vllm-openai:latest")))
    assert (d.workload_type, d.service_name, d.owner, d.source) == (
        "shared_service",
        "vllm",
        None,
        "none",
    )


def test_container_rule_beats_service_owner(resolver):
    d = resolver.classify(proc(container=container("taehoon_vllm", image="vllm/vllm-openai")))
    assert d.workload_type == "shared_service"
    assert (d.owner, d.source) == ("taehoon", "container_rule")


def test_exec_account_name_or_alias(resolver):
    d = resolver.classify(proc(exec_username="minsu", exec_uid=1002))
    assert (d.workload_type, d.owner, d.source) == ("personal", "minsu", "account_map")
    d = resolver.classify(proc(exec_username="TH", exec_uid=5555))
    assert (d.owner, d.source) == ("taehoon", "account_map"), "별칭도 쓰고 대소문자는 무시한다"


def test_root_is_unknown(resolver):
    d = resolver.classify(proc(exec_username="root", exec_uid=0))
    assert (d.workload_type, d.owner, d.source) == ("personal", None, "none")


def test_unregistered_host_user_is_unknown(resolver):
    d = resolver.classify(proc(exec_username="guest", exec_uid=4242))
    assert (d.owner, d.source) == (None, "none")


def test_display_name(resolver):
    assert resolver.display_name("taehoon") == "임태훈"
    assert resolver.display_name("someone") == "someone"
    assert resolver.display_name(None) is None


def test_color_slot_follows_users_order(resolver):
    assert [resolver.color_slot(u) for u in ("taehoon", "minsu", "jihye")] == [0, 1, 2]
    assert resolver.color_slot("someone") is None and resolver.color_slot(None) is None


def test_container_from_docker_proxy_is_not_guessed_by_name(resolver):
    """에이전트 docker-proxy 가 실행 계정을 붙인 컨테이너는 이름 규칙을 쓰지 않는다."""
    stamped = {"runboard.launcher": "th", "runboard.owner": "th"}
    d = resolver.classify(proc(container=container("minsu_dev", stamped)))
    assert (d.owner, d.source) == ("taehoon", "label"), "이름(minsu)이 아니라 실행 계정"
    by_root = {"runboard.launcher": "root"}
    d = resolver.classify(proc(container=container("minsu_dev", by_root)))
    assert (d.owner, d.source) == (None, "none"), "root 가 띄운 컨테이너는 이름으로 짐작하지 않는다"


def test_ignore_commands_match_the_executable_name(registry, resolver):
    """실행 파일명(명령어 첫 토큰의 파일명)만 본다. 기본 목록은 Xorg 하나다."""
    xorg = proc(
        exec_username="gdm",
        exec_uid=128,
        cmdline="/usr/lib/xorg/Xorg vt1 -displayfd 3 -auth /run/user/128/gdm/Xauthority -nolisten tcp",
    )
    assert resolver.ignored(xorg)
    assert not resolver.ignored(proc(cmdline="python Xorg.py")), "인자에 있는 이름은 보지 않는다"
    assert not resolver.ignored(proc(cmdline="/usr/bin/xorg")), "대소문자를 구분한다"
    assert not resolver.ignored(proc(cmdline="")), (
        "명령어를 못 읽은 프로세스는 무엇인지 모르니 둔다"
    )

    other = OwnerResolver(OwnerRules(ignore_commands=["gnome-shell", ""]), registry)
    assert not other.ignored(xorg)
    assert other.ignored(proc(cmdline="/usr/bin/gnome-shell --wayland"))
    none = OwnerResolver(OwnerRules(ignore_commands=[]), registry)
    assert not none.ignored(xorg), "빈 목록이면 모두 센다"
