"""HTTP 경로 끝에서 끝까지: 인증 → ingest → overview/users/workloads, tick 으로 오프라인 전이."""

import pytest
from fastapi.testclient import TestClient

from runboard_agent.collector.fake import CHURN_PERIOD_S
from runboard_server.core.engine import UNKNOWN_USER
from runboard_server.main import create_app

T0 = 1_760_600_000.0
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)


@pytest.fixture
def app(config):
    class Clock:
        now = BASE + 5

    application = create_app(config, start_scheduler=False, use_db=False)
    application.state.engine.clock = lambda: Clock.now
    application.state.clock = Clock
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


def post(client, report, token="dev-token-fake01"):
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post("/api/v1/report", content=report.model_dump_json(), headers=headers)


def test_auth(client, fake_report):
    report = fake_report(now=BASE + 5)
    assert post(client, report, token=None).status_code == 401
    assert post(client, report, token="wrong").status_code == 401
    assert post(client, report).status_code == 204
    gpu01 = fake_report(now=BASE + 5, node="gpu01")
    assert post(client, gpu01, token="dev-token-gpu01").status_code == 204, "sha256 토큰"
    assert post(client, gpu01, token="dev-token-fake01").status_code == 401, "다른 노드 토큰"


def test_invalid_report_is_422(client):
    r = client.post(
        "/api/v1/report",
        json={"node": {"name": "bad name"}},
        headers={"Authorization": "Bearer dev-token-fake01"},
    )
    assert r.status_code == 422


def test_overview_reflects_types_owners_and_axes(client, fake_report):
    assert post(client, fake_report(now=BASE + 5)).status_code == 204
    body = client.get("/api/v1/overview").json()
    assert body["summary"] == {
        "nodes": 1,
        "nodes_offline": 0,
        "gpus": 8,
        "gpus_free": 1,
        "gpus_unknown_owner": 1,
        "workloads": 6,
    }
    node = body["nodes"][0]
    assert node["name"] == "fake01" and node["conn_status"] == "online"
    gpus = {g["index"]: g for g in node["gpus"]}

    ddp = gpus[0]["workloads"][0]
    assert ddp["type"] == "container" and ddp["owner"] == "taehoon"
    assert ddp["owner_display"] == "임태훈" and ddp["owner_source"] == "label"
    assert ddp["gpu_count"] == 2 and ddp["name"] == "seg-train"
    assert gpus[1]["workloads"][0]["workload_id"] == ddp["workload_id"], "GPU 0,1 은 같은 작업"

    assert gpus[2]["workloads"][0]["owner"] == "minsu"
    assert gpus[2]["activity"] == "low" and "낮은 활동" in gpus[2]["badges"]

    ollama = gpus[3]
    assert ollama["occupancy"] == "service" and ollama["color"] == "blue"
    assert ollama["workloads"][0]["type"] == "shared_service"
    assert ollama["workloads"][0]["util_sm"] is None, "수집 불가는 null"
    assert ollama["health"] == "collect_error" and "수집 오류" in ollama["badges"]

    jihye = gpus[5]
    assert len(jihye["workloads"]) == 1 and jihye["workloads"][0]["pids"] == [5100, 5101]
    assert jihye["color"] == "red"

    unknown = gpus[6]["workloads"][0]
    assert unknown["owner"] is None and unknown["owner_source"] == "none"
    assert "미확인 소유자" in gpus[6]["badges"]

    assert gpus[7]["occupancy"] == "free" and gpus[7]["color"] == "green"
    assert gpus[7]["workloads"] == []


def test_users_and_workloads_endpoints(client, fake_report):
    post(client, fake_report(now=BASE + 5))
    users = {u["username"]: u for u in client.get("/api/v1/users").json()["users"]}
    assert users["taehoon"]["display_name"] == "임태훈"
    assert users["taehoon"]["workloads"] == 3, "DDP, churn(eval.py), ollama 담당"
    assert users["taehoon"]["gpus"] == 4
    assert users["jihye"]["gpus"] == 1 and users["jihye"]["workloads"] == 1
    assert users[None]["display_name"] == "미확인" and users[None]["workloads"] == 1
    assert users["minsu"]["registered"] is True

    detail = client.get(f"/api/v1/users/{UNKNOWN_USER}").json()
    assert [w["name"] for w in detail["workloads"]] == ["stoic_lovelace"]
    assert client.get("/api/v1/users/nobody").status_code == 404

    wls = client.get("/api/v1/workloads", params={"owner": "taehoon", "type": "container"}).json()
    assert [w["name"] for w in wls["workloads"]] == ["seg-train"]
    one = client.get(f"/api/v1/workloads/{wls['workloads'][0]['id']}").json()
    assert one["gpu_indexes"] == [0, 1] and len(one["sessions"]) == 2
    assert client.get("/api/v1/workloads/nope").status_code == 404
    assert client.get("/api/v1/nodes/fake01").json()["gpu_count"] == 8
    assert client.get("/api/v1/nodes/none").status_code == 404
    assert client.get("/api/v1/healthz").json()["nodes"] == 1


def test_tick_marks_offline_and_return_resumes(client, app, fake_report):
    clock = app.state.clock
    engine = app.state.engine
    post(client, fake_report(now=BASE + 5))

    clock.now = BASE + 25
    engine.tick()
    node = client.get("/api/v1/overview").json()["nodes"][0]
    assert node["conn_status"] == "delayed" and "지연" in node["gpus"][0]["badges"]

    clock.now = BASE + 40
    engine.tick()
    body = client.get("/api/v1/overview").json()
    node = body["nodes"][0]
    assert body["summary"]["nodes_offline"] == 1
    assert node["conn_status"] == "offline"
    assert node["gpus"][0]["color"] == "gray"
    assert "미확인" in node["gpus"][0]["badges"] and "수집 불가" not in node["gpus"][0]["badges"]
    assert node["gpus"][0]["workloads"][0]["status"] == "unknown"

    clock.now = BASE + CHURN_PERIOD_S + 5
    post(client, fake_report(now=clock.now))
    node = client.get("/api/v1/overview").json()["nodes"][0]
    assert node["conn_status"] == "online"
    assert node["gpus"][0]["workloads"][0]["status"] == "active"


def test_clock_skew_uses_received_time(client, app, fake_report):
    clock = app.state.clock
    clock.now = BASE + 5
    post(client, fake_report(now=BASE + 5 - 600))
    node = client.get("/api/v1/overview").json()["nodes"][0]
    assert node["clock_skew_flag"] is True and node["clock_skew_s"] == pytest.approx(600)
    assert node["conn_status"] == "online", "연결 판정은 수신 시각 기준"
