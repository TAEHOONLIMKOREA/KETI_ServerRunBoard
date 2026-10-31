"""공지 배너: 관리 API 로 DB 에 쓰고, 엔진이 메모리 사본에서 기한 전인 것만 배너 순서로 overview 에 낸다."""

import pytest
from fastapi.testclient import TestClient

from runboard_server.db.repo import Repository
from runboard_server.main import create_app

T0 = 1_760_600_000.0
BASE = "/api/v1/admin/notices"
LATER = 4_000_000_000.0  # 2096. 실제 시계로 도는 새 앱에서도 아직 기한 전


@pytest.fixture
def app(config, repo):
    class Clock:
        now = T0

    application = create_app(config, repo, start_scheduler=False)
    application.state.engine.clock = lambda: Clock.now
    application.state.clock = Clock
    return application


@pytest.fixture
def client(app):
    with TestClient(app) as c:
        yield c


@pytest.fixture
def add(client, app):
    """at 시각(엔진 시계)에 공지를 등록하고 응답 본문을 돌려준다."""

    def _add(at: float, **body) -> dict:
        app.state.clock.now = at
        r = client.post(BASE, json=body)
        assert r.status_code == 201, r.text
        return r.json()

    return _add


def banner(client) -> list[str]:
    return [n["text"] for n in client.get("/api/v1/overview").json()["notices"]]


def test_notice_crud(client, app, add):
    created = add(T0, text="  11/7(금) 18시까지 Server_3 점검  ")
    assert created == {
        "id": 1,
        "text": "11/7(금) 18시까지 Server_3 점검",
        "level": "info",
        "until": None,
        "created_ts": T0,
    }, "앞뒤 공백을 지우고 수준은 안내, 기한은 없음이 기본"
    assert client.get(BASE).json() == [created]
    assert client.get("/api/v1/overview").json()["notices"] == [
        {"id": 1, "text": "11/7(금) 18시까지 Server_3 점검", "level": "info", "until": None}
    ], "overview 에는 등록 시각을 싣지 않는다"

    app.state.clock.now = T0 + 60
    body = {"text": "점검이 11/8 로 밀렸다", "level": "warning", "until": T0 + 3600}
    r = client.put(f"{BASE}/1", json=body)
    assert r.status_code == 200 and r.json() == {"id": 1, **body, "created_ts": T0}, (
        "고쳐도 등록 시각은 그대로"
    )
    r = client.put(f"{BASE}/1", json={"text": "기한 없이"})
    assert (r.json()["level"], r.json()["until"]) == ("info", None), "PUT 은 전체를 바꾼다"

    for method in ("put", "delete"):
        r = client.request(method, f"{BASE}/99", json={"text": "x"})
        assert r.status_code == 404 and r.json()["detail"] == "공지가 없다: 99"
    assert client.delete(f"{BASE}/1").status_code == 204
    assert client.delete(f"{BASE}/1").status_code == 404
    assert client.get(BASE).json() == [] and banner(client) == []


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ({"text": "   "}, "공지 내용을 적는다"),
        ({"text": "첫 줄\n둘째 줄"}, "공지는 한 줄로 적는다. 줄바꿈은 넣지 않는다"),
        ({"text": "a\r\nb"}, "공지는 한 줄로 적는다. 줄바꿈은 넣지 않는다"),
        ({"text": "a\u2028b"}, "공지는 한 줄로 적는다. 줄바꿈은 넣지 않는다"),
        ({"text": "\u200b"}, "공지 내용을 적는다"),  # 폭 없는 공백뿐이면 배너에 이름표만 보인다
        ({"text": "a\x00b"}, "공지에 제어 문자(탭 등)는 넣지 않는다"),  # PostgreSQL 이 받지 않는다
        ({"text": "열\t맞춤"}, "공지에 제어 문자(탭 등)는 넣지 않는다"),
        ({"text": "\x1b[31m빨강"}, "공지에 제어 문자(탭 등)는 넣지 않는다"),
        ({"text": "가" * 201}, "String should have at most 200 characters"),
        ({"level": "info"}, "Field required"),
        ({"text": "x", "level": "urgent"}, "Input should be 'info' or 'warning'"),
        ({"text": "x", "until": -1}, "Input should be greater than or equal to 0"),
        ({"text": "x", "until": 5e9}, "Input should be less than or equal to 4102444800"),
        ({"text": "x", "until": "inf"}, "Input should be a finite number"),
    ],
)
def test_notice_validation(client, add, body, message):
    r = client.post(BASE, json=body)
    assert r.status_code == 422
    assert [issue["msg"].removeprefix("Value error, ") for issue in r.json()["detail"]] == [message]
    notice = add(T0, text="원래 공지")
    assert client.put(f"{BASE}/{notice['id']}", json=body).status_code == 422, "고칠 때도 같다"
    assert client.get(BASE).json() == [notice]


def test_notice_text_is_trimmed_before_length_check(add):
    assert add(T0, text=f"  {'가' * 200}\n")["text"] == "가" * 200, "끝의 줄바꿈은 공백처럼 지운다"


