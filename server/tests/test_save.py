"""작업·세션 저장 (PostgreSQL): 식별자가 바뀐 노드의 세션 이어받기, DB 가 받지 않는 행 건너뛰기, 실패한 저장 다시 쓰기."""

import logging

import pytest
from sqlalchemy.exc import OperationalError, ProgrammingError
from sqlalchemy.orm import Session as OrmSession

from runboard_agent.collector.fake import CHURN_PERIOD_S
from runboard_server.core.engine import Engine
from runboard_server.db.models import GpuSampleRow, NodeRow, SessionRow, WorkloadRow

T0 = 1_760_600_000.0
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)


@pytest.fixture
def send(fake_report):
    """fake 보고를 그 시각에 받은 것으로 넣는다. node_id 만 주면 식별자만 바뀐 같은 호스트다.

    fake 수집기는 GPU UUID·부팅 ID 를 호스트명으로 만들므로 같은 프로세스를 같은 키로 보고한다.
    mutate 는 넣기 전에 보고를 고치는 함수다.
    """

    def _send(
        engine: Engine,
        now: float,
        node: str = "fake01",
        node_id: str | None = None,
        mutate=None,
        **kw,
    ):
        report = fake_report(now=now, node=node, **kw)
        report.node.node_id = node_id
        if mutate is not None:
            mutate(report)
        engine.ingest(report, received_ts=now)

    return _send


def _rows(repo, model, **where) -> list:
    with repo.orm() as db:
        return db.query(model).filter_by(**where).all()


def _db_down(*args, **kwargs):
    raise OperationalError("INSERT ...", None, Exception("DB 연결 끊김"))


def _skipped(caplog) -> list[str]:
    """Repository._save_each 가 건너뛴 행의 경고."""
    return [r.getMessage() for r in caplog.records if r.name == "runboard_server.db.repo"]


def _no_jupyter(report) -> None:
    report.gpus[2].processes.clear()  # minsu 의 jupyter 가 보고에서 빠진다


def test_identifier_change_takes_over_sessions(config, registry, repo, send, caplog):
    """재부팅 없이 node_id 를 새로 주고 에이전트를 다시 띄운 노드.

    전에는 새 세션이 옛 노드의 열린 세션과 uq_session_open_key 에서 부딪혔고, 저장은 모든 노드의 변경을
    한 트랜잭션에 쓰므로 다른 노드의 샘플 저장과 tick 까지 실패했다.
    """
    caplog.set_level(logging.INFO, logger="runboard_server.core.sessions")
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5, churn=False)
    send(engine, BASE + 5, node="fake02")
    send(engine, BASE + 35, node="fake02")
    engine.tick(now=BASE + 40)  # fake01 만 비활성화

    send(engine, BASE + 41, node_id="id-b", churn=False)
    send(engine, BASE + 42, node_id="id-b", churn=False)
    old = _rows(repo, SessionRow, node="fake01")
    assert len(old) == 7
    assert {(r.status, r.ended_reason, r.end_ts) for r in old} == {
        ("ended", "node_replaced", BASE + 5)
    }
    assert {r.status for r in _rows(repo, WorkloadRow, node="fake01")} == {"ended"}
    new = _rows(repo, SessionRow, node="id-b")
    assert len(new) == 7 and {r.status for r in new} == {"active"}
    assert "노드 fake01 의 세션 7개를 노드 id-b 가 이어받는다" in caplog.text

    # 다른 노드의 샘플 저장(dirty 전부를 쓴다)과 tick 도 막히지 않는다
    send(engine, BASE + 66, node="fake02")  # churn 이 새 PID 로 바뀐다
    engine.tick(now=BASE + 70)
    assert repo.count(GpuSampleRow) == 8 * 5, "fake01 한 번, fake02 세 번, id-b 한 번"
    fake02 = _rows(repo, SessionRow, node="fake02")
    assert [r.ended_reason for r in fake02 if r.status == "ended"] == ["exited"]
    assert len([r for r in fake02 if r.status == "active"]) == 8


