"""영속화: 엔진 → PostgreSQL 저장 → 새 엔진에서 복원. 등록 정보 저장과 외래 키 동작."""

from sqlalchemy import inspect, text

from runboard_agent.collector.fake import CHURN_ALIVE_S, CHURN_PERIOD_S
from runboard_server.core.engine import Engine
from runboard_server.db.models import GpuRow, GpuSampleRow, NodeRow, SessionRow, WorkloadRow
from runboard_server.db.repo import Repository
from runboard_server.registry import UserFields

T0 = 1_760_600_000.0
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)


def test_ingest_persists_and_restores(config, registry, repo, fake_report):
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    engine.ingest(fake_report(now=BASE + 5))

    assert repo.count(NodeRow) == 1
    with repo.orm() as db:
        row = db.get(NodeRow, "fake01")
    assert (row.hostname, row.display_name, row.seq) == ("fake01", "Server_1", 1), (
        "첫 보고에 바로 등록"
    )
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
    fresh = Engine(config, repo, clock=lambda: BASE + 60, registry=registry)
    fresh.restore()
    node = fresh.state.nodes["fake01"]
    assert node.conn_status == "offline" and len(node.gpus) == 8, "노드는 비활성화로 남는다"
    assert node.display_name == "Server_1" and node.seq == 1, "이름은 재시작 뒤에도 그대로"
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


def test_restore_keys_nodes_by_node_id(config, registry, repo, fake_report):
    """node_id 로 등록된 노드는 재시작 뒤에도 같은 식별자로 복원되어, 다음 보고가 새 노드를 만들지 않는다."""
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    report = fake_report(now=BASE + 5, node="host-a")
    report.node.node_id = "id-a"
    engine.ingest(report)

    fresh = Engine(config, repo, clock=lambda: BASE + 60, registry=registry)
    fresh.restore()
    assert list(fresh.state.nodes) == ["id-a"]
    again = fake_report(now=BASE + 60, node="host-a")
    again.node.node_id = "id-a"
    fresh.ingest(again)
    nodes = fresh.overview(now=BASE + 60).nodes
    assert [(n.key, n.display_name, n.conn_status) for n in nodes] == [
        ("id-a", "Server_1", "online")
    ]


def test_tick_updates_node_status_in_db(config, registry, repo, fake_report):
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    engine.ingest(fake_report(now=BASE + 5))
    engine.tick(now=BASE + 40)
    with repo.orm() as db:
        row = db.get(NodeRow, "fake01")
        assert row.conn_status == "offline"
        statuses = {r.status for r in db.query(SessionRow).all()}
    assert statuses == {"unknown"}


def test_load_nodes_backfills_display_names_once(repo):
    with repo.orm() as db:
        db.add(NodeRow(name="old01", first_seen=1.0, last_received_ts=1.0, last_measured_ts=1.0))
        db.add(NodeRow(name="old02", first_seen=2.0, last_received_ts=2.0, last_measured_ts=2.0))
        db.commit()
    nodes = repo.load_nodes(prefix="Srv_")
    assert [(n.key, n.display_name, n.seq) for n in nodes] == [
        ("old01", "Srv_1", 1),
        ("old02", "Srv_2", 2),
    ]
    assert nodes[0].info.name == "old01" and nodes[0].info.node_id is None, "옛 행은 이름이 식별자"
    again = repo.load_nodes(prefix="Other_")
    assert [n.display_name for n in again] == ["Srv_1", "Srv_2"], "한 번 붙인 이름은 유지"


def test_load_nodes_backfill_skips_names_in_use(repo):
    with repo.orm() as db:
        db.add(NodeRow(name="old01", first_seen=1.0, last_received_ts=1.0, last_measured_ts=1.0))
        db.add(
            NodeRow(
                name="old02",
                first_seen=2.0,
                last_received_ts=2.0,
                last_measured_ts=2.0,
                display_name="Srv_3",
                seq=2,
            )
        )
        db.commit()
    nodes = repo.load_nodes(prefix="Srv_")
    assert [(n.key, n.display_name, n.seq) for n in nodes] == [
        ("old02", "Srv_3", 2),
        ("old01", "Srv_4", 3),
    ], "순번 이름 Srv_3 은 이미 쓰이므로 다음 번호"


