"""영속화: 엔진 → SQLite 저장 → 새 엔진에서 복원."""

from runboard_agent.collector.fake import CHURN_ALIVE_S, CHURN_PERIOD_S
from runboard_server.core.engine import Engine
from runboard_server.db.models import GpuRow, GpuSampleRow, NodeRow, SessionRow, WorkloadRow
from runboard_server.db.repo import Repository

T0 = 1_760_600_000.0
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)


def test_ingest_persists_and_restores(config, fake_report):
    repo = Repository("sqlite://")
    engine = Engine(config, repo, clock=lambda: BASE + 5)
    engine.ingest(fake_report(now=BASE + 5))

    assert repo.count(NodeRow) == 1
    assert repo.count(GpuRow) == 8
    assert repo.count(GpuSampleRow) == 8, "첫 보고는 바로 샘플을 쓴다"
    assert repo.count(WorkloadRow) == 6
    assert repo.count(SessionRow) == 8

    engine.clock = lambda: BASE + 10
    engine.ingest(fake_report(now=BASE + 10))
    assert repo.count(GpuSampleRow) == 8, "persist_interval(30초) 전에는 샘플을 더 쓰지 않는다"

    engine.clock = lambda: BASE + 36
    engine.ingest(fake_report(now=BASE + 36))
    assert repo.count(GpuSampleRow) == 16

    # churn 종료 → ended 로 저장되고 메모리에서는 빠진다
    engine.clock = lambda: BASE + CHURN_ALIVE_S + 5
    engine.ingest(fake_report(now=BASE + CHURN_ALIVE_S + 5))
    engine.clock = lambda: BASE + CHURN_ALIVE_S + 10
    engine.ingest(fake_report(now=BASE + CHURN_ALIVE_S + 10))
    with repo.orm() as db:
        ended = db.query(SessionRow).filter(SessionRow.status == "ended").all()
    assert len(ended) == 1 and ended[0].ended_reason == "exited"
    assert len(engine.tracker.open_sessions()) == 7

    # 재시작: 열린 것만 복원, 전부 unknown 으로 시작
    fresh = Engine(config, repo, clock=lambda: BASE + 60)
    fresh.restore()
    node = fresh.state.nodes["fake01"]
    assert node.conn_status == "offline" and len(node.gpus) == 8, "노드는 비활성화로 남는다"
    ddp = next(g.report for g in node.gpus.values() if g.report.index == 0)
    assert ddp.mem_used_mb == 65_000 and ddp.mem_total_mb == 81920, "마지막 샘플 값으로 채운다"
    summary = fresh.overview(now=BASE + 100).summary
    assert summary.nodes == 1 and summary.nodes_offline == 1
    restored = fresh.tracker.open_sessions()
    assert len(restored) == 7 and {s.status for s in restored} == {"unknown"}
    assert len(fresh.tracker.open_workloads()) == 5

    fresh.ingest(fake_report(now=BASE + 60))
    assert {s.status for s in fresh.tracker.open_sessions()} == {"active"}
    assert {s.id for s in fresh.tracker.open_sessions()} == {s.id for s in restored}
    repo.close()


def test_tick_updates_node_status_in_db(config, fake_report):
    repo = Repository("sqlite://")
    engine = Engine(config, repo, clock=lambda: BASE + 5)
    engine.ingest(fake_report(now=BASE + 5))
    engine.tick(now=BASE + 40)
    with repo.orm() as db:
        row = db.get(NodeRow, "fake01")
        assert row.conn_status == "offline"
        statuses = {r.status for r in db.query(SessionRow).all()}
    assert statuses == {"unknown"}
    repo.close()
