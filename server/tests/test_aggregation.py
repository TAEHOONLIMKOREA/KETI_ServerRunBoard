"""이력 집계와 보존 정리 (PostgreSQL): 원본 샘플 → gpu_samples_5m, 시작 때 밀린 집계, 기한 지난 행 삭제."""

import pytest
from sqlalchemy import inspect, text

from runboard_agent.collector.fake import fake_gpu_uuid
from runboard_server.core.engine import AGG_CHUNK_S, Engine
from runboard_server.core.history import BUCKET_S, DAY_S, bucket_start
from runboard_server.db.models import (
    GpuRow,
    GpuSample5mRow,
    GpuSampleRow,
    NodeRow,
    SessionRow,
    WorkloadRow,
)
from runboard_server.db.repo import Repository

H0 = 1_760_598_000  # 정각. 5분 경계이고 fake churn 주기(90초)의 시작이기도 하다
GPU = "GPU-test-0"


def add_samples(repo: Repository, *samples: tuple[float, dict], uuid: str = GPU) -> None:
    with repo.orm() as db:
        db.add_all(
            GpuSampleRow(gpu_uuid=uuid, ts=ts, received_ts=ts, **values) for ts, values in samples
        )
        db.commit()


def buckets(repo: Repository) -> dict[tuple[str, int], GpuSample5mRow]:
    with repo.orm() as db:
        return {(r.gpu_uuid, r.bucket_ts): r for r in db.query(GpuSample5mRow).all()}


def test_samples_record_occupancy_and_tick_aggregates_them(config, registry, repo, fake_report):
    engine = Engine(config, repo, clock=lambda: H0 + 5, registry=registry)
    engine.restore()
    for k in range(20):  # 30초마다 10분: 버킷 둘에 GPU 마다 샘플 10개씩
        now = H0 + 5 + 30 * k
        engine.clock = lambda now=now: now
        engine.ingest(fake_report(now=now))
    with repo.orm() as db:
        occupied = {r.gpu_uuid: r.occupied for r in db.query(GpuSampleRow).all()}
    gpu = {i: fake_gpu_uuid("fake01", i) for i in range(8)}
    assert occupied[gpu[0]] is True and occupied[gpu[3]] is True, "공용 서비스도 작업이다"
    assert occupied[gpu[7]] is False, "빈 GPU"

    engine.tick(now=H0 + 600 + 60)
    rows = buckets(repo)
    assert len(rows) == 16, "GPU 8장 × 닫힌 버킷 2개"
    jupyter = rows[(gpu[2], H0)]
    assert (jupyter.samples, jupyter.util_avg, jupyter.util_max) == (10, 0.0, 0)
    assert (jupyter.mem_avg_mb, jupyter.mem_max_mb, jupyter.temp_max) == (30200.0, 30200, 38)
    assert (jupyter.power_avg, jupyter.occupied_ratio) == (60.0, 1.0)
    assert rows[(gpu[7], H0 + 300)].occupied_ratio == 0.0
    ddp = rows[(gpu[0], H0)]
    assert 0 < ddp.util_avg <= ddp.util_max <= 100


def test_bucket_closes_after_grace_and_upsert_is_idempotent(config, registry, repo):
    engine = Engine(config, repo, clock=lambda: H0, registry=registry)
    engine.restore()
    add_samples(repo, (H0 + 10, {"util_gpu": 10}), (H0 + 200, {"util_gpu": 30}))

    engine.tick(now=H0 + 300 + 59)
    assert buckets(repo) == {}, "끝나고 clock_skew_max_s(60초)가 지나야 닫힌다"
    add_samples(repo, (H0 + 299, {"util_gpu": 50}))  # 시계가 59초 늦은 에이전트의 샘플이 늦게 왔다
    engine.tick(now=H0 + 300 + 60)
    row = buckets(repo)[(GPU, H0)]
    assert (row.samples, row.util_avg, row.util_max) == (3, 30.0, 50), "늦게 온 샘플도 제 버킷에"

    assert repo.aggregate_samples(H0, H0 + 300) == 1
    again = buckets(repo)
    assert len(again) == 1 and again[(GPU, H0)].samples == 3, "다시 해도 같다"
    add_samples(repo, (H0 + 100, {"util_gpu": 90}))
    repo.aggregate_samples(H0, H0 + 300)
    row = buckets(repo)[(GPU, H0)]
    assert (row.samples, row.util_max) == (4, 90), "있는 행은 덮어쓴다"
    add_samples(repo, (H0 + 150, {"util_gpu": 5}))
    engine.tick(now=H0 + 900)
    assert buckets(repo)[(GPU, H0)].samples == 4, "tick 은 이미 집계한 버킷을 다시 읽지 않는다"


