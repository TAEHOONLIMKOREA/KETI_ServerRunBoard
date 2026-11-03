"""관리 API: 사용자·공용 서비스를 DB 에 쓰고 판정에 바로 쓴다. 노드 목록·이름 변경·삭제."""

import pytest
from fastapi.testclient import TestClient

from runboard_agent.collector.fake import CHURN_PERIOD_S, fake_ip
from runboard_server.db.models import NodeRow, SessionRow, WorkloadRow
from runboard_server.db.repo import Repository
from runboard_server.main import create_app

T0 = 1_760_600_000.0
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)
NODE_TOKEN = "dev-token-fake01"
JOIN_TOKEN = "join-token"


@pytest.fixture
def app(config, registry, repo, seed):
    seed(repo, registry)
    config.auth.shared_token = JOIN_TOKEN

    class Clock:
        now = BASE + 5

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
    """now 시각으로 시계를 옮기고 그 시각의 fake 보고를 보낸다."""

    def _report(now: float, node: str = "fake01", token: str = NODE_TOKEN) -> None:
        app.state.clock.now = now
        r = client.post(
            "/api/v1/report",
            content=fake_report(now=now, node=node).model_dump_json(),
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        assert r.status_code == 204

    return _report


def gpu(client, index: int, node: str = "fake01") -> dict:
    return client.get(f"/api/v1/nodes/{node}").json()["gpus"][index]


def test_user_crud_and_validation(client):
    r = client.post(
        "/api/v1/admin/users",
        json={"username": "soyeon", "display_name": " 박소연 ", "aliases": ["sy", ""]},
    )
    assert r.status_code == 201
    assert r.json() == {
        "username": "soyeon",
        "display_name": "박소연",
        "email": None,
        "slack_id": None,
        "aliases": ["sy"],
    }
    names = [u["username"] for u in client.get("/api/v1/admin/users").json()]
    assert names == ["taehoon", "minsu", "jihye", "soyeon"], "맨 뒤에 붙는다"

    assert client.post("/api/v1/admin/users", json={"username": "soyeon"}).status_code == 409
    r = client.post("/api/v1/admin/users", json={"username": "TH"})
    assert r.status_code == 409 and "taehoon" in r.json()["detail"], "다른 사용자의 별칭과 겹친다"
    assert client.post("/api/v1/admin/users", json={"username": "bad name"}).status_code == 422

    r = client.put("/api/v1/admin/users/soyeon", json={"aliases": ["Minsu"]})
    assert r.status_code == 409, "다른 사용자 이름을 별칭으로 쓸 수 없다"
    r = client.put(
        "/api/v1/admin/users/soyeon", json={"display_name": "소연", "email": "s@keti.re.kr"}
    )
    assert r.status_code == 200 and r.json()["aliases"] == [], "PUT 은 전체를 바꾼다"
    assert client.put("/api/v1/admin/users/nobody", json={}).status_code == 404

    order = ["soyeon", "jihye", "minsu", "taehoon"]
    r = client.put("/api/v1/admin/user-order", json={"usernames": order})
    assert [u["username"] for u in r.json()] == order
    users = {u["username"]: u for u in client.get("/api/v1/users").json()["users"]}
    assert users["soyeon"]["display_name"] == "소연"
    assert (users["soyeon"]["color_slot"], users["taehoon"]["color_slot"]) == (0, 3), (
        "순서가 색 번호"
    )
    assert client.put("/api/v1/admin/user-order", json={"usernames": ["soyeon"]}).status_code == 409

    assert client.delete("/api/v1/admin/users/soyeon").status_code == 204
    assert client.delete("/api/v1/admin/users/soyeon").status_code == 404
    assert "soyeon" not in {u["username"] for u in client.get("/api/v1/users").json()["users"]}


def test_values_the_db_refuses_are_422_not_500(client):
    """등록 정보 문자열·경로 이름의 NUL 은 저장소가, 정수 열 범위 밖의 행 번호는 경로 검증이 막는다."""
    refused = "DB 에 넣을 수 없는 값이다 (NUL 같은 제어 문자 등)"
    users = client.get("/api/v1/admin/users").json()
    r = client.post(
        "/api/v1/admin/users", json={"username": "soyeon", "display_name": "박\x00소연"}
    )
    assert (r.status_code, r.json()["detail"]) == (422, refused)
    r = client.put("/api/v1/admin/users/jihye", json={"email": "j\x00@keti.re.kr"})
    assert (r.status_code, r.json()["detail"]) == (422, refused)
    r = client.delete("/api/v1/admin/users/%00")
    assert (r.status_code, r.json()["detail"]) == (422, refused), "경로의 이름에 든 NUL"
    r = client.post("/api/v1/admin/services", json={"name": "a\x00", "match_exec_account": "a"})
    assert (r.status_code, r.json()["detail"]) == (422, refused)
    r = client.put(
        "/api/v1/admin/users/jihye",
        content='{"email": "j\\udcff@keti.re.kr"}',  # httpx 의 json= 은 서로게이트를 보내지 못한다
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 422 and r.json()["detail"][0]["type"] == "string_unicode", (
        "짝 없는 서로게이트는 pydantic 이 막는다. 422 본문에서 입력값만 빠진다"
    )
    assert client.get("/api/v1/admin/users").json() == users, "아무것도 바뀌지 않는다"
    assert len(client.get("/api/v1/admin/services").json()) == 2

    body = {"name": "x", "match_exec_account": "a"}
    for row_id in (0, 2**31, 10**20):
        path = f"/api/v1/admin/services/{row_id}"
        for r in (client.put(path, json=body), client.delete(path)):
            assert r.status_code == 422 and r.json()["detail"][0]["loc"] == ["path", "service_id"]
    assert client.delete("/api/v1/admin/services/2147483647").status_code == 404, "범위 안이면 없다"


def test_registry_changes_apply_to_running_workloads_and_persist(client, config, repo, report):
    report(BASE + 5)
    assert gpu(client, 6)["workloads"][0]["owner"] is None, "컨테이너 안 root, 라벨 없음"

    r = client.put(
        "/api/v1/admin/users/minsu", json={"display_name": "김민수", "aliases": ["stoic"]}
    )
    assert r.status_code == 200
    report(BASE + 6)
    wl = gpu(client, 6)["workloads"][0]
    assert (wl["owner"], wl["owner_source"], wl["owner_display"]) == (
        "minsu",
        "container_rule",
        "김민수",
    ), "컨테이너 stoic_lovelace 가 별칭으로 맞는다. 돌고 있던 작업도 다음 보고에서 다시 판정한다"

    with TestClient(create_app(config, Repository(repo.db_url), start_scheduler=False)) as fresh:
        users = fresh.get("/api/v1/admin/users").json()
        assert next(u for u in users if u["username"] == "minsu")["aliases"] == ["stoic"], (
            "재시작해도 DB 에서 읽는다"
        )

    assert client.delete("/api/v1/admin/users/minsu").status_code == 204
    report(BASE + 7)
    assert gpu(client, 6)["workloads"][0]["owner"] is None
    assert gpu(client, 2)["workloads"][0]["owner"] is None, "minsu 계정의 jupyter 도 미확인"


def test_service_crud_changes_workload_type(client, report):
    base = "/api/v1/admin/services"
    report(BASE + 5)
    assert client.post(base, json={"name": "x"}).status_code == 422, (
        "매칭 규칙이 하나는 있어야 한다"
    )
    assert client.post(base, json={"name": "x", "match_image_pattern": "("}).status_code == 422
    assert client.post(base, json={"name": "OLLAMA", "match_exec_account": "a"}).status_code == 409
    r = client.post(base, json={"name": "x", "match_exec_account": "a", "owner": "ghost"})
    assert r.status_code == 422

    r = client.post(
        base,
        json={
            "name": "jupyter",
            "match_exec_account": "minsu",
            "owner": "minsu",
            "idle_exempt": False,
        },
    )
    assert r.status_code == 201
    service_id = r.json()["id"]
    report(BASE + 6)
    g = gpu(client, 2)
    assert g["occupancy"] == "service" and g["workloads"][0]["name"] == "jupyter"
    assert g["workloads"][0]["owner_source"] == "service"

    r = client.put(f"{base}/{service_id}", json={"name": "jupyter", "match_exec_account": "nobody"})
    assert r.status_code == 200 and r.json()["owner"] is None and r.json()["idle_exempt"] is True
    report(BASE + 7)
    g = gpu(client, 2)
    assert g["occupancy"] == "occupied" and g["workloads"][0]["name"] == "ipykernel_launcher"

    assert client.delete(f"{base}/{service_id}").status_code == 204
    assert client.delete(f"{base}/{service_id}").status_code == 404
    assert [s["name"] for s in client.get(base).json()] == ["ollama", "vllm"]


def test_node_rename_and_delete(client, repo, report):
    report(BASE + 5)
    report(BASE + 5, node="fake02", token=JOIN_TOKEN)
    base = "/api/v1/admin/nodes"

    r = client.patch(f"{base}/fake02", json={"display_name": " 연구실-A100 "})
    assert r.status_code == 200 and r.json()["display_name"] == "연구실-A100"
    with repo.orm() as db:
        assert db.get(NodeRow, "fake02").display_name == "연구실-A100"
    assert client.patch(f"{base}/fake01", json={"display_name": "연구실-A100"}).status_code == 409
    r = client.patch(f"{base}/fake01", json={"display_name": "fake02"})
    assert (r.status_code, r.json()["detail"]) == (
        409,
        "다른 노드(fake02)가 이미 쓰는 이름이다: fake02",
    ), "다른 노드의 식별자·호스트명도 안 된다. nodes 명령이 어느 노드인지 모른다"
    r = client.patch(f"{base}/fake01", json={"display_name": "fake01"})
    assert r.status_code == 200 and r.json()["display_name"] == "fake01", "자기 호스트명은 된다"
    assert client.patch(f"{base}/fake01", json={"display_name": "  "}).status_code == 422
    assert client.patch(f"{base}/fake01", json={"display_name": "a\x1bb"}).status_code == 422
    assert client.patch(f"{base}/nope", json={"display_name": "x"}).status_code == 404

    r = client.delete(f"{base}/fake02")
    assert r.status_code == 409 and "보고 중인" in r.json()["detail"]
    report(BASE + 40)  # fake01 만 계속 보고한다. fake02 는 35초째 끊김
    assert client.delete(f"{base}/fake02").status_code == 204
    assert client.delete(f"{base}/fake02").status_code == 404
    assert [n["key"] for n in client.get("/api/v1/overview").json()["nodes"]] == ["fake01"]
    with repo.orm() as db:
        assert db.get(NodeRow, "fake02") is None
        sessions = db.query(SessionRow).filter(SessionRow.node == "fake02").all()
        assert sessions and {s.ended_reason for s in sessions} == {"node_removed"}, (
            "세션 이력은 남고 삭제로 끝난다"
        )
        assert db.query(WorkloadRow).filter(WorkloadRow.node == "fake02").count() > 0

    report(BASE + 41, node="fake02", token=JOIN_TOKEN)
    assert client.get("/api/v1/nodes/fake02").json()["display_name"] == "Server_2", (
        "지운 노드가 다시 보고하면 새로 등록된다"
    )


def test_new_node_name_skips_names_in_use(client, report):
    """자동 이름 Server_n 도 이름 변경과 같은 규칙이다. 다른 노드가 쓰는 이름이면 다음 번호로 넘어간다."""
    report(BASE + 5)
    client.patch("/api/v1/admin/nodes/fake01", json={"display_name": "Server_2"})
    report(BASE + 6, node="fake02", token=JOIN_TOKEN)
    report(BASE + 7, node="Server_4", token=JOIN_TOKEN)
    report(BASE + 8, node="fake05", token=JOIN_TOKEN)
    nodes = client.get("/api/v1/admin/nodes").json()
    assert [(n["key"], n["seq"], n["display_name"]) for n in nodes] == [
        ("fake01", 1, "Server_2"),
        ("fake02", 2, "Server_3"),
        ("Server_4", 3, "Server_4"),  # Server_3 은 다른 노드가 쓴다. 자기 호스트명은 피하지 않는다
        ("fake05", 4, "Server_5"),
    ]


def test_node_list_is_compact_and_in_dashboard_order(client, report):
    report(BASE + 5, node="fake02", token=JOIN_TOKEN)
    report(BASE + 6)
    client.patch("/api/v1/admin/nodes/fake01", json={"display_name": "연구실-A100"})
    report(BASE + 40)  # fake01 만 계속 보고한다. fake02 는 35초째 끊김
    nodes = client.get("/api/v1/admin/nodes").json()
    assert nodes == [
        {
            "key": "fake02",
            "display_name": "Server_1",
            "hostname": "fake02",
            "node_id": None,
            "seq": 1,
            "ip": fake_ip("fake02"),
            "gpu_count": 8,
            "conn_status": "offline",
            "last_received_ts": BASE + 5,
            "age_s": 35.0,
        },
        {
            "key": "fake01",
            "display_name": "연구실-A100",
            "hostname": "fake01",
            "node_id": None,
            "seq": 2,
            "ip": fake_ip("fake01"),
            "gpu_count": 8,
            "conn_status": "online",
            "last_received_ts": BASE + 40,
            "age_s": 0.0,
        },
    ], "처음 본 순서. GPU 는 수만 담는다"
    overview = client.get("/api/v1/overview").json()["nodes"]
    assert [n["key"] for n in overview] == ["fake02", "fake01"], "대시보드와 같은 순서"