def test_identifier_change_waits_while_old_node_still_reports(config, registry, repo, send):
    """옛 노드가 아직 비활성화되지 않았으면 새 노드는 그 프로세스의 세션을 만들지 않는다. 끊긴 뒤 이어받는다.

    그 사이에도 새 노드의 샘플은 같은 GPU 를 쓰이는 것(occupied)으로 적는다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5, churn=False)
    send(engine, BASE + 6, node_id="id-b", churn=False)
    send(engine, BASE + 35, node_id="id-b", churn=False)
    assert engine.tracker.open_sessions("id-b") == []
    assert _rows(repo, SessionRow, node="id-b") == []
    assert {r.status for r in _rows(repo, SessionRow, node="fake01")} == {"active"}

    def occupied(ts: float) -> dict[str, bool]:
        return {r.gpu_uuid: r.occupied for r in _rows(repo, GpuSampleRow, ts=ts)}

    assert occupied(BASE + 6) == occupied(BASE + 5), "옛 노드의 첫 샘플과 같다"
    assert sum(occupied(BASE + 6).values()) == 6, "churn 을 끈 GPU 4 와 GPU 7 만 비었다"

    engine.tick(now=BASE + 40)  # fake01 비활성화. id-b 는 정상
    send(engine, BASE + 41, node_id="id-b", churn=False)
    assert {r.ended_reason for r in _rows(repo, SessionRow, node="fake01")} == {"node_replaced"}
    new = _rows(repo, SessionRow, node="id-b")
    assert len(new) == 7 and {r.status for r in new} == {"active"}


def test_row_the_db_rejects_is_skipped_and_the_rest_is_written(
    config, registry, repo, fake_report, caplog
):
    """DB 가 받지 않는 값(64자를 넘는 exec_username)이 든 세션만 경고를 남기고 건너뛴다. 나머지는 쓴다."""
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)

    def ingest(now: float) -> None:
        report = fake_report(now=now)
        report.gpus[2].processes[0].exec_username = "m" * 65  # minsu 의 jupyter
        engine.ingest(report, received_ts=now)

    ingest(BASE + 5)
    bad = next(s for s in engine.tracker.open_sessions() if s.exec_username == "m" * 65)
    rows = _rows(repo, SessionRow)
    assert len(rows) == 7 and bad.id not in {r.id for r in rows}
    assert repo.count(WorkloadRow) == 6 and repo.count(GpuSampleRow) == 8

    ingest(BASE + 36)  # 다음 샘플 저장: 나머지 세션의 누적값도 쓴다
    rows = _rows(repo, SessionRow)
    assert len(rows) == 7 and {r.last_seen_ts for r in rows} == {BASE + 36}
    skipped = _skipped(caplog)
    assert len(skipped) == 2, "저장마다 다시 시도하고 남긴다"
    assert all(
        m.startswith(f"gpu_sessions 행을 쓰지 못해 건너뛴다 (id {bad.id}): value too long")
        for m in skipped
    )


def test_row_psycopg_cannot_encode_is_skipped_too(config, registry, repo, send, caplog):
    """짝 없는 서로게이트(JSON 의 "\\udcff")는 psycopg 가 값을 보내기 전에 UnicodeEncodeError 를 낸다.

    DB 오류가 아니어도 그 행 탓이므로 건너뛴다. 전에는 연결 끊김처럼 돌려받아 다음 저장마다 다시 실패했고,
    모든 노드의 샘플 저장과 tick 이 멈췄다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)

    def surrogate(report):
        report.gpus[2].processes[0].exec_username = "a\udcffb"  # minsu 의 jupyter

    send(engine, BASE + 5, churn=False, mutate=surrogate)
    send(engine, BASE + 5, node="fake02", churn=False)
    bad = next(s for s in engine.tracker.open_sessions() if s.exec_username == "a\udcffb")
    assert len(_rows(repo, SessionRow, node="fake01")) == 6
    assert len(_rows(repo, SessionRow, node="fake02")) == 7

    send(engine, BASE + 6, churn=False, mutate=_no_jupyter)
    send(engine, BASE + 7, churn=False, mutate=_no_jupyter)  # 끝난다. 그 종료도 건너뛴다
    assert bad.ended_reason == "exited"
    engine.tick(now=BASE + 10)
    send(engine, BASE + 36, node="fake02", churn=False)  # 다른 노드의 샘플 저장
    rows = _rows(repo, SessionRow, node="fake01")
    assert len(rows) == 6 and {r.last_seen_ts for r in rows} == {BASE + 7}, "tick 이 썼다"
    assert repo.count(GpuSampleRow) == 8 * 3
    skipped = _skipped(caplog)
    assert len(skipped) == 2, "만들 때와 끝날 때"
    assert all(
        m.startswith(f"gpu_sessions 행을 쓰지 못해 건너뛴다 (id {bad.id}): 'utf-8' codec")
        for m in skipped
    )
    assert engine.tracker.take_dirty() == ([], []), "건너뛴 행은 돌려받지 않는다"