def test_restore_backfills_from_where_aggregation_stopped(config, registry, repo, monkeypatch):
    # 30시간 동안 1시간마다 샘플 하나, 그리고 아직 열린 버킷에 하나
    now = H0 + 30 * 3600 + 120
    add_samples(repo, *[(H0 + 3600 * h + 7, {"util_gpu": h}) for h in range(30)])
    add_samples(repo, (now - 30, {"util_gpu": 99}))
    assert repo.aggregation_resume() == H0, "집계가 없으면 가장 오래된 샘플의 버킷부터"

    calls: list[tuple[int, int]] = []
    original = Repository.aggregate_samples

    def spy(self, start, end):
        calls.append((start, end))
        return original(self, start, end)

    monkeypatch.setattr(Repository, "aggregate_samples", spy)
    Engine(config, repo, clock=lambda: now, registry=registry).restore()
    until = H0 + 30 * 3600  # now - 60초 의 5분 경계
    assert calls[0][0] == H0 and calls[-1][1] == until
    assert len(calls) == 5 and all(b - a <= AGG_CHUNK_S for a, b in calls), "6시간씩 나눠 읽는다"
    assert all(calls[i][1] == calls[i + 1][0] for i in range(len(calls) - 1)), "빈틈 없이 이어진다"
    rows = buckets(repo)
    assert sorted(b for _, b in rows) == [H0 + 3600 * h for h in range(30)]
    assert rows[(GPU, H0 + 3600 * 29)].util_avg == 29.0
    assert repo.aggregation_resume() == H0 + 3600 * 29 + BUCKET_S, "마지막 집계 버킷의 끝부터"

    calls.clear()
    later = now + 600
    Engine(config, repo, clock=lambda: later, registry=registry).restore()
    assert calls == [(H0 + 3600 * 29 + BUCKET_S, until + 600)], "재시작하면 멈춘 곳부터 잇는다"
    assert rows.keys() < buckets(repo).keys(), "열려 있던 버킷이 이제 닫혀 집계된다"


def test_backfill_skips_samples_older_than_aggregate_retention(config, registry, repo):
    now = H0 + 100 * DAY_S
    add_samples(repo, (H0, {"util_gpu": 1}), (now - 2 * DAY_S, {"util_gpu": 2}))
    Engine(config, repo, clock=lambda: now, registry=registry).restore()
    assert [b for _, b in buckets(repo)] == [(now - 2 * DAY_S) // BUCKET_S * BUCKET_S], (
        "agg_days(90일)보다 오래된 샘플은 집계해도 바로 지워지므로 건너뛴다"
    )


def test_startup_maintenance_failure_does_not_block_start(
    config, registry, repo, monkeypatch, caplog
):
    def broken(self):
        raise RuntimeError("DB 가 잠깐 끊겼다")

    monkeypatch.setattr(Repository, "aggregation_resume", broken)
    engine = Engine(config, repo, clock=lambda: H0, registry=registry)
    engine.restore()
    assert "tick 에서 다시 한다" in caplog.text, "시작은 막지 않고 남긴다"
    monkeypatch.undo()
    add_samples(repo, (H0 + 10, {"util_gpu": 1}))
    engine.tick(now=H0 + 300 + 60)
    assert (GPU, H0) in buckets(repo), "다음 tick 이 이어서 한다"


def _workload(id_: str, status: str, end_ts: float | None) -> WorkloadRow:
    return WorkloadRow(
        id=id_,
        key=f"k-{id_}",
        node="n1",
        type="personal",
        name=id_,
        status=status,
        first_seen=0.0,
        last_seen=end_ts or 0.0,
        end_ts=end_ts,
    )


def _session(id_: str, workload_id: str, status: str, end_ts: float | None) -> SessionRow:
    return SessionRow(
        id=id_,
        workload_id=workload_id,
        node="n1",
        gpu_uuid=GPU,
        boot_id="b",
        pid=len(id_),
        status=status,
        start_ts=0.0,
        last_seen_ts=end_ts or 0.0,
        end_ts=end_ts,
    )


