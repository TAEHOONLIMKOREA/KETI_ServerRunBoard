"""상태 4축 판정과 요약 색 (PLAN 4.6). 전부 순수 함수다.

| 축 | 값 |
|---|---|
| 연결 | online / delayed / offline |
| 점유 | free / occupied / service |
| 활동 | active / low / unknown |
| 건강 | ok / temp_warn / collect_error |
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Literal

from runboard_server.config import Thresholds

ConnStatus = Literal["online", "delayed", "offline"]
Occupancy = Literal["free", "occupied", "service"]
Activity = Literal["active", "low", "unknown"]
Health = Literal["ok", "temp_warn", "collect_error"]
Color = Literal["gray", "green", "blue", "yellow", "red", "orange"]


def conn_status(
    last_received_ts: float | None, now: float, th: Thresholds, interval_s: float = 0.0
) -> ConnStatus:
    """마지막 수신 뒤 '보고 주기 + delayed_s' 가 지나면 지연, '보고 주기 + offline_s' 가 지나면 끊김.

    interval_s 는 에이전트가 보고에 적어 보낸 다음 보고까지의 최대 간격이다 (동작 중 1초, 유휴 30초).
    모르면 0 으로 두어 임계값만으로 판정한다.
    """
    if last_received_ts is None:
        return "offline"
    age = now - last_received_ts
    if age > interval_s + th.offline_s:
        return "offline"
    if age > interval_s + th.delayed_s:
        return "delayed"
    return "online"


def occupancy(workload_types: Iterable[str]) -> Occupancy:
    types = set(workload_types)
    if types & {"personal", "container"}:
        return "occupied"
    if "shared_service" in types:
        return "service"
    return "free"


def activity(
    util_history: Iterable[tuple[float, int | None]], now: float, th: Thresholds
) -> Activity:
    """최근 activity_window_s 동안 GPU 전체 사용률 평균. 유효 샘플이 없으면 unknown (수집 불가)."""
    values = [
        util for ts, util in util_history if util is not None and now - ts <= th.activity_window_s
    ]
    if not values:
        return "unknown"
    return "active" if sum(values) / len(values) >= th.activity_low_pct else "low"


def health(temp_c: int | None, has_collect_error: bool, th: Thresholds) -> Health:
    if has_collect_error:
        return "collect_error"
    if temp_c is not None and temp_c >= th.temp_warn_c:
        return "temp_warn"
    return "ok"


def summary_color(
    conn: ConnStatus,
    occ: Occupancy,
    mem_used_mb: int | None,
    mem_total_mb: int,
    util_gpu: int | None,
    idle_hog: bool,
    th: Thresholds,
) -> Color:
    if conn == "offline":
        return "gray"
    if occ == "free":
        return "green"
    if idle_hog:
        return "orange"
    if occ == "service":
        return "blue"
    mem_pct = (mem_used_mb or 0) / mem_total_mb * 100 if mem_total_mb else 0.0
    if mem_pct >= th.occupied_mem_pct or (util_gpu or 0) >= th.busy_util_pct:
        return "red"
    return "yellow"


def badges(
    conn: ConnStatus,
    act: Activity,
    hlth: Health,
    has_unknown_owner: bool,
    has_unknown_session: bool,
    idle_hog: bool,
) -> list[str]:
    out: list[str] = []
    if conn == "delayed":
        out.append("지연")
    if act == "low":
        out.append("낮은 활동")
    if act == "unknown":
        out.append("수집 불가")
    if hlth == "temp_warn":
        out.append("온도")
    if hlth == "collect_error":
        out.append("수집 오류")
    if has_unknown_owner:
        out.append("미확인 소유자")
    if has_unknown_session:
        out.append("미확인")
    if idle_hog:
        out.append("유휴 의심")
    return out