def test_unstorable_input_is_422_not_500(client, add):
    """JSON 으로 되돌려 줄 수 없는 입력(짝 없는 서로게이트, NaN)과 DB 정수 열 범위 밖의 행 번호도 422 다."""
    notice = add(T0, text="원래 공지")
    headers = {"Content-Type": "application/json"}
    bodies = [  # httpx 의 json= 은 둘 다 보내지 못해 본문을 직접 적는다
        (
            '{"text": "ab\\udcff"}',
            "Input should be a valid string, unable to parse raw data as a unicode string",
        ),
        ('{"text": "x", "until": NaN}', "Input should be a finite number"),
    ]
    for body, message in bodies:
        for method, path in (("post", BASE), ("put", f"{BASE}/{notice['id']}")):
            r = client.request(method, path, content=body, headers=headers)
            assert r.status_code == 422
            assert [issue["msg"] for issue in r.json()["detail"]] == [message]
            assert "input" not in r.json()["detail"][0], "입력값은 빼고 보낸다"
    r = client.post(BASE, json={"text": "\n"})
    assert r.status_code == 422 and r.json()["detail"][0]["input"] == "\n", (
        "보낼 수 있으면 그대로다"
    )
    assert client.get(BASE).json() == [notice] and banner(client) == ["원래 공지"]

    for notice_id in (0, 2**31, 10**20):
        assert client.put(f"{BASE}/{notice_id}", json={"text": "x"}).status_code == 422
        assert client.delete(f"{BASE}/{notice_id}").status_code == 422
    assert client.delete(f"{BASE}/{2**31 - 1}").status_code == 404, "범위 안이면 없는 공지다"


def test_banner_shows_unexpired_notices_warning_first(client, app, add):
    info_1 = add(T0, text="안내 1")
    add(T0 + 1, text="주의 1", level="warning", until=T0 + 100)
    info_2 = add(T0 + 2, text="안내 2", until=T0 + 50)
    warning_2 = add(T0 + 3, text="주의 2", level="warning")
    add(
        T0 + 4, text="벌써 지남", level="warning", until=T0 + 4
    )  # 지난 시각도 받는다: 바로 내려간다
    app.state.clock.now = T0 + 10
    assert banner(client) == ["주의 1", "주의 2", "안내 1", "안내 2"], (
        "주의가 먼저, 같은 수준은 오래된 것부터. 기한이 지금인 것은 지난 것으로 본다"
    )
    assert [n["text"] for n in client.get(BASE).json()] == [
        "주의 1",
        "주의 2",
        "벌써 지남",
        "안내 1",
        "안내 2",
    ], "관리 목록은 기한이 지난 것까지 같은 순서로"

    app.state.clock.now = T0 + 50
    assert banner(client) == ["주의 1", "주의 2", "안내 1"]
    app.state.clock.now = T0 + 100.5
    assert banner(client) == ["주의 2", "안내 1"], "재시작 없이 기한이 지나면 내려간다"

    r = client.put(f"{BASE}/{warning_2['id']}", json={**warning_2, "until": T0})
    assert r.status_code == 200 and banner(client) == ["안내 1"], (
        "지난 시각으로 고치면 바로 내려간다"
    )
    client.put(f"{BASE}/{info_2['id']}", json={"text": "안내 2", "level": "warning"})
    client.put(f"{BASE}/{info_1['id']}", json={"text": "안내 1", "level": "warning"})
    assert banner(client) == ["안내 1", "안내 2"], "수준을 바꿔도 등록 순서는 그대로"

    same = [add(T0 + 200, text=f"같은 시각 {i}") for i in range(3)]
    assert [n["id"] for n in same] == [6, 7, 8]
    assert banner(client)[-3:] == ["같은 시각 0", "같은 시각 1", "같은 시각 2"], "같으면 id 순"


def test_overview_reads_notices_from_memory(client, app, add, monkeypatch):
    """overview 는 보는 사람마다 1초마다 부르므로 DB 를 읽지 않는다. 고칠 때만 다시 읽는다."""
    add(T0, text="메모리에서")

    def no_db():
        raise AssertionError("overview 가 공지를 DB 에서 읽었다")

    monkeypatch.setattr(app.state.repo, "load_notices", no_db)
    for _ in range(3):
        assert banner(client) == ["메모리에서"]
    assert client.get(BASE).json()[0]["text"] == "메모리에서"


def test_notices_survive_restart(client, config, repo, add):
    first = add(T0, text="재시작해도 남는다", until=LATER)
    second = add(T0 + 1, text="주의도", level="warning")
    with TestClient(create_app(config, Repository(repo.db_url), start_scheduler=False)) as fresh:
        assert fresh.get(BASE).json() == [second, first], "시작할 때 DB 에서 읽는다"
        assert [n["text"] for n in fresh.get("/api/v1/overview").json()["notices"]] == [
            "주의도",
            "재시작해도 남는다",
        ]


def test_without_db_there_are_no_notices(config, registry):
    app = create_app(config, start_scheduler=False, use_db=False, registry=registry)
    with TestClient(app) as client:
        assert client.get("/api/v1/overview").json()["notices"] == []
        assert client.get(BASE).json() == []
        assert client.post(BASE, json={"text": "x"}).status_code == 503
        assert client.put(f"{BASE}/1", json={"text": "x"}).status_code == 503
        assert client.delete(f"{BASE}/1").status_code == 503
