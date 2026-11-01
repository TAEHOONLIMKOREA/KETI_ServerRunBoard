"""runboard-server nodes: 관리 API 를 부르는 CLI. HTTP 는 cli.http 를 바꿔 끼워 TestClient 로 실제 앱에 보낸다."""

import io
import json
import socket
import sys
import threading
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

import runboard_server.main as server_main
from runboard_agent.collector.fake import CHURN_PERIOD_S
from runboard_server import cli
from runboard_server.main import create_app

T0 = 1_760_600_000.0
BASE = float((int(T0) // CHURN_PERIOD_S) * CHURN_PERIOD_S)
SERVER = "http://runboard.test:8000"
JOIN_TOKEN = "join-token"


class Stdin(io.StringIO):
    """확인 질문에 답하는 표준 입력. tty 가 거짓이면 파이프·cron 처럼 터미널이 아니다."""

    def __init__(self, answer: str = "", tty: bool = True) -> None:
        super().__init__(answer)
        self.tty = tty

    def isatty(self) -> bool:
        return self.tty


@pytest.fixture
def app(config, repo):
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


def route(client: TestClient, requests: list[tuple[str, str]] | None = None):
    """cli.http 대신 client 로 보내는 함수. 본문은 cli.http 처럼 json.dumps(ASCII 이스케이프) 로 보낸다."""

    def http(method: str, url: str, body=None) -> tuple[int, bytes]:
        if requests is not None:
            requests.append((method, url))
        data = None if body is None else json.dumps(body)
        headers = {"Content-Type": "application/json"}
        r = client.request(method, urlsplit(url).path, content=data, headers=headers)
        return r.status_code, r.content

    return http


@pytest.fixture
def sent(client, monkeypatch) -> list[tuple[str, str]]:
    """CLI 의 요청을 TestClient 로 보낸다. 보낸 (메서드, URL) 을 차례로 모은다."""
    requests: list[tuple[str, str]] = []
    monkeypatch.setattr(cli, "http", route(client, requests))
    return requests


@pytest.fixture
def report(client, app, fake_report):
    """now 시각으로 시계를 옮기고 등록 토큰으로 fake 보고를 보낸다."""

    def _report(now: float, node: str = "fake01", node_id: str | None = None, **kw) -> None:
        app.state.clock.now = now
        body = fake_report(now=now, node=node, **kw)
        body.node.node_id = node_id
        r = client.post(
            "/api/v1/report",
            content=body.model_dump_json(),
            headers={"Authorization": f"Bearer {JOIN_TOKEN}", "Content-Type": "application/json"},
        )
        assert r.status_code == 204

    return _report


def run(capsys, *argv: str) -> tuple[int, str, str]:
    """runboard-server nodes <argv> --server SERVER 를 실행하고 (종료 코드, stdout, stderr)."""
    code = server_main.main(["nodes", *argv, "--server", SERVER])
    out = capsys.readouterr()
    return code, out.out, out.err


def test_list_prints_aligned_table(sent, client, report, capsys):
    report(BASE + 5)
    report(BASE + 5, node="fake02")
    client.patch("/api/v1/admin/nodes/fake02", json={"display_name": "연구실-A100"})
    report(BASE + 40)  # fake01 만 계속 보고한다. fake02 는 35초째 끊김
    code, out, err = run(capsys, "list")
    assert (code, err) == (0, "")
    assert out.splitlines() == [
        "화면 이름    식별자  호스트명  상태      마지막 보고  GPU",
        "Server_1     fake01  fake01    정상      0초 전         8",
        "연구실-A100  fake02  fake02    비활성화  35초 전        8",
    ], "한글은 두 칸으로 세어 맞춘다. 순서는 대시보드와 같다"
    assert sent == [("GET", f"{SERVER}/api/v1/admin/nodes")]


def test_list_without_nodes(sent, client, capsys):
    assert run(capsys, "list") == (0, "아직 보고한 노드가 없다.\n", "")


def test_rename_by_display_name_hostname_or_key(sent, client, report, capsys):
    report(BASE + 5)
    report(BASE + 5, node="fake02")
    code, out, _ = run(capsys, "rename", "Server_2", "연구실-A100")
    assert (code, out) == (0, "화면 이름을 바꿨다: Server_2 → 연구실-A100 (식별자 fake02)\n")
    assert sent[-1] == ("PATCH", f"{SERVER}/api/v1/admin/nodes/fake02")

    code, out, _ = run(capsys, "rename", "fake01", " A100-1호기 ")
    assert (code, out) == (0, "화면 이름을 바꿨다: Server_1 → A100-1호기 (식별자 fake01)\n"), (
        "앞뒤 공백은 서버가 지운다"
    )
    names = [n["display_name"] for n in client.get("/api/v1/overview").json()["nodes"]]
    assert names == ["A100-1호기", "연구실-A100"], "대시보드에 바로 반영된다"


def test_ambiguous_name_lists_candidates(sent, report, capsys):
    """호스트명이 같은 노드 둘 (node_id 가 다르다). GPU 가 없어야 GPU·세션 키가 겹치지 않는다."""
    report(BASE + 5, node="gpu", node_id="id-A", gpu_count=0)
    report(BASE + 5, node="gpu", node_id="id-B", gpu_count=0)
    code, out, err = run(capsys, "rename", "gpu", "x")
    assert (code, out) == (1, "")
    assert err == (
        "오류: 'gpu' 에 맞는 노드가 2개다: Server_1 (식별자 id-A, 호스트명 gpu), "
        "Server_2 (식별자 id-B, 호스트명 gpu). 노드 하나에만 맞는 화면 이름이나 식별자로 지정한다\n"
    )
    assert [method for method, _ in sent] == ["GET"], "고르지 못하면 고치지 않는다"

    assert run(capsys, "rename", "id-B", "gpu-b")[0] == 0, "식별자는 노드 하나에만 맞는다"
    assert run(capsys, "rename", "gpu-b", "gpu-b2")[1] == (
        "화면 이름을 바꿨다: gpu-b → gpu-b2 (식별자 id-B)\n"
    ), "화면 이름은 노드 하나에만 맞는다"
    assert run(capsys, "rename", "gpu", "x")[0] == 1, "호스트명은 여전히 둘에 맞는다"


def test_key_that_is_another_nodes_hostname_is_ambiguous(sent, client, report, capsys):
    """node_id 를 붙여 다시 설치하면 호스트명으로 등록됐던 옛 노드와 새 노드가 호스트명 a100 을 같이 쓴다.

    a100 은 옛 노드의 식별자이기도 하다. 식별자라고 옛 노드를 말없이 고르지 않는다.
    """
    report(BASE + 5, node="a100", gpu_count=0)
    report(BASE + 40, node="a100", node_id="3f2a9c1e-a100", gpu_count=0)
    ambiguous = (
        "오류: 'a100' 에 맞는 노드가 2개다: Server_1 (식별자 a100, 호스트명 a100), "
        "Server_2 (식별자 3f2a9c1e-a100, 호스트명 a100). "
        "노드 하나에만 맞는 화면 이름이나 식별자로 지정한다\n"
    )
    assert run(capsys, "rename", "a100", "A100-서버") == (1, "", ambiguous)
    assert run(capsys, "remove", "a100", "--yes") == (1, "", ambiguous)
    assert [method for method, _ in sent] == ["GET", "GET"], "어느 노드도 고치거나 지우지 않는다"

    assert run(capsys, "remove", "Server_1", "--yes")[0] == 0, "끊긴 옛 노드는 화면 이름으로 지운다"
    assert run(capsys, "rename", "a100", "A100-서버") == (
        0,
        "화면 이름을 바꿨다: Server_2 → A100-서버 (식별자 3f2a9c1e-a100)\n",
        "",
    ), "옛 노드를 지우면 호스트명이 새 노드 하나에만 맞는다"
    assert [n["key"] for n in client.get("/api/v1/admin/nodes").json()] == ["3f2a9c1e-a100"]


def test_new_node_key_that_is_another_nodes_display_name_is_ambiguous(sent, report, capsys):
    """화면 이름을 먼저 붙인 뒤 그 이름의 노드가 새로 등록됐다. 대시보드의 'fake02' 카드는 fake01 이다."""
    report(BASE + 5)
    assert run(capsys, "rename", "fake01", "fake02")[0] == 0, "아직 fake02 노드가 없다"
    report(BASE + 6, node="fake02")
    code, out, err = run(capsys, "remove", "fake02", "--yes")
    assert (code, out) == (1, "")
    assert err.startswith(
        "오류: 'fake02' 에 맞는 노드가 2개다: fake02 (식별자 fake01, 호스트명 fake01), "
        "Server_2 (식별자 fake02, 호스트명 fake02)."
    )
    assert "DELETE" not in {method for method, _ in sent}

    assert run(capsys, "rename", "Server_2", "fake01") == (
        1,
        "",
        "오류: 다른 노드(fake01)가 이미 쓰는 이름이다: fake01\n",
    ), "다른 노드의 식별자·호스트명은 화면 이름으로 쓸 수 없다 (409)"


def test_server_refusals_are_printed(sent, client, report, capsys, monkeypatch):
    report(BASE + 5)
    report(BASE + 5, node="fake02")
    assert run(capsys, "rename", "fake02", "Server_1") == (
        1,
        "",
        "오류: 다른 노드(fake01)가 이미 쓰는 이름이다: Server_1\n",
    ), "409"
    assert run(capsys, "rename", "fake02", "   ")[2] == "오류: 화면 이름을 적는다\n", "422"
    assert run(capsys, "rename", "fake02", "x" * 65)[2] == (
        "오류: 화면 이름: String should have at most 64 characters\n"
    )
    assert run(capsys, "rename", "fake02", "a\tb")[2] == (
        "오류: 화면 이름에 제어 문자(탭 등)는 넣지 않는다\n"
    ), "422"
    # UTF-8 이 아닌 터미널의 인자는 짝 없는 서로게이트로 들어온다 (surrogateescape). pydantic 이 막는다
    assert run(capsys, "rename", "fake02", "ab\udcff") == (
        1,
        "",
        "오류: 화면 이름: Input should be a valid string, unable to parse raw data as a unicode string\n",
    ), "422 (500 이 아니다)"
    assert client.get("/api/v1/nodes/fake02").json()["display_name"] == "Server_2", "그대로다"
    assert run(capsys, "rename", "nope", "x") == (
        1,
        "",
        "오류: 노드가 없다: nope. 'runboard-server nodes list' 로 이름과 식별자를 본다\n",
    )

    stale = client.get("/api/v1/admin/nodes").json()
    report(BASE + 40)  # fake02 는 35초째 끊김
    assert client.delete("/api/v1/admin/nodes/fake02").status_code == 204
    to_app = cli.http

    def stale_list(method: str, url: str, body=None) -> tuple[int, bytes]:
        if method == "GET":  # 목록을 읽은 뒤에 다른 사람이 지웠다
            return 200, json.dumps(stale).encode()
        return to_app(method, url, body)

    monkeypatch.setattr(cli, "http", stale_list)
    assert run(capsys, "rename", "fake02", "x") == (1, "", "오류: 노드가 없다: fake02\n"), "404"


def test_remove_confirms_and_server_refuses_reporting_node(
    sent, client, report, capsys, monkeypatch
):
    report(BASE + 5)
    report(BASE + 5, node="fake02")

    monkeypatch.setattr(sys, "stdin", Stdin("y\n", tty=False))
    assert run(capsys, "remove", "Server_2") == (
        1,
        "",
        "오류: 확인할 터미널이 없어 지우지 않는다. 터미널에서 실행하거나 --yes 를 붙인다\n",
    )
    assert [method for method, _ in sent] == ["GET"], "터미널이 아니면 묻지도 지우지도 않는다"

    code, out, err = run(capsys, "remove", "Server_2", "--yes")
    assert (code, out) == (1, "")
    assert err == (
        "오류: 보고 중인 노드는 지울 수 없다. 에이전트를 멈추고 '비활성화' 가 된 뒤에 지운다\n"
    ), "보고 중인 노드는 서버가 거절한다 (409)"

    report(BASE + 40)  # fake01 만 계속 보고한다. fake02 는 35초째 끊김
    monkeypatch.setattr(sys, "stdin", Stdin("\n"))
    code, out, _ = run(capsys, "remove", "Server_2")
    assert code == 1
    assert out == (
        "Server_2 (식별자 fake02, 호스트명 fake02) 노드를 지운다 (상태 비활성화, 마지막 보고 35초 전). "
        "열린 작업은 '노드 삭제'로 끝나고 지난 작업·세션 이력은 남는다. 계속할까? [y/N] 지우지 않았다.\n"
    ), "기본 답은 아니오"

    monkeypatch.setattr(sys, "stdin", Stdin("y\n"))
    code, out, _ = run(capsys, "remove", "fake02")
    assert code == 0
    assert out.endswith(
        "[y/N] 노드를 지웠다: Server_2 (식별자 fake02, 호스트명 fake02)\n"
        "이 노드의 에이전트가 다시 보고하면 새 이름(Server_n)으로 다시 등록된다.\n"
    )
    assert sent[-1] == ("DELETE", f"{SERVER}/api/v1/admin/nodes/fake02")
    assert [n["key"] for n in client.get("/api/v1/admin/nodes").json()] == ["fake01"]


def test_remove_with_yes_skips_question(sent, client, report, capsys, monkeypatch):
    report(BASE + 5, node="fake02")
    report(BASE + 40)
    monkeypatch.setattr(sys, "stdin", Stdin("", tty=False))
    code, out, err = run(capsys, "remove", "-y", "fake02")
    assert (code, err) == (0, "") and out.startswith("노드를 지웠다: Server_1 (식별자 fake02")
    assert [n["key"] for n in client.get("/api/v1/admin/nodes").json()] == ["fake01"]


def test_server_address_from_option_env_or_default(monkeypatch, tmp_path, capsys):
    """설정 파일·DB 접속 정보를 읽지 않는다. 읽었다면 없는 파일이라 실패한다."""
    monkeypatch.setenv("RUNBOARD_CONFIG", str(tmp_path / "missing.yaml"))
    monkeypatch.setenv("RUNBOARD_SETTINGS", str(tmp_path / "missing.ini"))
    monkeypatch.delenv("RUNBOARD_SERVER_URL", raising=False)
    urls: list[str] = []

    def http(method: str, url: str, body=None) -> tuple[int, bytes]:
        urls.append(url)
        return 200, b"[]"

    monkeypatch.setattr(cli, "http", http)
    assert server_main.main(["nodes", "list"]) == 0
    monkeypatch.setenv("RUNBOARD_SERVER_URL", "http://10.0.3.54:55400/")
    assert server_main.main(["nodes", "list"]) == 0
    assert server_main.main(["nodes", "list", "--server", "127.0.0.1:55400"]) == 0
    assert urls == [
        "http://127.0.0.1:8000/api/v1/admin/nodes",
        "http://10.0.3.54:55400/api/v1/admin/nodes",
        "http://127.0.0.1:55400/api/v1/admin/nodes",
    ], "기본은 서버 기본 포트 8000, 스킴이 없으면 http"
    assert capsys.readouterr().out == "아직 보고한 노드가 없다.\n" * 3

    assert server_main.main(["nodes", "list", "--server", "ftp://x"]) == 1
    assert capsys.readouterr().err == "오류: 서버 주소는 http:// 로 시작한다: ftp://x\n"

    urls.clear()
    assert server_main.main(["nodes", "--server", "10.0.0.1:1", "list"]) == 0, "nodes 바로 뒤"
    assert (
        server_main.main(["nodes", "--server", "10.0.0.1:1", "list", "--server", "10.0.0.2:2"]) == 0
    )
    assert urls == [
        "http://10.0.0.1:1/api/v1/admin/nodes",
        "http://10.0.0.2:2/api/v1/admin/nodes",
    ], "동작 뒤에 적은 값이 앞의 값보다 우선한다. 동작 뒤에 적지 않으면 앞의 값을 지우지 않는다"


@pytest.mark.parametrize(
    ("address", "message"),
    [
        ("http://[::1", "서버 주소가 올바르지 않다: http://[::1 (Invalid IPv6 URL)"),
        ("http://", "서버 주소에 호스트가 없다: http://"),
        ("http://:8000/", "서버 주소에 호스트가 없다: http://:8000/"),
        (" ", "서버 주소에 호스트가 없다: http://"),
    ],
)
def test_malformed_server_address_is_an_error(address, message, monkeypatch, capsys):
    def http(method: str, url: str, body=None) -> tuple[int, bytes]:
        raise AssertionError(f"요청을 보냈다: {url}")

    monkeypatch.setattr(cli, "http", http)
    assert server_main.main(["nodes", "list", "--server", address]) == 1
    assert capsys.readouterr().err == f"오류: {message}\n"
    monkeypatch.setenv("RUNBOARD_SERVER_URL", address)
    assert server_main.main(["nodes", "list"]) == 1, "환경변수도 같다"
    assert capsys.readouterr().err == f"오류: {message}\n"


def test_unreachable_server_is_an_error(capsys):
    with socket.socket() as s:  # 열었다 닫은 포트: 아무도 듣지 않는다
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    assert server_main.main(["nodes", "list", "--server", f"http://127.0.0.1:{port}"]) == 1
    err = capsys.readouterr().err
    assert err.startswith(
        f"오류: 서버에 연결할 수 없다: http://127.0.0.1:{port}/api/v1/admin/nodes ("
    )


def test_bad_address_and_non_http_port_are_errors(capsys):
    assert server_main.main(["nodes", "list", "--server", "http://127.0.0.1:abc"]) == 1
    assert capsys.readouterr().err == (
        "오류: 서버 주소가 올바르지 않다: http://127.0.0.1:abc/api/v1/admin/nodes"
        " (nonnumeric port: 'abc')\n"
    )

    with socket.socket() as listener:  # HTTP 가 아닌 서비스 (SSH 처럼 인사말부터 보낸다)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]

        def greet() -> None:
            conn, _ = listener.accept()
            with conn:
                conn.recv(4096)
                conn.sendall(b"SSH-2.0-OpenSSH_test\r\n")

        thread = threading.Thread(target=greet)
        thread.start()
        code = server_main.main(["nodes", "list", "--server", f"127.0.0.1:{port}"])
        thread.join(5)
    assert code == 1
    assert capsys.readouterr().err.startswith(
        f"오류: RunBoard 서버의 응답이 아니다: http://127.0.0.1:{port}/api/v1/admin/nodes (BadStatusLine("
    )


@pytest.mark.parametrize(
    ("status", "body", "message"),
    [
        (200, b"<html></html>", "RunBoard 서버의 응답이 아니다 (HTTP 200): {url}"),
        (404, b"<html>Not Found</html>", "RunBoard 서버의 응답이 아니다 (HTTP 404): {url}"),
        (502, b"Bad Gateway", "서버에서 오류가 났다 (HTTP 502): {url}. 서버 로그를 본다"),
        (500, b"", "서버에서 오류가 났다 (HTTP 500): {url}. 서버 로그를 본다"),
    ],
)
def test_non_json_reply_is_an_error(status, body, message, monkeypatch, capsys):
    """RunBoard 는 4xx 도 JSON 으로 준다. JSON 이 아닌 4xx·2xx 는 다른 서버, 5xx 는 서버 안의 오류다."""
    monkeypatch.setattr(cli, "http", lambda method, url, body_=None: (status, body))
    assert server_main.main(["nodes", "list", "--server", SERVER]) == 1
    url = f"{SERVER}/api/v1/admin/nodes"
    assert capsys.readouterr().err == f"오류: {message.format(url=url)}\n"


def test_server_fault_is_reported_as_a_server_error(app, report, capsys, monkeypatch):
    """서버 안에서 처리하지 못한 예외는 Starlette 가 JSON 이 아닌 500 으로 답한다. 주소가 틀렸다고 하지 않는다."""
    report(BASE + 5)

    def db_down(key: str, display_name: str) -> None:
        raise OperationalError("UPDATE nodes", {}, ConnectionError("server closed the connection"))

    monkeypatch.setattr(app.state.repo, "rename_node", db_down)
    monkeypatch.setattr(cli, "http", route(TestClient(app, raise_server_exceptions=False)))
    assert run(capsys, "rename", "fake01", "x") == (
        1,
        "",
        f"오류: 서버에서 오류가 났다 (HTTP 500): {SERVER}/api/v1/admin/nodes/fake01. 서버 로그를 본다\n",
    )
    assert app.state.engine.state.nodes["fake01"].display_name == "Server_1"


@pytest.mark.parametrize(
    "argv",
    [["nodes"], ["nodes", "rename", "x"], ["nodes", "remove"], ["nodes", "move", "x"], ["serve"]],
)
def test_usage_errors_exit_2(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        server_main.main(argv)
    assert exc.value.code == 2


def test_without_command_serves_as_before(monkeypatch, tmp_path):
    """Dockerfile 의 CMD 그대로. 명령이 없으면 설정을 읽고 서버를 띄운다 (여기서는 띄우는 함수만 바꿔 끼운다)."""
    monkeypatch.chdir(tmp_path)  # 레포 루트의 실제 settings.ini 를 읽지 않게
    monkeypatch.delenv("RUNBOARD_CONFIG", raising=False)
    monkeypatch.delenv("RUNBOARD_SETTINGS", raising=False)
    monkeypatch.setenv("RUNBOARD_DB_URL", "postgresql+psycopg://rb@127.0.0.1:1/rb")
    started: dict = {}

    def fake_create_app(cfg):
        started["db_url"] = cfg.server.db_url
        return "app"

    monkeypatch.setattr(server_main, "create_app", fake_create_app)
    monkeypatch.setattr("uvicorn.run", lambda app, **kw: started.update(app=app, **kw))
    assert server_main.main(["--host", "0.0.0.0", "--port", "8000"]) == 0
    assert started == {
        "db_url": "postgresql+psycopg://rb@127.0.0.1:1/rb",
        "app": "app",
        "host": "0.0.0.0",
        "port": 8000,
        "workers": 1,
        "log_level": "info",
    }