def test_purge_deletes_rows_past_retention(repo):
    now = H0 + 400 * DAY_S
    old, recent = now - 400 * DAY_S + 1, now - 10 * DAY_S
    add_samples(repo, (now - 8 * DAY_S, {"util_gpu": 1}), (now - 3600, {"util_gpu": 2}))
    with repo.orm() as db:
        db.add(NodeRow(name="n1", first_seen=old, last_received_ts=old, last_measured_ts=old))
        db.flush()  # relationship 이 없어 flush 가 외래 키 순서를 모른다
        db.add(
            GpuRow(uuid=GPU, node_name="n1", index=0, name="g", memory_total_mb=1, first_seen=old)
        )
        db.add_all(
            GpuSample5mRow(gpu_uuid=GPU, bucket_ts=b, samples=1)
            for b in (H0 + 309 * DAY_S, H0 + 311 * DAY_S)  # 91일 전, 89일 전
        )
        db.add_all(
            [
                _workload("gone", "ended", old),
                _workload("kept_by_session", "ended", old),
                _workload("recent", "ended", recent),
                _workload("active", "active", None),
            ]
        )
        db.flush()
        db.add_all(
            [
                _session("s-gone", "gone", "ended", old),
                _session("s-recent-of-old", "kept_by_session", "ended", recent),
                _session("s-recent", "recent", "ended", recent),
                _session("s-open-old", "active", "unknown", None),
            ]
        )
        db.commit()

    deleted = repo.purge(
        raw_before=now - 7 * DAY_S, agg_before=now - 90 * DAY_S, sessions_before=now - 365 * DAY_S
    )
    assert deleted == {"gpu_samples": 1, "gpu_samples_5m": 1, "gpu_sessions": 1, "workloads": 1}
    with repo.orm() as db:
        assert [r.ts for r in db.query(GpuSampleRow).all()] == [now - 3600], "8일 전 샘플만 지운다"
        assert [r.bucket_ts for r in db.query(GpuSample5mRow).all()] == [H0 + 311 * DAY_S]
        assert {r.id for r in db.query(SessionRow).all()} == {
            "s-recent-of-old",
            "s-recent",
            "s-open-old",
        }, "끝난 세션 중 기한 전에 끝난 것만"
        assert {r.id for r in db.query(WorkloadRow).all()} == {
            "kept_by_session",
            "recent",
            "active",
        }, "남은 세션이 있는 작업은 끝났어도 남긴다"
        assert db.get(NodeRow, "n1") is not None and db.get(GpuRow, GPU) is not None, (
            "노드·GPU 행은 지우지 않는다"
        )


def test_purge_runs_at_start_and_then_hourly(config, registry, repo):
    now = H0 + 30 * DAY_S
    add_samples(repo, (now - 8 * DAY_S, {"util_gpu": 40}))
    engine = Engine(config, repo, clock=lambda: now, registry=registry)
    engine.restore()
    assert repo.count(GpuSampleRow) == 0, "시작할 때 지운다"
    row = buckets(repo)[(GPU, bucket_start(now - 8 * DAY_S))]
    assert (row.samples, row.util_avg) == (1, 40.0), (
        "집계가 먼저다. 집계하지 않은 원본은 지우기 전에 5분 행으로 옮긴다"
    )

    add_samples(repo, (now - 8 * DAY_S, {}))
    engine.tick(now=now + 3599)
    assert repo.count(GpuSampleRow) == 1, "1시간 안에는 다시 지우지 않는다"
    engine.tick(now=now + 3600)
    assert repo.count(GpuSampleRow) == 0


def test_retention_follows_config(config, registry, repo):
    now = H0 + 30 * DAY_S
    config.server.retention.raw_days = 1
    add_samples(repo, (now - 2 * DAY_S, {}), (now - 3600, {}))
    Engine(config, repo, clock=lambda: now, registry=registry).restore()
    with repo.orm() as db:
        assert [r.ts for r in db.query(GpuSampleRow).all()] == [now - 3600]


@pytest.mark.parametrize("drop_table", [False, True])
def test_migrate_adds_occupied_column_and_indexes(pg_url, repo, drop_table):
    with repo.engine.begin() as conn:
        conn.execute(text("ALTER TABLE gpu_samples DROP COLUMN occupied"))
        conn.execute(text("DROP INDEX ix_gpu_samples_ts"))
        if drop_table:
            conn.execute(text("DROP TABLE gpu_samples_5m"))
    fresh = Repository(pg_url)
    try:
        insp = inspect(fresh.engine)
        assert "occupied" in {c["name"] for c in insp.get_columns("gpu_samples")}
        assert "ix_gpu_samples_ts" in {i["name"] for i in insp.get_indexes("gpu_samples")}
        assert "ix_gpu_samples_5m_bucket" in {i["name"] for i in insp.get_indexes("gpu_samples_5m")}
    finally:
        fresh.close()
