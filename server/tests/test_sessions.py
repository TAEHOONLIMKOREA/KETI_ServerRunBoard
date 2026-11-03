"""세션 상태 전이를 fake 수집기 시나리오로 검증한다."""

import logging

import pytest

from runboard_agent.collector.fake import CHURN_ALIVE_S, CHURN_PERIOD_S, fake_gpu_uuid
from runboard_server.core.owner import OwnerResolver
from runboard_server.core.sessions import SessionTracker

T0 = 1_760_600_000.0
# churn 프로세스가 살아 있는 구간의 시작점에 맞춘 기준 시각
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)
TAKEOVER_LOG = (
    "노드 %s 의 세션 %d개를 노드 %s 가 이어받는다 (같은 프로세스. 식별자가 바뀐 것으로 본다)"
)


@pytest.fixture
def tracker(config, registry, fake_report):
    resolver = OwnerResolver(config.owner, registry)
    t = SessionTracker(miss_reports=2, offline_close_after_s=86400)

    def apply(now: float, key: str | None = None, **kw):
        """key 는 노드 식별자. 호스트명이 같고 식별자만 다르면 같은 GPU·부팅 ID·PID 를 보고한다."""
        report = fake_report(now=now, **kw)
        t.apply_report(report, now, resolver.classify, node=key)
        return report

    t.apply = apply  # type: ignore[attr-defined]
    return t


def _wl_by_name(tracker, name):
    return next(w for w in tracker.open_workloads() if w.name == name)


def _proc_index(tracker) -> dict:
    """GPU 별 프로세스 색인을 펼친 것: 노드를 뺀 키 → 열린 세션의 키."""
    return {proc: key for procs in tracker._open_by_gpu.values() for proc, key in procs.items()}


def _assert_proc_index(tracker):
    """프로세스 색인은 열린 세션과 꼭 맞는다. GPU 별로 나뉘고 빈 GPU 항목은 남기지 않는다."""
    assert _proc_index(tracker) == {s.key[1:]: s.key for s in tracker.open_sessions()}
    assert all(
        procs and all(proc[0] == gpu for proc in procs)
        for gpu, procs in tracker._open_by_gpu.items()
    )


def _messages(caplog, level: int) -> list[str]:
    return [
        r.getMessage()
        for r in caplog.records
        if r.name == "runboard_server.core.sessions" and r.levelno == level
    ]


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


def test_restore_marks_unknown_and_next_report_reconciles(tracker, config, registry, fake_report):
    tracker.apply(BASE + 5)
    workloads, sessions = tracker.take_dirty()

    fresh = SessionTracker()
    fresh.restore(workloads, sessions)
    assert {s.status for s in fresh.open_sessions()} == {"unknown"}
    assert len(fresh.open_workloads()) == 6

    resolver = OwnerResolver(config.owner, registry)
    report = fake_report(now=BASE + 20)
    fresh.apply_report(report, BASE + 20, resolver.classify)
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


def test_registry_change_reclassifies_open_workloads(config, registry, fake_report):
    """등록 정보가 바뀌면 이미 돌고 있는 작업도 다음 보고 때 유형·소유자를 다시 판정한다."""
    from runboard_server.registry import UserEntry

    t = SessionTracker()
    resolver = OwnerResolver(config.owner, registry)

    def apply(now: float) -> None:
        report = fake_report(now=now)
        t.apply_report(report, now, resolver.classify)

    apply(BASE + 5)
    stoic = _wl_by_name(t, "stoic_lovelace")
    jihye = _wl_by_name(t, "train.py")
    assert stoic.owner is None and jihye.owner == "jihye"
    t.take_dirty()

    registry.users.append(UserEntry(username="stoic"))
    registry.users = [u for u in registry.users if u.username != "jihye"]
    resolver = OwnerResolver(config.owner, registry)
    apply(BASE + 6)
    assert stoic.owner is None, "세션은 처음 볼 때만 판정한다"

    t.mark_reclassify()
    apply(BASE + 7)
    assert (stoic.owner, stoic.owner_source) == ("stoic", "container_rule"), "컨테이너명 규칙"
    assert (jihye.owner, jihye.owner_source) == (None, "none"), "지운 사용자의 작업은 미확인"
    workloads, _ = t.take_dirty(transitions_only=True)
    assert {w.id for w in workloads} == {stoic.id, jihye.id}, "바뀐 작업만 바로 저장한다"
    apply(BASE + 8)
    assert t.take_dirty(transitions_only=True) == ([], []), "다시 판정은 한 번이면 끝"


