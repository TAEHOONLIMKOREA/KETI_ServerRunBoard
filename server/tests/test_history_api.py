"""이력 조회 API: 노드 이력(24h·7d 고정 격자)과 GPU 이력(raw·5m·1h). 보고 → 샘플 → tick 집계 → 조회."""

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from runboard_agent.collector.fake import fake_gpu_uuid
from runboard_common.schemas import NodeReport
from runboard_server.main import create_app

H0 = 1_760_598_000  # 정각. 5분 경계이고 fake churn 주기(90초)의 시작이기도 하다
DAY = 86400
GPU0 = fake_gpu_uuid("fake01", 0)


@pytest.fixture
def app(config, repo):
    class Clock:
        now = H0 + 5

    application = create_app(config, repo, start_scheduler=False)
    application.state.engine.clock = lambda: Clock.now
    application.state.clock = Clock
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def report(client, app, fake_report):
    """now 시각으로 시계를 옮기고 그 시각의 fake 보고를 보낸다. edit 으로 보고를 고쳐 보낼 수 있다."""

    def _report(now: float, edit: Callable[[NodeReport], NodeReport] | None = None, **kw) -> None:
        app.state.clock.now = now
        body = fake_report(now=now, **kw)
        if edit is not None:
            body = edit(body)
        r = client.post(
            "/api/v1/report",
            content=body.model_dump_json(),
            headers={
                "Authorization": "Bearer dev-token-fake01",
                "Content-Type": "application/json",
            },
        )
        assert r.status_code == 204

    return _report


@pytest.fixture
def ten_minutes(app, report):
    """H0 부터 10분 동안 30초마다 보고하고(GPU 마다 샘플 20개) tick 으로 닫힌 5분 버킷 둘을 집계한다."""
    for k in range(20):
        report(H0 + 5 + 30 * k)
    app.state.clock.now = H0 + 600 + 60
    app.state.engine.tick()


def test_node_history_24h_is_288_five_minute_points(client, ten_minutes):
    body = client.get("/api/v1/nodes/fake01/history").json()
    end = H0 + 600  # 지금(H0+660) 닫힌 마지막 5분 경계
    assert (body["key"], body["display_name"], body["range"]) == ("fake01", "Server_1", "24h")
    assert (body["step_s"], body["from_ts"], body["to_ts"]) == (300, end - DAY, end)
    gpus = body["gpus"]
    assert [g["index"] for g in gpus] == list(range(8))
    assert (gpus[0]["uuid"], gpus[0]["mem_total_mb"]) == (GPU0, 81920)
    for g in gpus:
        assert [p["ts"] for p in g["points"]] == list(range(end - DAY, end, 300)), "288점 고정 격자"

    jupyter = gpus[2]["points"]
    assert jupyter[-1] == {
        "ts": H0 + 300,
        "util_avg": 0.0,
        "util_max": 0,
        "mem_avg_mb": 30200.0,
        "mem_max_mb": 30200,
        "temp_max": 38,
        "power_avg": 60.0,
        "occupied_ratio": 1.0,
    }
    assert jupyter[-2]["ts"] == H0 and jupyter[-2]["mem_max_mb"] == 30200
    empty = jupyter[-3]
    assert empty["ts"] == H0 - 300 and set(empty.values()) == {H0 - 300, None}, "빈 칸은 null"
    assert gpus[7]["points"][-1]["occupied_ratio"] == 0.0, "빈 GPU"


def test_node_history_7d_is_168_hourly_points(client, app, ten_minutes):
    five = client.get(f"/api/v1/gpus/{GPU0}/history").json()["points"]
    app.state.clock.now = H0 + 3600 + 60  # H0 의 1시간이 닫히고
    app.state.engine.tick()  # 그 마지막 5분 버킷까지 집계된다
    body = client.get("/api/v1/nodes/fake01/history", params={"range": "7d"}).json()
    end = H0 + 3600
    assert (body["range"], body["step_s"], body["from_ts"], body["to_ts"]) == (
        "7d",
        3600,
        end - 7 * DAY,
        end,
    )
    points = body["gpus"][0]["points"]
    assert [p["ts"] for p in points] == list(range(end - 7 * DAY, end, 3600)), "168점 고정 격자"
    hour = points[-1]
    assert hour["ts"] == H0
    assert hour["util_avg"] == pytest.approx((five[0]["util_avg"] + five[1]["util_avg"]) / 2), (
        "5분 행을 샘플 수로 가중해 묶는다 (둘 다 10개)"
    )
    assert hour["util_max"] == max(p["util_max"] for p in five)
    assert points[-2]["util_avg"] is None


