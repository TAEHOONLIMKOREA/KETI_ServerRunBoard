from runboard_agent.collector.fake import (
    CHURN_ALIVE_S,
    CHURN_PERIOD_S,
    FakeCollector,
    fake_ip,
    node_offset_s,
    stable_boot_id,
)
from runboard_common.schemas import NodeReport


def _collector(now: float, **kw) -> FakeCollector:
    return FakeCollector(node_name="fake01", clock=lambda: now, **kw)


def test_report_is_valid_and_has_all_three_owner_cases():
    report = _collector(now=1_760_600_000.0).collect()
    NodeReport.model_validate(report.model_dump())
    assert report.node.name == "fake01"
    assert len(report.gpus) == 8

    container_procs = [p for g in report.gpus for p in g.processes if p.container]
    assert any(p.container.labels.get("runboard.owner") == "taehoon" for p in container_procs)

    ollama = report.gpus[3].processes[0]
    assert ollama.exec_username == "ollama"
    assert ollama.util_sm is None, "미지원은 None"
    assert any(e.scope == "gpu:3" and e.code == "NOT_SUPPORTED" for e in report.errors)

    personal = [p for g in report.gpus for p in g.processes if p.container is None]
    assert {p.exec_username for p in personal} >= {"minsu", "jihye"}

    assert report.gpus[7].processes == []


def test_ddp_processes_share_container_and_pgid():
    report = _collector(now=1_760_600_000.0).collect()
    p0, p1 = report.gpus[0].processes[0], report.gpus[1].processes[0]
    assert p0.container.id == p1.container.id
    assert p0.pgid == p1.pgid
    assert p0.pid != p1.pid


def test_jihye_worker_shares_pgid_on_same_gpu():
    procs = _collector(now=1_760_600_000.0).collect().gpus[5].processes
    assert len(procs) == 2
    assert {p.pgid for p in procs} == {5100}


def test_churn_process_appears_and_disappears():
    cycle_start = float(CHURN_PERIOD_S * 1000)
    alive = _collector(now=cycle_start + 5).collect().gpus[4].processes
    gone = _collector(now=cycle_start + CHURN_ALIVE_S + 5).collect().gpus[4].processes
    next_cycle = _collector(now=cycle_start + CHURN_PERIOD_S + 5).collect().gpus[4].processes
    assert len(alive) == 1 and gone == [] and len(next_cycle) == 1
    assert alive[0].pid != next_cycle[0].pid, "주기마다 새 프로세스"
    assert _collector(now=cycle_start + 5, churn=False).collect().gpus[4].processes == []


def test_boot_id_is_stable_across_restarts_but_overridable():
    assert stable_boot_id("gpu01") == stable_boot_id("gpu01")
    assert stable_boot_id("gpu01") != stable_boot_id("gpu02")
    assert _collector(now=0.0, boot_id="custom").collect().node.boot_id == "custom"


def test_gpu_count_truncates():
    report = _collector(now=0.0, gpu_count=3).collect()
    assert [g.index for g in report.gpus] == [0, 1, 2]
    assert report.errors == [], "gpu:3 이 없으면 그 오류도 없다"


def test_multiple_nodes_do_not_share_identifiers():
    a = FakeCollector(node_name="fake01", clock=lambda: 0.0).collect()
    b = FakeCollector(node_name="fake02", clock=lambda: 0.0).collect()
    again = FakeCollector(node_name="fake01", clock=lambda: 0.0).collect()
    assert {g.uuid for g in a.gpus}.isdisjoint({g.uuid for g in b.gpus}), (
        "서버 DB 는 GPU UUID 가 기본 키"
    )
    assert len({g.uuid for g in a.gpus}) == 8
    assert [g.uuid for g in a.gpus] == [g.uuid for g in again.gpus], "재시작해도 같은 UUID"
    assert a.node.boot_id != b.node.boot_id
    assert (a.node.ip, b.node.ip) == ("10.0.0.11", "10.0.0.12")
    assert fake_ip("no-digits") != fake_ip("other-name")


def test_node_offset_staggers_churn_between_nodes():
    assert (node_offset_s("fake01"), node_offset_s("fake02"), node_offset_s("fake03")) == (
        0,
        30,
        60,
    )
    t = float(CHURN_PERIOD_S * 1000) + 5  # fake01 기준 주기 시작 5초 뒤: 살아 있음
    alive = FakeCollector(node_name="fake01", clock=lambda: t).collect().gpus[4].processes
    staggered = FakeCollector(node_name="fake03", clock=lambda: t).collect().gpus[4].processes
    assert len(alive) == 1 and staggered == [], "fake03 은 60초 어긋나 같은 시각에 죽어 있는 구간"
    later = FakeCollector(node_name="fake03", clock=lambda: t + 30).collect().gpus[4].processes
    assert len(later) == 1 and later[0].proc_start_ts <= t + 30, "시작 시각은 미래가 아니다"