def test_new_identifier_takes_over_sessions_of_offline_node(tracker, caplog):
    """재부팅 없이 식별자만 바뀐 노드는 같은 프로세스를 보고한다. 끊긴 옛 노드의 세션을 이어받는다."""
    caplog.set_level(logging.INFO, logger="runboard_server.core.sessions")
    tracker.apply(BASE + 5, churn=False)
    old = tracker.open_sessions("fake01")
    old_workloads = tracker.open_workloads("fake01")
    tracker.mark_node_offline("fake01", BASE + 40)
    tracker.take_dirty()

    tracker.apply(BASE + 41, key="id-new", churn=False)
    assert {(s.status, s.ended_reason) for s in old} == {("ended", "node_replaced")}
    assert {s.end_ts for s in old} == {BASE + 5}, "종료 시각은 옛 노드에서 마지막으로 본 시각"
    assert {w.status for w in old_workloads} == {"ended"}, "옛 노드의 작업도 끝난다"
    assert tracker.open_sessions("fake01") == [] and tracker.open_workloads("fake01") == []
    new = tracker.open_sessions("id-new")
    assert len(new) == 7 and {s.status for s in new} == {"active"}
    assert {s.key[1:] for s in new} == {s.key[1:] for s in old}, "같은 프로세스"
    assert not {s.id for s in new} & {s.id for s in old}, "세션은 새로 시작한다"
    assert {w.node for w in tracker.open_workloads()} == {"id-new"}
    assert _messages(caplog, logging.INFO) == [TAKEOVER_LOG % ("fake01", 7, "id-new")]

    _, sessions = tracker.take_dirty(transitions_only=True)
    assert {s.id for s in sessions} == {s.id for s in old + new}, "둘 다 전이라 바로 쓴다"
    _assert_proc_index(tracker)


def test_active_holder_keeps_process_until_its_node_goes_offline(tracker, caplog):
    """다른 노드가 아직 보고 중인 프로세스는 세션을 만들지 않는다 (에이전트 둘이 번갈아 새 세션을 열지 않게).

    경고는 두 노드 쌍마다 한 번. 그 노드가 끊기면 다음 보고가 이어받고, 그 뒤 다시 막히면 또 경고한다.
    """
    caplog.set_level(logging.INFO, logger="runboard_server.core.sessions")
    tracker.apply(BASE + 5, churn=False)
    held = {s.id for s in tracker.open_sessions("fake01")}
    tracker.take_dirty()
    for now in (BASE + 6, BASE + 7):
        tracker.apply(now, key="id-new", churn=False)
        tracker.apply(now, churn=False)
        tracker.take_dirty()
    assert tracker.open_sessions("id-new") == [] and tracker.open_workloads("id-new") == []
    assert {s.id for s in tracker.open_sessions("fake01")} == held
    assert {s.status for s in tracker.open_sessions("fake01")} == {"active"}
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1, "보고마다 남기지 않는다"
    assert warnings[0].startswith(
        "노드 id-new 가 보고한 프로세스의 세션이 노드 fake01 에 열려 있다"
    )
    _assert_proc_index(tracker)

    tracker.mark_node_offline("fake01", BASE + 40)
    tracker.apply(BASE + 41, key="id-new", churn=False)
    assert len(tracker.open_sessions("id-new")) == 7 and tracker.open_sessions("fake01") == []
    assert _messages(caplog, logging.INFO) == [TAKEOVER_LOG % ("fake01", 7, "id-new")]
    tracker.take_dirty()

    # 이번에는 새 노드가 끊기고 옛 식별자의 에이전트가 이어받는다. 그 뒤 새 노드가 돌아오면 다시 막힌다
    tracker.mark_node_offline("id-new", BASE + 80)
    tracker.apply(BASE + 81, churn=False)
    tracker.take_dirty()
    tracker.apply(BASE + 82, key="id-new", churn=False)
    assert len(tracker.open_sessions("fake01")) == 7 and tracker.open_sessions("id-new") == []
    assert len(_messages(caplog, logging.WARNING)) == 2, "이어받은 뒤 다시 막히면 또 남긴다"
    _assert_proc_index(tracker)


