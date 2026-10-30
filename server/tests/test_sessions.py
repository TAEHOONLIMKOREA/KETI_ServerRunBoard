"""세션 상태 전이 (PLAN 5.2) 를 fake 수집기 시나리오로 검증한다."""

import pytest

from runboard_agent.collector.fake import CHURN_ALIVE_S, CHURN_PERIOD_S, fake_gpu_uuid
from runboard_server.core.owner import OwnerResolver
from runboard_server.core.sessions import SessionTracker

T0 = 1_760_600_000.0
# churn 프로세스가 살아 있는 구간의 시작점에 맞춘 기준 시각
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)


@pytest.fixture
def tracker(config, fake_report):
    resolver = OwnerResolver(config)
    t = SessionTracker(miss_reports=2, offline_close_after_s=86400)

    def apply(now: float, **kw):
        report = fake_report(now=now, **kw)
        t.apply_report(report, now, lambda p: resolver.classify(p, report.node.name))
        return report

    t.apply = apply  # type: ignore[attr-defined]
    return t


def _wl_by_name(tracker, name):
    return next(w for w in tracker.open_workloads() if w.name == name)


def test_first_report_creates_workloads_and_sessions(tracker):
    tracker.apply(BASE + 5)
    sessions = tracker.open_sessions("fake01")
    workloads = tracker.open_workloads("fake01")
    assert len(sessions) == 8
    assert len(workloads) == 6, "DDP 2세션과 jihye 2세션이 각각 작업 1개로 묶인다"

    ddp = _wl_by_name(tracker, "seg-train")
    assert ddp.type == "container" and ddp.owner == "taehoon" and ddp.owner_source == "label"
    assert ddp.project == "pbf-defect"
    assert len(tracker.sessions_of_workload(ddp.id)) == 2
    assert {s.gpu_uuid for s in tracker.sessions_of_workload(ddp.id)} == {
        fake_gpu_uuid("fake01", 0),
        fake_gpu_uuid("fake01", 1),
    }

    jihye = _wl_by_name(tracker, "train.py")
    assert jihye.type == "personal" and jihye.owner == "jihye"
    assert len(tracker.sessions_of_workload(jihye.id)) == 2

    ollama = _wl_by_name(tracker, "ollama")
    assert ollama.type == "shared_service" and ollama.owner == "taehoon"

    unknown = _wl_by_name(tracker, "stoic_lovelace")
    assert unknown.type == "container" and unknown.owner is None and unknown.owner_source == "none"

    minsu = _wl_by_name(tracker, "ipykernel_launcher")
    assert minsu.owner == "minsu"


def test_counters_accumulate_between_reports(tracker):
    tracker.apply(BASE + 5)
    tracker.apply(BASE + 10)
    ollama = next(s for s in tracker.open_sessions() if s.exec_username == "ollama")
    assert ollama.util_samples == 0 and ollama.util_avg is None, "수집 불가는 평균에 넣지 않는다"
    expected = (36_500 / 1024) * 5 / 3600
    assert ollama.mem_gb_hours == pytest.approx(expected)
    assert ollama.mem_max_mb == 36_500
    minsu = next(s for s in tracker.open_sessions() if s.exec_username == "minsu")
    assert minsu.util_samples == 2 and minsu.util_avg == 0


def test_session_ends_after_missing_twice(tracker):
    tracker.apply(BASE + 5)
    churn = next(s for s in tracker.open_sessions() if s.cmdline.startswith("python eval.py"))
    gone = BASE + CHURN_ALIVE_S + 5
    tracker.apply(gone)
    assert churn.status == "active" and churn.miss_count == 1
    tracker.apply(gone + 5)
    assert churn.status == "ended" and churn.ended_reason == "exited"
    assert churn.end_ts == BASE + 5, "종료 시각은 마지막으로 관측된 시각"
    assert churn.key not in {s.key for s in tracker.open_sessions()}
    churn_wl = tracker.workloads[churn.workload_id]
    assert churn_wl.status == "ended" and churn_wl.end_ts == BASE + 5