@pytest.mark.parametrize(
    ("error", "message"),
    [(OperationalError, "DB 연결 끊김"), (ProgrammingError, "permission denied for table")],
    ids=["connection", "privilege"],
)
def test_db_side_failure_is_not_taken_for_a_bad_row(
    config, registry, repo, send, monkeypatch, caplog, error, message
):
    """연결 끊김이나 권한 같은 DB 쪽 실패는 행 탓이 아니다. 건너뛰지 않고 올려서 엔진이 꺼낸 것을 돌려준다.

    행 탓인 실패로 한 행씩 쓰다가 그런 실패가 나도 같다. 그때까지 쓴 행도 남기지 않는다 (한 트랜잭션).
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    merge = OrmSession.merge

    def failing_merge(self, instance, *args, **kwargs):
        if isinstance(instance, SessionRow) and instance.pid == 5100:  # jihye 의 train.py
            raise error("UPDATE ...", None, Exception(message))
        return merge(self, instance, *args, **kwargs)

    def long_name(report):
        report.gpus[2].processes[0].exec_username = "m" * 65  # 이 행 탓에 한 행씩 쓰기로 간다

    monkeypatch.setattr(OrmSession, "merge", failing_merge)
    with pytest.raises(error):
        send(engine, BASE + 5, churn=False, mutate=long_name)
    assert repo.count(SessionRow) == 0 and repo.count(WorkloadRow) == 0
    monkeypatch.undo()

    engine.tick(now=BASE + 10)  # 돌려받은 것을 쓴다. 행 탓인 jupyter 만 또 건너뛴다
    assert repo.count(SessionRow) == 6 and repo.count(WorkloadRow) == 5
    assert len(_skipped(caplog)) == 2 and all("value too long" in m for m in _skipped(caplog))


def test_takeover_with_a_row_the_db_rejects_still_ends_old_sessions_first(
    config, registry, repo, send, caplog
):
    """이어받는 저장에 DB 가 받지 않는 행이 섞여 한 행씩 쓰기로 가도 옛 세션의 종료가 새 세션보다 먼저 나간다.

    순서가 틀리면 새 세션마다 uq_session_open_key 에 걸려 건너뛴다는 경고가 더 남는다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5, churn=False)
    engine.tick(now=BASE + 40)

    def long_name(report):
        report.gpus[5].processes[0].exec_username = "j" * 65  # jihye 의 train.py

    send(engine, BASE + 41, node_id="id-b", churn=False, mutate=long_name)
    skipped = _skipped(caplog)
    assert len(skipped) == 1 and "value too long" in skipped[0]
    old = _rows(repo, SessionRow, node="fake01")
    assert {(r.status, r.ended_reason) for r in old} == {("ended", "node_replaced")}
    assert len(_rows(repo, SessionRow, node="id-b")) == 6


def test_failed_save_is_written_by_next_tick(config, registry, repo, send, monkeypatch):
    """세션을 끝낸 tick 의 저장이 실패해도 그 종료를 잃지 않는다. 다음 tick 이 쓴다."""
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5)
    engine.tick(now=BASE + 40)  # 비활성화: 세션 8개 미확인
    save = repo.save
    failed = []

    def flaky(workloads, sessions):
        if not failed:
            failed.append(len(sessions))
            _db_down()
        save(workloads, sessions)

    monkeypatch.setattr(repo, "save", flaky)
    timeout = BASE + 5 + 86400 + 1
    with pytest.raises(OperationalError):
        engine.tick(now=timeout)
    assert failed == [8] and engine.tracker.open_sessions() == [], "메모리에서는 끝났다"
    assert {r.status for r in _rows(repo, SessionRow)} == {"unknown"}, "DB 에는 아직 열려 있다"

    engine.tick(now=timeout + 10)
    rows = _rows(repo, SessionRow)
    assert len(rows) == 8
    assert {(r.status, r.ended_reason) for r in rows} == {("ended", "node_offline_timeout")}
    assert {r.status for r in _rows(repo, WorkloadRow)} == {"ended"}