def test_hostname_change_is_saved_immediately(config, registry, repo, fake_report):
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    first = fake_report(now=BASE + 5, node="host-a")
    first.node.node_id = "id-a"
    engine.ingest(first)
    engine.clock = lambda: BASE + 6
    renamed = fake_report(now=BASE + 6, node="host-a2")
    renamed.node.node_id = "id-a"
    engine.ingest(renamed)
    with repo.orm() as db:
        row = db.get(NodeRow, "id-a")
    assert (row.hostname, row.display_name) == ("host-a2", "Server_1"), (
        "persist 간격을 기다리지 않는다"
    )
    assert repo.count(GpuSampleRow) == 8, "샘플은 간격대로만 쓴다"


def test_long_container_metadata_is_clipped_to_columns(config, registry, repo, fake_report):
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    report = fake_report(now=BASE + 5)
    labels = {"runboard.owner": "o" * 99, "runboard.project": "p" * 999, "runboard.job": "j" * 999}
    for gpu in report.gpus:
        for proc in gpu.processes:
            if proc.container and proc.container.name == "taehoon_torch":
                proc.container.name = "c" * 999
                proc.container.image = "i" * 999
                proc.container.labels.update(labels)
    engine.ingest(report)
    with repo.orm() as db:
        row = db.query(WorkloadRow).filter(WorkloadRow.container_name.like("ccc%")).one()
    stored = (row.name, row.container_name, row.image, row.project, row.job, row.owner)
    assert [len(v) for v in stored] == [200, 200, 300, 100, 100, 64], (
        "PostgreSQL 은 길이를 넘는 값이 하나라도 있으면 그 트랜잭션 전체를 거부한다"
    )
    shown = next(w for w in engine.tracker.open_workloads() if w.id == row.id)
    assert len(shown.container_name) == 999, "화면(메모리)은 원문 그대로"


def test_registry_round_trip_and_foreign_keys(registry, repo, seed):
    seed(repo, registry)
    loaded = repo.load_registry()
    assert loaded == registry, "순서·별칭·id 까지 그대로"

    assert repo.update_user("minsu", UserFields(display_name="김민수2", aliases=["ms"]))
    assert repo.reorder_users(["jihye", "minsu", "taehoon"])
    assert not repo.reorder_users(["jihye", "minsu"]), "전체 목록이 아니면 바꾸지 않는다"
    loaded = repo.load_registry()
    assert [u.username for u in loaded.users] == ["jihye", "minsu", "taehoon"]
    assert loaded.user("minsu").aliases == ["ms"]

    assert repo.delete_user("taehoon")
    loaded = repo.load_registry()
    assert loaded.services[0].name == "ollama" and loaded.services[0].owner is None, (
        "담당 서비스는 담당자만 비운다"
    )
    assert not repo.delete_user("taehoon")


def test_migrate_drops_account_mappings_table(pg_url, repo, registry, seed):
    """계정 매핑 기능을 없앴다. 예전 DB 에 남은 표는 시작할 때 지운다. 사용자는 그대로다."""
    seed(repo, registry)
    with repo.engine.begin() as conn:
        conn.execute(
            text(
                "CREATE TABLE account_mappings (id SERIAL PRIMARY KEY, node VARCHAR(64), "
                "uid INTEGER NOT NULL, username VARCHAR(64) NOT NULL "
                "REFERENCES users (username) ON DELETE CASCADE)"
            )
        )
        conn.execute(text("INSERT INTO account_mappings (uid, username) VALUES (1001, 'taehoon')"))
    fresh = Repository(pg_url)
    try:
        assert not inspect(fresh.engine).has_table("account_mappings")
        assert fresh.load_registry() == registry
    finally:
        fresh.close()
