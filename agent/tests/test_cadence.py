from runboard_agent.cadence import ReportCadence, is_active
from runboard_agent.collector.fake import FakeCollector
from runboard_common.schemas import GpuReport, NodeInfo, NodeReport, ProcessReport


def _report(*gpus: GpuReport) -> NodeReport:
    node = NodeInfo(name="n", boot_id="b", agent_version="t", measured_ts=0.0)
    return NodeReport(node=node, gpus=list(gpus))


def _gpu(index: int, util: int | None = None, procs: int = 0) -> GpuReport:
    gpu = GpuReport(index=index, uuid=f"u{index}", name="g", mem_total_mb=100, util_gpu=util)
    gpu.processes = [ProcessReport(pid=100 + i) for i in range(procs)]
    return gpu


def test_is_active():
    assert not is_active(_report())
    assert not is_active(_report(_gpu(0, util=0), _gpu(1, util=None)))
    assert is_active(_report(_gpu(0, util=0), _gpu(1, util=0, procs=1)))
    assert is_active(_report(_gpu(0, util=3))), "프로세스 목록이 없어도 사용률이 있으면 동작 중"
    assert is_active(FakeCollector("fake01", clock=lambda: 0.0).collect())
    assert not is_active(FakeCollector("fake01", gpu_count=0, clock=lambda: 0.0).collect())


def test_active_reports_every_active_interval():
    c = ReportCadence(1.0, 30.0, 5.0)
    assert c.due(0.0), "첫 보고는 바로"
    c.observe(True, 0.0)
    c.mark_sent(0.0)
    assert not c.due(0.5)
    assert c.due(0.95), "루프 오차는 봐준다"
    assert c.declared_interval(True) == 1.0


def test_idle_reports_every_idle_interval_but_wakes_immediately():
    c = ReportCadence(1.0, 30.0, 5.0)
    c.observe(False, 0.0)
    c.mark_sent(0.0)
    assert c.interval(1.0) == 30.0
    assert not c.due(10.0) and c.due(29.95)
    assert c.declared_interval(False) == 30.0
    c.observe(True, 10.0)
    assert c.fast(10.0) and c.due(10.0), "작업이 시작되면 다음 수집 때 바로 보낸다"


def test_grace_keeps_fast_cadence_after_activity_stops():
    c = ReportCadence(1.0, 30.0, 5.0)
    c.observe(True, 100.0)
    c.mark_sent(100.0)
    for t in (101.0, 102.0, 103.0, 104.0, 105.0):
        c.observe(False, t)
        assert c.fast(t) and c.due(t), "유예 중에는 1초마다 보낸다"
        c.mark_sent(t)
    c.observe(False, 106.0)
    assert not c.fast(106.0) and not c.due(106.0)
    assert c.due(135.0), "마지막 전송(105) 에서 30초 뒤"
    assert c.declared_interval(False) == 30.0, "유예 중이라도 유휴 보고에는 느린 주기를 적는다"