def test_restore_then_new_identifier_reports_first(tracker, config, registry, fake_report):
    """재시작하면 열린 세션은 전부 미확인이다. 식별자가 바뀐 노드가 먼저 보고하면 바로 이어받는다."""
    tracker.apply(BASE + 5, churn=False)
    workloads, sessions = tracker.take_dirty()

    fresh = SessionTracker()
    fresh.restore(workloads, sessions)
    _assert_proc_index(fresh)
    resolver = OwnerResolver(config.owner, registry)
    report = fake_report(now=BASE + 20, churn=False)
    fresh.apply_report(report, BASE + 20, resolver.classify, node="id-new")
    assert {s.ended_reason for s in sessions} == {"node_replaced"}
    assert fresh.open_sessions("fake01") == [] and len(fresh.open_sessions("id-new")) == 7
    assert {w.status for w in workloads} == {"ended"}
    _assert_proc_index(fresh)


def test_proc_index_is_cleaned_when_sessions_end(tracker):
    """프로세스 색인은 세션이 끝나는 모든 경로에서 빠진다. 남으면 다른 노드의 보고가 끝난 세션에 막힌다.

    엔진처럼 매번 take_dirty 로 끝난 세션을 메모리에서 뺀다.
    """

    def ended_reasons() -> set[str]:
        return {s.ended_reason for s in tracker.take_dirty()[1] if s.status == "ended"}

    tracker.apply(BASE + 5)
    assert len(_proc_index(tracker)) == 8
    tracker.apply(BASE + CHURN_ALIVE_S + 5)
    tracker.apply(BASE + CHURN_ALIVE_S + 10)
    assert ended_reasons() == {"exited"}
    _assert_proc_index(tracker)
    assert len(_proc_index(tracker)) == 7
    assert tracker.open_sessions_on_gpu(fake_gpu_uuid("fake01", 4)) == [], "churn 의 GPU"

    cycle = BASE + CHURN_PERIOD_S  # churn 주기마다 새 PID 로 다시 생긴다
    tracker.apply(cycle + 5, boot_id="rebooted")
    assert ended_reasons() == {"node_rebooted"}
    _assert_proc_index(tracker)
    assert len(_proc_index(tracker)) == 8
    assert {proc[1] for proc in _proc_index(tracker)} == {"rebooted"}

    tracker.mark_node_offline("fake01", cycle + 40)
    tracker.apply(cycle + CHURN_PERIOD_S + 5, boot_id="rebooted")  # 끊긴 사이 churn PID 가 바뀌었다
    assert ended_reasons() == {"lost_while_offline"}
    _assert_proc_index(tracker)
    assert len(_proc_index(tracker)) == 8

    tracker.mark_node_offline("fake01", cycle + CHURN_PERIOD_S + 40)
    assert tracker.close_offline_timeouts("fake01", cycle + CHURN_PERIOD_S + 5 + 86400 + 1) == 8
    assert ended_reasons() == {"node_offline_timeout"}
    assert tracker._open_by_gpu == {}

    later = BASE + 2 * 86400 + 5  # churn 이 살아 있는 구간
    tracker.apply(later, boot_id="rebooted")
    assert len(_proc_index(tracker)) == 8
    tracker.end_node("fake01", "node_removed")
    assert ended_reasons() == {"node_removed"}
    assert tracker._open_by_gpu == {}
    tracker.apply(later + 1, key="id-new", boot_id="rebooted")
    assert len(tracker.open_sessions("id-new")) == 8, "지운 노드의 세션에 막히지 않는다"


