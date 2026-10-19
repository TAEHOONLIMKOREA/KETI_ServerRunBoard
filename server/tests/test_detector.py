from runboard_server.config import Thresholds
from runboard_server.core.detector import (
    activity,
    badges,
    conn_status,
    health,
    occupancy,
    summary_color,
)

TH = Thresholds()


def test_conn_status_thresholds():
    assert conn_status(None, 100, TH) == "offline"
    assert conn_status(100, 110, TH) == "online"
    assert conn_status(100, 116, TH) == "delayed"
    assert conn_status(100, 131, TH) == "offline"


def test_occupancy():
    assert occupancy([]) == "free"
    assert occupancy(["shared_service"]) == "service"
    assert occupancy(["shared_service", "personal"]) == "occupied"
    assert occupancy(["container"]) == "occupied"


def test_activity_window_and_unknown():
    now = 1000.0
    assert activity([], now, TH) == "unknown"
    assert activity([(now - 10, None), (now - 5, None)], now, TH) == "unknown", (
        "수집 불가는 0 이 아니다"
    )
    assert activity([(now - 10, 50), (now - 5, 30)], now, TH) == "active"
    assert activity([(now - 10, 5), (now - 5, 0)], now, TH) == "low"
    assert activity([(now - 120, 90), (now - 5, 0)], now, TH) == "low", "창 밖 샘플은 무시"


def test_health_priority():
    assert health(50, False, TH) == "ok"
    assert health(90, False, TH) == "temp_warn"
    assert health(90, True, TH) == "collect_error"
    assert health(None, False, TH) == "ok"


def test_summary_color():
    assert summary_color("offline", "occupied", 100, 100, 100, False, TH) == "gray"
    assert summary_color("online", "free", 0, 100, 0, False, TH) == "green"
    assert summary_color("online", "service", 50, 100, 3, False, TH) == "blue"
    assert summary_color("online", "occupied", 30, 100, 20, False, TH) == "yellow"
    assert summary_color("online", "occupied", 80, 100, 20, False, TH) == "red", "메모리 70% 이상"
    assert summary_color("online", "occupied", 30, 100, 95, False, TH) == "red", "사용률 90% 이상"
    assert summary_color("online", "occupied", 30, 100, 0, True, TH) == "orange"


def test_badges():
    assert badges("online", "active", "ok", False, False, False) == []
    out = badges("delayed", "unknown", "collect_error", True, True, True)
    assert out == ["지연", "수집 불가", "수집 오류", "미확인 소유자", "미확인", "유휴 의심"]