def test_failed_takeover_save_is_written_by_next_tick(config, registry, repo, send, monkeypatch):
    """이어받는 보고의 저장이 실패해도 옛 세션의 종료(node_replaced)와 새 세션을 잃지 않는다. 다음 tick 이 쓴다."""
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5, churn=False)
    engine.tick(now=BASE + 40)
    monkeypatch.setattr(repo, "save", _db_down)
    with pytest.raises(OperationalError):
        send(engine, BASE + 41, node_id="id-b", churn=False)
    monkeypatch.undo()
    assert {r.status for r in _rows(repo, SessionRow, node="fake01")} == {"unknown"}
    assert _rows(repo, SessionRow, node="id-b") == []

    engine.tick(now=BASE + 45)
    old = _rows(repo, SessionRow, node="fake01")
    assert {(r.status, r.ended_reason) for r in old} == {("ended", "node_replaced")}
    new = _rows(repo, SessionRow, node="id-b")
    assert len(new) == 7 and {r.status for r in new} == {"active"}


def test_rows_taken_back_are_written_with_the_next_transition(
    config, registry, repo, send, monkeypatch, caplog
):
    """돌려받은 것은 tick 을 기다리지 않고 다음 전이 저장에도 함께 쓴다.

    첫 보고의 저장이 실패해 컨테이너 작업이 돌려받은 것에만 있을 때, DB 가 돌아온 뒤 tick 전에 그 작업에
    짧은 프로세스가 붙었다 끝난다. 그 세션만 따로 쓰면 외래 키에 걸려 건너뛰어져 사라졌다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    save = repo.save
    calls = []

    def fails_once(workloads, sessions):  # 빈 저장은 DB 에 닿지 않는다 (Repository.save 와 같다)
        if workloads or sessions:
            calls.append(len(sessions))
            if len(calls) == 1:
                _db_down()
        save(workloads, sessions)

    def helper(report):  # DDP 컨테이너 안의 보조 프로세스 (GPU 0)
        ddp = report.gpus[0].processes[0]
        report.gpus[0].processes.append(
            ddp.model_copy(update={"pid": 4100, "cmdline": "python helper.py"})
        )

    monkeypatch.setattr(repo, "save", fails_once)
    with pytest.raises(OperationalError):
        send(engine, BASE + 5, churn=False)
    send(engine, BASE + 6, churn=False, mutate=helper)  # 새 세션: 전이. 돌려받은 것과 함께 쓴다
    helper_session = next(s for s in engine.tracker.open_sessions() if s.pid == 4100)
    send(engine, BASE + 7, churn=False)
    send(engine, BASE + 8, churn=False)  # 보조 프로세스가 끝난다
    assert calls == [7, 8, 1], "첫 저장 실패, 돌려받은 7개와 새 세션, 종료"
    row = _rows(repo, SessionRow, id=helper_session.id)
    assert [(r.status, r.ended_reason) for r in row] == [("ended", "exited")]
    assert len(_rows(repo, SessionRow)) == 8 and _skipped(caplog) == []


def test_reappeared_process_after_a_failed_end_is_written(
    config, registry, repo, send, monkeypatch, caplog
):
    """세션의 종료를 쓰지 못한 사이 같은 프로세스가 다시 보이면 돌려받은 종료와 새 세션을 같은 저장에 쓴다.

    새 세션만 따로 쓰면 DB 에 아직 열려 있는 옛 행과 uq_session_open_key 에서 부딪혀 건너뛰어졌다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5, churn=False)
    old = next(s for s in engine.tracker.open_sessions() if s.exec_username == "minsu")
    send(engine, BASE + 6, churn=False, mutate=_no_jupyter)
    monkeypatch.setattr(repo, "save", _db_down)
    with pytest.raises(OperationalError):
        send(engine, BASE + 7, churn=False, mutate=_no_jupyter)  # 끝난다
    monkeypatch.undo()

    send(engine, BASE + 8, churn=False)  # 다시 보인다: 같은 키의 새 세션
    rows = _rows(repo, SessionRow, pid=old.pid)
    assert sorted((r.status, r.ended_reason) for r in rows) == [
        ("active", None),
        ("ended", "exited"),
    ]
    assert _skipped(caplog) == []