def test_requeue_returns_rows_on_next_take_that_writes(tracker):
    """DB 에 쓰지 못한 것은 다음 전체 take_dirty 가 다시 낸다. 전이만 내는 호출은 낼 전이가 있을 때만 함께 낸다."""
    tracker.apply(BASE + 5)
    workloads, sessions = tracker.take_dirty(transitions_only=True)
    tracker.requeue(workloads, sessions)
    assert tracker.take_dirty(transitions_only=True) == ([], []), (
        "DB 가 멈춘 동안 전이가 없는 보고는 DB 에 닿지 않는다"
    )
    again_w, again_s = tracker.take_dirty()
    assert {w.id for w in again_w} == {w.id for w in workloads}
    assert {s.id for s in again_s} == {s.id for s in sessions}
    assert tracker.take_dirty() == ([], []), "다시 낸 뒤에는 비운다"

    tracker.requeue(again_w, again_s)
    tracker.apply(BASE + 6)  # 돌려받은 세션이 그사이 다시 dirty 가 된다
    _, both = tracker.take_dirty()
    assert len(both) == len({s.id for s in both}) == 8, "같은 세션은 한 번만"

    tracker.apply(BASE + CHURN_ALIVE_S + 5)
    tracker.apply(BASE + CHURN_ALIVE_S + 10)
    workloads, sessions = tracker.take_dirty(transitions_only=True)
    churn = next(s for s in sessions if s.status == "ended")
    assert churn.key not in tracker.sessions, "끝난 세션은 꺼낼 때 메모리에서 빠진다"
    tracker.requeue(workloads, sessions)
    workloads, sessions = tracker.take_dirty()
    assert churn in sessions and tracker.workloads.get(churn.workload_id) is None
    assert any(w.id == churn.workload_id and w.status == "ended" for w in workloads)

    # 전이가 있는 보고는 어차피 DB 에 쓰므로 돌려받은 것도 함께 낸다. 따로 쓰면 그 사이 생긴 세션이
    # 돌려받은 작업을 참조하거나(외래 키) 돌려받은 종료보다 먼저 쓰여(uq_session_open_key) 건너뛰어진다
    tracker.requeue(workloads, sessions)
    assert tracker.take_dirty(transitions_only=True) == ([], [])
    tracker.apply(BASE + CHURN_PERIOD_S + 5)  # 다음 주기의 churn 이 새 PID 로 생긴다: 전이
    workloads, sessions = tracker.take_dirty(transitions_only=True)
    assert churn in sessions and any(w.id == churn.workload_id for w in workloads)
    assert any(s.is_open and s.gpu_uuid == churn.gpu_uuid for s in sessions), "새 churn 세션"
    assert churn not in tracker.take_dirty()[1], "낸 것은 버퍼에서 빠진다"


@pytest.mark.parametrize("transitions_only", [False, True], ids=["full", "transitions"])
def test_reappeared_process_keeps_new_session_while_old_end_is_buffered(
    config, registry, fake_report, transitions_only
):
    """끝난 세션을 DB 에 못 쓴 사이 같은 키의 프로세스가 다시 보이면 새 세션이 생긴다.

    다음 take_dirty 는 돌려받은 옛 세션을 새 세션과 함께 낸다. 전이만 내는 보고 경로도 같다 (같은 저장에서
    옛 종료를 먼저 써야 새 세션이 uq_session_open_key 에 걸리지 않는다). 그때 새 세션·작업을 메모리에서
    빼면 안 된다.
    """
    t = SessionTracker(miss_reports=2)
    resolver = OwnerResolver(config.owner, registry)

    def apply(now: float, without_gpu: int | None = None) -> None:
        report = fake_report(now=now, churn=False)
        if without_gpu is not None:
            report.gpus[without_gpu].processes.clear()
        t.apply_report(report, now, resolver.classify)

    apply(BASE + 5)
    t.take_dirty()
    old = next(s for s in t.open_sessions() if s.exec_username == "minsu")
    old_wl = t.workloads[old.workload_id]
    apply(BASE + 6, without_gpu=2)
    apply(BASE + 7, without_gpu=2)
    assert old.ended_reason == "exited"
    t.requeue(*t.take_dirty(transitions_only=True))  # DB 쓰기 실패

    apply(BASE + 8)
    new = t.sessions[old.key]
    assert new is not old and new.is_open
    new_wl = t.workloads[new.workload_id]
    assert new_wl is not old_wl and new_wl.key == old_wl.key

    workloads, sessions = t.take_dirty(transitions_only=transitions_only)
    assert old in sessions and new in sessions and old_wl in workloads
    assert t.sessions[old.key] is new, "새 세션은 메모리에 남는다"
    assert t._wl_by_key[new_wl.key] is new_wl and t.workloads[new_wl.id] is new_wl
    assert t.sessions_of_workload(new_wl.id) == [new]
    apply(BASE + 9)
    assert t.sessions[old.key] is new and new.last_seen_ts == BASE + 9, "같은 세션으로 이어진다"
    assert len(t.open_workloads()) == 5
    _assert_proc_index(t)