def test_node_history_ends_where_tick_has_aggregated(client, app, report):
    """구간이 닫히고 다음 tick(10초 주기)이 집계하기 전에는 그 구간을 넣지 않는다. 빈 점으로 끝나지 않는다."""
    url = "/api/v1/nodes/fake01/history"
    for k in range(20):
        report(H0 + 5 + 30 * k)
    app.state.engine.tick(now=H0 + 655)  # H0+300 버킷은 H0+660 에 닫힌다
    app.state.clock.now = H0 + 662
    body = client.get(url).json()
    last = body["gpus"][2]["points"][-1]
    assert (body["to_ts"], last["ts"], last["mem_max_mb"]) == (H0 + 300, H0, 30200), (
        "닫혔지만 아직 집계하지 않은 버킷은 빼고 끝난다"
    )
    app.state.engine.tick(now=H0 + 665)
    body = client.get(url).json()
    last = body["gpus"][2]["points"][-1]
    assert (body["to_ts"], last["ts"], last["mem_max_mb"]) == (H0 + 600, H0 + 300, 30200)

    report(H0 + 3590)  # H0 의 1시간 중 마지막 5분 버킷
    app.state.engine.tick(now=H0 + 3655)
    app.state.clock.now = H0 + 3662
    body = client.get(url, params={"range": "7d"}).json()
    assert body["to_ts"] == H0, "1시간은 5분 버킷 12개가 다 집계돼야 넣는다 (11개로 묶지 않는다)"
    app.state.engine.tick(now=H0 + 3665)
    body = client.get(url, params={"range": "7d"}).json()
    last = body["gpus"][2]["points"][-1]
    assert (body["to_ts"], last["ts"], last["mem_max_mb"]) == (H0 + 3600, H0, 30200)


def test_node_history_keeps_gpus_missing_from_latest_report(client, report, ten_minutes):
    report(H0 + 700, gpu_count=7)
    assert client.get("/api/v1/nodes/fake01").json()["gpu_count"] == 7
    gpus = client.get("/api/v1/nodes/fake01/history").json()["gpus"]
    assert len(gpus) == 8 and gpus[7]["points"][-1]["mem_max_mb"] == 1, (
        "보고에서 빠진 GPU 도 지난 이력은 보인다"
    )


def test_node_history_orders_gpus_sharing_an_index(client, report, ten_minutes):
    """가운데 GPU 가 빠져 뒤 번호가 당겨지면 gpus 표에 같은 index 가 둘 생긴다. 그래도 순서는 정해져 있다."""

    def drop_gpu3(r: NodeReport) -> NodeReport:
        kept = [g for g in r.gpus if g.index != 3]
        return r.model_copy(
            update={"gpus": [g.model_copy(update={"index": i}) for i, g in enumerate(kept)]}
        )

    report(H0 + 700, edit=drop_gpu3)
    gpus = client.get("/api/v1/nodes/fake01/history").json()["gpus"]
    keys = [(g["index"], g["uuid"]) for g in gpus]
    assert [i for i, _ in keys] == [0, 1, 2, 3, 3, 4, 5, 6]
    assert keys == sorted(keys), "같은 index 면 uuid 순"
    shared = {u for i, u in keys if i == 3}
    assert shared == {fake_gpu_uuid("fake01", n) for n in (3, 4)}, "빠진 3번과 당겨진 4번"


def test_gpu_history_raw_5m_1h(client, ten_minutes):
    url = f"/api/v1/gpus/{GPU0}/history"
    body = client.get(url).json()
    assert (body["gpu_uuid"], body["step"]) == (GPU0, "5m"), "기본은 지금까지 24시간의 5분 집계"
    five = body["points"]
    assert [p["ts"] for p in five] == [H0, H0 + 300], "데이터가 있는 점만"

    raw = client.get(url, params={"step": "raw", "from": H0, "to": H0 + 300}).json()["points"]
    assert [p["ts"] for p in raw] == [H0 + 5 + 30 * k for k in range(10)]
    assert all(p["util_avg"] == p["util_max"] and p["occupied_ratio"] == 1.0 for p in raw)
    assert five[0]["util_avg"] == pytest.approx(sum(p["util_avg"] for p in raw) / 10)
    assert five[0]["util_max"] == max(p["util_max"] for p in raw)

    hour = client.get(url, params={"step": "1h"}).json()["points"]
    assert [p["ts"] for p in hour] == [H0]
    partial = client.get(url, params={"from": H0 + 310, "to": H0 + 600}).json()["points"]
    assert [p["ts"] for p in partial] == [H0 + 300], "from 에 걸친 버킷부터"


def test_history_rejects_bad_queries(client, ten_minutes):
    url = f"/api/v1/gpus/{GPU0}/history"
    r = client.get(url, params={"step": "raw", "from": H0 - DAY - 1, "to": H0})
    assert r.status_code == 422 and "24시간" in r.json()["detail"]
    assert client.get(url, params={"step": "raw", "from": H0 - DAY, "to": H0}).status_code == 200
    assert client.get(url, params={"step": "raw"}).status_code == 200, "기본 구간은 24시간"
    assert client.get(url, params={"from": H0, "to": H0}).status_code == 422
    assert client.get(url, params={"from": "nan"}).status_code == 422, "500 이 아니다"
    for params in ({"from": 0, "to": 1e19}, {"from": -1e300}, {"to": 1e300, "step": "1h"}):
        assert client.get(url, params=params).status_code == 422, (
            "BIGINT 를 넘는 시각도 500 이 아니다"
        )
    assert client.get(url, params={"from": 0, "to": 4_102_444_800}).status_code == 200
    assert client.get(url, params={"step": "10m"}).status_code == 422
    assert client.get("/api/v1/gpus/GPU-nope/history").status_code == 404
    assert client.get("/api/v1/nodes/nope/history").status_code == 404
    r = client.get("/api/v1/nodes/fake01/history", params={"range": "30d"})
    assert r.status_code == 422


def test_history_needs_db(config, registry):
    app = create_app(config, start_scheduler=False, use_db=False, registry=registry)
    with TestClient(app) as c:
        assert c.get("/api/v1/nodes/fake01/history").status_code == 503
        assert c.get(f"/api/v1/gpus/{GPU0}/history").status_code == 503