def test_offline_then_return_resumes_or_loses(tracker):
    tracker.apply(BASE + 5)
    tracker.mark_node_offline("fake01", BASE + 40)
    assert {s.status for s in tracker.open_sessions()} == {"unknown"}
    assert {w.status for w in tracker.open_workloads()} == {"unknown"}

    back = BASE + CHURN_PERIOD_S + 5  # 다음 주기: churn 프로세스는 새 PID 로 바뀜
    tracker.apply(back)
    resumed = next(s for s in tracker.open_sessions() if s.exec_username == "ollama")
    assert resumed.status == "active"
    assert resumed.unknown_seconds == pytest.approx(CHURN_PERIOD_S)
    assert resumed.mem_gb_hours == 0, "미확인 구간은 누적하지 않는다"

    lost = [s for s in tracker.sessions.values() if s.ended_reason == "lost_while_offline"]
    assert len(lost) == 1 and lost[0].cmdline.startswith("python eval.py")
    assert lost[0].end_ts == BASE + 5


def test_reboot_ends_everything_and_starts_fresh(tracker):
    tracker.apply(BASE + 5)
    old_ids = {s.id for s in tracker.open_sessions()}
    tracker.apply(BASE + 10, boot_id="rebooted")
    rebooted = [s for s in tracker.sessions.values() if s.ended_reason == "node_rebooted"]
    assert {s.id for s in rebooted} == old_ids
    fresh = tracker.open_sessions()
    assert len(fresh) == 8 and not ({s.id for s in fresh} & old_ids)


def test_offline_timeout_closes_unknown_sessions(tracker):
    tracker.apply(BASE + 5)
    tracker.mark_node_offline("fake01", BASE + 40)
    assert tracker.close_offline_timeouts("fake01", BASE + 3600) == 0
    assert tracker.close_offline_timeouts("fake01", BASE + 5 + 86400 + 1) == 8
    assert tracker.open_sessions() == []
    assert all(s.ended_reason == "node_offline_timeout" for s in tracker.sessions.values())


def test_take_dirty_drops_ended(tracker):
    tracker.apply(BASE + 5)
    workloads, sessions = tracker.take_dirty()
    assert len(workloads) == 6 and len(sessions) == 8
    assert tracker.take_dirty() == ([], [])

    tracker.apply(BASE + CHURN_ALIVE_S + 5)
    tracker.apply(BASE + CHURN_ALIVE_S + 10)
    workloads, sessions = tracker.take_dirty()
    ended = [s for s in sessions if s.status == "ended"]
    assert len(ended) == 1
    assert ended[0].key not in tracker.sessions
    assert ended[0].workload_id not in tracker.workloads


def test_restore_marks_unknown_and_next_report_reconciles(tracker, config, fake_report):
    tracker.apply(BASE + 5)
    workloads, sessions = tracker.take_dirty()

    fresh = SessionTracker()
    fresh.restore(workloads, sessions)
    assert {s.status for s in fresh.open_sessions()} == {"unknown"}
    assert len(fresh.open_workloads()) == 6

    resolver = OwnerResolver(config)
    report = fake_report(now=BASE + 20)
    fresh.apply_report(report, BASE + 20, lambda p: resolver.classify(p, "fake01"))
    assert {s.status for s in fresh.open_sessions()} == {"active"}
    assert {s.id for s in fresh.open_sessions()} == {s.id for s in sessions}, (
        "같은 세션으로 이어진다"
    )


def test_take_dirty_transitions_only(tracker):
    tracker.apply(BASE + 5)
    workloads, sessions = tracker.take_dirty(transitions_only=True)
    assert len(workloads) == 6 and len(sessions) == 8, "새로 생긴 것은 전이다"
    tracker.apply(BASE + 6)
    assert tracker.take_dirty(transitions_only=True) == ([], []), "누적값만 바뀐 것은 모아서 쓴다"
    workloads, sessions = tracker.take_dirty()
    assert len(workloads) == 6 and len(sessions) == 8, "전체 호출에서는 누적값도 쓴다"

    tracker.apply(BASE + CHURN_ALIVE_S + 5)
    tracker.apply(BASE + CHURN_ALIVE_S + 6)
    workloads, sessions = tracker.take_dirty(transitions_only=True)
    assert [s.status for s in sessions] == ["ended"]
    assert [w.status for w in workloads] == ["ended"]
    assert sessions[0].key not in tracker.sessions, "끝난 것은 메모리에서 빠진다"