def test_failed_node_delete_can_be_retried(config, registry, repo, send, monkeypatch):
    """노드 삭제의 저장이 실패하면 오류(관리 API 는 500)이고 노드는 남는다.

    세션은 메모리에서 이미 끝났으므로 그 종료(node_removed)는 다음 tick 이 쓴다. 다시 지우면 지워진다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5, churn=False)
    engine.tick(now=BASE + 40)
    monkeypatch.setattr(repo, "save", _db_down)
    with pytest.raises(OperationalError):
        engine.delete_node("fake01", now=BASE + 41)
    monkeypatch.undo()
    assert "fake01" in engine.state.nodes and repo.count(NodeRow) == 1
    assert {r.status for r in _rows(repo, SessionRow)} == {"unknown"}, "DB 에는 아직 열려 있다"

    engine.tick(now=BASE + 45)
    rows = _rows(repo, SessionRow)
    assert {(r.status, r.ended_reason) for r in rows} == {("ended", "node_removed")}
    assert repo.count(NodeRow) == 1, "노드 행은 남는다"
    assert engine.delete_node("fake01", now=BASE + 46)
    assert repo.count(NodeRow) == 0 and "fake01" not in engine.state.nodes


def test_reports_without_transitions_skip_the_db_while_it_is_down(
    config, registry, repo, send, monkeypatch
):
    """DB 가 멈춘 동안 전이도 샘플 저장도 없는 보고는 DB 에 닿지 않는다 (204). 전이가 있는 보고는 실패(500)한다.

    샘플 저장 때(persist_interval_s 30초)가 되면 그 노드의 보고는 저장에 성공할 때까지 보고마다 DB 에 쓰려다
    실패한다. 그 사이 꺼낸 세션 종료는 DB 가 돌아온 뒤 처음 DB 에 쓰는 보고가 쓴다.
    """
    engine = Engine(config, repo, clock=lambda: BASE + 5, registry=registry)
    send(engine, BASE + 5)
    send(engine, BASE + 35)  # 샘플 저장. 다음 저장은 30초 뒤
    churn = next(s for s in engine.tracker.open_sessions() if s.cmdline.startswith("python eval"))

    def save(workloads, sessions):  # 빈 저장은 DB 에 닿지 않는다 (Repository.save 와 같다)
        if workloads or sessions:
            _db_down()

    monkeypatch.setattr(repo, "save", save)
    monkeypatch.setattr(repo, "upsert_node", _db_down)
    monkeypatch.setattr(repo, "insert_samples", _db_down)
    send(engine, BASE + 41)  # churn 이 한 번 빠진다. 전이가 아니다
    with pytest.raises(OperationalError):
        send(engine, BASE + 42)  # churn 종료
    send(engine, BASE + 43)
    send(engine, BASE + 64)  # 돌려받은 것이 있어도 전이가 없으면 DB 에 닿지 않는다
    assert churn.ended_reason == "exited" and churn.key not in engine.tracker.sessions
    assert _rows(repo, SessionRow, id=churn.id)[0].status == "active", "DB 에는 아직 열려 있다"
    for now in (BASE + 65, BASE + 66):  # 샘플 저장 때가 됐다
        with pytest.raises(OperationalError):
            send(engine, now)

    monkeypatch.undo()
    send(engine, BASE + 67)
    row = _rows(repo, SessionRow, id=churn.id)[0]
    assert (row.status, row.ended_reason, row.end_ts) == ("ended", "exited", BASE + 35)
    assert _rows(repo, WorkloadRow, id=churn.workload_id)[0].status == "ended"
    assert repo.count(GpuSampleRow) == 8 * 3, "BASE+5, +35, +67"
