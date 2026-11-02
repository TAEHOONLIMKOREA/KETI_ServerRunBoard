"""Docker 소켓 중계: 생성 요청에만 실행 계정 라벨을 붙이고, 나머지는 바이트 그대로 넘긴다."""

import asyncio
import json
import logging
import os
import re
import socket
import sys
import uuid
from pathlib import Path

import pytest

from runboard_agent import dockerproxy
from runboard_agent.dockerproxy import Denied, DockerProxy, Launcher, peer_launcher, serve, stamp

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="SO_PEERCRED 와 유닉스 소켓 서버는 Linux 전용"
)

ALICE = Launcher(uid=1234, name="alice", pid=1)
ROOT = Launcher(uid=0, name="root", pid=1)
REAL_DIR = "/run/runboard-docker"


def test_stamp_overrides_declared_owner_and_marks_launcher():
    body = {"Image": "x", "Labels": {"runboard.owner": "bob", "keep": "1"}}
    assert stamp(body, ALICE, real_dir=REAL_DIR)["Labels"] == {
        "runboard.owner": "alice",
        "runboard.launcher": "alice",
        "keep": "1",
    }
    assert stamp({"Labels": None}, ALICE, real_dir=REAL_DIR)["Labels"]["runboard.owner"] == "alice"


def test_root_launcher_removes_owner_but_keeps_mark():
    body = stamp({"Labels": {"runboard.owner": "bob"}}, ROOT, real_dir=REAL_DIR)
    assert body["Labels"] == {"runboard.launcher": "root"}, (
        "root 는 사람이 아니다. 이름으로 짐작하지도 않게 표시만"
    )


def test_mounting_real_socket_dir_is_denied(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    for host in (
        {"Binds": [f"{real}/docker.sock:/var/run/docker.sock"]},
        {"Mounts": [{"Type": "bind", "Source": str(link), "Target": "/x"}]},
    ):
        with pytest.raises(Denied, match="마운트할 수 없다"):
            stamp({"HostConfig": host}, ALICE, real_dir=str(real))
    allowed = {
        "Binds": ["data:/data", f"{tmp_path}:/work", "/var/run/docker.sock:/var/run/docker.sock"]
    }
    assert (
        stamp({"HostConfig": allowed}, ALICE, real_dir=str(real))["Labels"]["runboard.owner"]
        == "alice"
    )


def test_peer_launcher_reads_login_account_of_peer():
    a, b = socket.socketpair(socket.AF_UNIX)
    with a, b:
        who = peer_launcher(a)
    try:
        login = int(Path("/proc/self/loginuid").read_text())
    except OSError:
        login = 4294967295
    assert who.pid == os.getpid()
    assert who.uid == (os.getuid() if login == 4294967295 else login), "sudo 를 거쳐도 로그인 계정"


# ---------------------------------------------------------------------- 가짜 dockerd 를 둔 연결 시험


async def _fake_docker(reader, writer, seen):
    """받은 요청을 적어 두고 200 ok 로 답한다. Upgrade 요청은 101 뒤에 받은 바이트를 대문자로 돌려준다."""
    try:
        while True:
            try:
                head = (await reader.readuntil(b"\r\n\r\n")).decode()
            except asyncio.IncompleteReadError:
                break
            body = b""
            if "transfer-encoding: chunked" in head.lower():
                while size := int((await reader.readline()).strip(), 16):
                    body += (await reader.readexactly(size + 2))[:-2]
                await reader.readexactly(2)
            elif m := re.search(r"content-length: (\d+)", head, re.I):
                body = await reader.readexactly(int(m[1]))
            seen.append((head, body))
            if "upgrade:" in head.lower():
                writer.write(
                    b"HTTP/1.1 101 UPGRADED\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
                )
                await writer.drain()
                while data := await reader.read(1024):
                    writer.write(data.upper())
                    await writer.drain()
                break
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
            await writer.drain()
    finally:
        writer.close()  # 시험이 끝나 남은 작업이 취소돼도 연결을 닫는다


async def _response(reader):
    head = (await reader.readuntil(b"\r\n\r\n")).decode()
    m = re.search(r"content-length: (\d+)", head, re.I)
    return head, (await reader.readexactly(int(m[1])) if m else b"")


def _with_proxy(scenario, *, launcher=ALICE, docker_up=True):
    """추상 유닉스 소켓(경로 길이 제한 없음)에 가짜 dockerd 와 중계를 띄우고 scenario(reader, writer) 를 돌린다."""
    seen: list = []

    async def main():
        up, front = (f"\0runboard-test-{uuid.uuid4().hex}" for _ in range(2))
        servers = []
        if docker_up:
            servers.append(
                await asyncio.start_unix_server(lambda r, w: _fake_docker(r, w, seen), path=up)
            )
        proxy = DockerProxy(up, identify=lambda _sock: launcher, real_dir=REAL_DIR)
        servers.append(await asyncio.start_unix_server(proxy.handle, path=front))
        try:
            reader, writer = await asyncio.open_unix_connection(front)
            result = await scenario(reader, writer)
            writer.close()
            return result
        finally:
            for server in servers:
                server.close()

    return asyncio.run(asyncio.wait_for(main(), 5)), seen


def _create(body: dict, extra: str = "") -> bytes:
    data = json.dumps(body).encode()
    return (
        b"POST /v1.47/containers/create?name=job1 HTTP/1.1\r\nHost: docker\r\n"
        + extra.encode()
        + f"Content-Type: application/json\r\nContent-Length: {len(data)}\r\n\r\n".encode()
        + data
    )


def test_create_is_stamped_and_other_requests_pass_on_same_connection():
    async def scenario(reader, writer):
        writer.write(b"GET /_ping HTTP/1.1\r\nHost: docker\r\n\r\n")
        first = await _response(reader)
        writer.write(_create({"Image": "x", "Labels": {"runboard.owner": "bob"}}))
        return first, await _response(reader)

    (first, second), seen = _with_proxy(scenario)
    assert first[1] == b"ok" and second[1] == b"ok"
    assert seen[0][0].startswith("GET /_ping HTTP/1.1\r\n"), "생성이 아닌 요청은 그대로"
    head, body = seen[1]
    assert json.loads(body)["Labels"] == {"runboard.owner": "alice", "runboard.launcher": "alice"}
    assert f"Content-Length: {len(body)}" in head, "길이를 새 본문에 맞춘다"


def test_chunked_create_body_is_rewritten_with_length():
    data = json.dumps({"Image": "x"}).encode()

    async def scenario(reader, writer):
        writer.write(
            b"POST /containers/create HTTP/1.1\r\nHost: docker\r\nTransfer-Encoding: chunked\r\n\r\n"
            + f"{5:x}\r\n".encode()
            + data[:5]
            + b"\r\n"
            + f"{len(data) - 5:x}\r\n".encode()
            + data[5:]
            + b"\r\n0\r\n\r\n"
        )
        return await _response(reader)

    _, seen = _with_proxy(scenario)
    head, body = seen[0]
    assert "chunked" not in head.lower() and json.loads(body)["Labels"]["runboard.owner"] == "alice"


def test_non_create_chunked_body_passes_unchanged():
    payload = os.urandom(3000)

    async def scenario(reader, writer):
        writer.write(
            b"POST /v1.47/build HTTP/1.1\r\nHost: docker\r\nTransfer-Encoding: chunked\r\n\r\n"
        )
        for part in (payload[:1000], payload[1000:]):
            writer.write(f"{len(part):x}\r\n".encode() + part + b"\r\n")
        writer.write(b"0\r\n\r\n")
        return await _response(reader)

    _, seen = _with_proxy(scenario)
    assert seen[0][1] == payload and "transfer-encoding: chunked" in seen[0][0].lower()


def test_upgrade_switches_to_raw_stream():
    async def scenario(reader, writer):
        body = b'{"Detach":false,"Tty":true}'
        writer.write(
            b"POST /v1.47/exec/abc/start HTTP/1.1\r\nHost: docker\r\nConnection: Upgrade\r\nUpgrade: tcp\r\n"
            + f"Content-Length: {len(body)}\r\n\r\n".encode()
            + body
        )
        head = await reader.readuntil(b"\r\n\r\n")
        writer.write(b"ls -al\n")
        return head, await reader.readexactly(7)

    (head, echoed), _ = _with_proxy(scenario)
    assert head.startswith(b"HTTP/1.1 101") and echoed == b"LS -AL\n", (
        "exec -it 같은 스트림은 날것 그대로"
    )


def test_denied_mount_never_reaches_docker():
    async def scenario(reader, writer):
        writer.write(
            _create({"Image": "x", "HostConfig": {"Binds": [f"{REAL_DIR}/docker.sock:/s"]}})
        )
        return await _response(reader)

    (head, body), seen = _with_proxy(scenario)
    assert head.startswith("HTTP/1.1 403") and "마운트할 수 없다" in json.loads(body)["message"]
    assert seen == []


def test_docker_down_gives_readable_error():
    async def scenario(reader, writer):
        writer.write(b"GET /_ping HTTP/1.1\r\nHost: docker\r\n\r\n")
        return await _response(reader)

    (head, body), _ = _with_proxy(scenario, docker_up=False)
    assert head.startswith("HTTP/1.1 502") and "연결할 수 없다" in json.loads(body)["message"]


def test_expect_continue_on_create_is_answered_by_proxy():
    data = json.dumps({"Image": "x"}).encode()

    async def scenario(reader, writer):
        writer.write(
            b"POST /containers/create HTTP/1.1\r\nHost: docker\r\nExpect: 100-continue\r\n"
            + f"Content-Length: {len(data)}\r\n\r\n".encode()
        )
        cont = await reader.readuntil(b"\r\n\r\n")
        writer.write(data)
        return cont, await _response(reader)

    (cont, (_, body)), seen = _with_proxy(scenario)
    assert cont.startswith(b"HTTP/1.1 100") and body == b"ok"
    assert "expect" not in seen[0][0].lower(), "dockerd 에는 본문까지 한 번에 넘긴다"


# ---------------------------------------------------------------------- 멈출 때 (serve)

_ATTACH = (
    b"POST /v1.47/containers/abc/attach?stream=1&stdin=1&stdout=1 HTTP/1.1\r\nHost: docker\r\n"
    b"Connection: Upgrade\r\nUpgrade: tcp\r\n\r\n"
)


async def _connect(path):
    """serve() 가 소켓을 열 때까지 다시 붙어 본다."""
    for _ in range(100):
        try:
            return await asyncio.open_unix_connection(path)
        except ConnectionRefusedError:
            await asyncio.sleep(0.01)
    raise AssertionError("중계가 소켓을 열지 않았다")


def _stopping(monkeypatch, docker, scenario, *, grace):
    """가짜 dockerd(docker) 앞에 serve() 로 중계를 띄우고 scenario(front, stop, serving) 를 돌린다."""
    # 추상 유닉스 소켓에는 파일이 없어 권한을 바꿀 수 없다
    monkeypatch.setattr(dockerproxy, "_share_with_docker_group", lambda _path: None)

    async def main():
        up, front = (f"\0runboard-test-{uuid.uuid4().hex}" for _ in range(2))
        upstream = await asyncio.start_unix_server(docker, path=up)
        proxy = DockerProxy(up, identify=lambda _sock: ALICE, real_dir=REAL_DIR)
        stop = asyncio.Event()
        serving = asyncio.create_task(serve(proxy, listen=front, stop=stop, grace=grace))
        try:
            return await scenario(front, stop, serving)
        finally:
            serving.cancel()
            upstream.close()

    return asyncio.run(asyncio.wait_for(main(), 5))


def test_stop_closes_open_stream_after_grace(monkeypatch, caplog):
    """attach·exec -it·logs -f 처럼 끝나지 않는 연결은 grace 만큼 기다린 뒤 양쪽 다 끊는다.

    Python 3.12.1 부터 서버를 닫으면 받은 연결이 모두 닫힐 때까지 기다린다. 끊지 않으면 중계가 끝나지 않는다.
    그보다 낮은 버전에서는 serve() 가 어차피 바로 돌아오므로, 양쪽 연결이 정말 닫혔는지를 본다.
    """
    upstream_closed = asyncio.Event()

    async def docker(reader, writer):
        # 101 뒤로는 받은 것을 돌려줄 뿐 먼저 닫지 않는다. 끝나면 중계가 dockerd 쪽 연결을 닫은 것이다
        try:
            await _fake_docker(reader, writer, [])
        finally:
            upstream_closed.set()

    async def scenario(front, stop, serving):
        reader, writer = await _connect(front)
        writer.write(_ATTACH)
        assert (await reader.readuntil(b"\r\n\r\n")).startswith(b"HTTP/1.1 101")
        writer.write(b"ls\n")
        assert await reader.readexactly(3) == b"LS\n"
        stop.set()
        await asyncio.wait_for(serving, 2)
        eof = await asyncio.wait_for(reader.read(), 1)
        await asyncio.wait_for(upstream_closed.wait(), 1)
        writer.close()
        return eof

    caplog.set_level(logging.INFO, logger=dockerproxy.__name__)
    assert _stopping(monkeypatch, docker, scenario, grace=0.2) == b"", "클라이언트도 끊긴 것을 본다"
    assert "끝나지 않은 연결 1개를 끊었다" in caplog.text


def test_stop_lets_request_in_flight_finish(monkeypatch, caplog):
    """멈추면 새 연결은 받지 않는다. 처리 중이던 짧은 요청은 끝까지 넘기고, 끝나면 grace 를 다 기다리지 않는다."""
    arrived, release = asyncio.Event(), asyncio.Event()

    async def docker(reader, writer):
        await reader.readuntil(b"\r\n\r\n")
        arrived.set()
        await release.wait()  # 중계가 멈추기 시작한 뒤에 답한다
        writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok")
        await writer.drain()
        await reader.read()  # 클라이언트가 닫으면 중계가 EOF 를 넘긴다
        writer.close()

    async def scenario(front, stop, serving):
        reader, writer = await _connect(front)
        writer.write(b"GET /v1.47/containers/json HTTP/1.1\r\nHost: docker\r\n\r\n")
        await arrived.wait()
        stop.set()
        await asyncio.sleep(0.05)
        with pytest.raises(ConnectionRefusedError):
            await asyncio.open_unix_connection(front)
        release.set()
        response = await _response(reader)
        writer.close()
        await asyncio.wait_for(serving, 1)  # grace 는 5초
        return response

    caplog.set_level(logging.INFO, logger=dockerproxy.__name__)
    head, body = _stopping(monkeypatch, docker, scenario, grace=5)
    assert head.startswith("HTTP/1.1 200") and body == b"ok"
    assert "Docker 소켓 중계 종료" in caplog.text and "끊었다" not in caplog.text


@pytest.mark.parametrize("stop_first", [False, True], ids=["accept-then-stop", "stop-then-accept"])
def test_request_accepted_right_at_stop_gets_the_grace(monkeypatch, caplog, stop_first):
    """멈추라는 신호를 처리하는 바로 그 루프 바퀴에 받은 연결도 grace 안에 끝까지 넘긴다.

    asyncio 는 연결을 받은(accept) 뒤 몇 바퀴 지나서야 handle 을 띄운다. 그 사이에 서버를 닫으면 asyncio 가 연결을
    버리고(stop-then-accept), 열린 연결을 그 전에 세어 두면 grace 없이 끊긴다(accept-then-stop).
    """
    ok = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nok"

    async def docker(reader, writer):
        try:
            await reader.readuntil(b"\r\n\r\n")
            await asyncio.sleep(0.1)  # 끊긴다면 답하기 전에 끊긴다
            writer.write(ok)
            await writer.drain()
            await reader.read()
        except asyncio.IncompleteReadError:
            pass  # 요청 없이 닫은 연결 (중계가 떴는지 본 연결)
        finally:
            writer.close()

    async def main():
        loop = asyncio.get_running_loop()
        up, front = (f"\0runboard-test-{uuid.uuid4().hex}" for _ in range(2))
        upstream = await asyncio.start_unix_server(docker, path=up)
        proxy = DockerProxy(up, identify=lambda _sock: ALICE, real_dir=REAL_DIR)
        stop = asyncio.Event()
        serving = asyncio.create_task(serve(proxy, listen=front, stop=stop, grace=5))
        try:
            probe_reader, probe = await _connect(front)
            probe.write_eof()  # 요청 없이 끝낸다. 중계가 이 연결을 마치고 닫으면 EOF 를 읽는다
            assert await probe_reader.read() == b""
            while proxy._connections:  # 붙어 본 연결까지 끝나 열린 연결이 하나도 없을 때 멈춘다
                await asyncio.sleep(0.01)
            probe.close()
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(front)  # 커널 대기열에 들어가고, 중계는 다음 바퀴에 받는다
            client.setblocking(False)
            client.send(b"GET /v1.47/containers/json HTTP/1.1\r\nHost: docker\r\n\r\n")
            if stop_first:
                loop.call_soon(stop.set)  # 다음 바퀴에 연결을 받기 전에 멈춘다
            else:
                # 다음 바퀴에 연결을 받은 뒤 멈춘다 (때가 된 예약 호출은 입출력 다음에 돈다)
                loop.call_at(loop.time(), stop.set)
            got = b""
            try:
                while len(got) < len(ok) and (
                    chunk := await asyncio.wait_for(loop.sock_recv(client, 4096), 2)
                ):
                    got += chunk
            except (ConnectionResetError, asyncio.TimeoutError) as exc:
                got += f"<{type(exc).__name__}>".encode()  # 버린 연결: 끊기거나 아무 답이 없다
            finally:
                client.close()  # docker CLI 처럼 응답을 다 읽고 닫는다
            await asyncio.wait_for(serving, 2)  # grace 는 5초. 다 넘겼으면 기다리지 않는다
            return got
        finally:
            serving.cancel()
            upstream.close()

    monkeypatch.setattr(dockerproxy, "_share_with_docker_group", lambda _path: None)
    caplog.set_level(logging.INFO, logger=dockerproxy.__name__)
    assert asyncio.run(asyncio.wait_for(main(), 5)) == ok
    assert "Docker 소켓 중계 종료" in caplog.text and "끊었다" not in caplog.text


def test_stop_leaves_systemd_socket_to_next_relay(monkeypatch, tmp_path):
    """systemd 소켓으로 돌면, 중계를 다시 띄우는 사이에 온 docker 명령은 커널 대기열에 있다가 다음 중계가 받는다.

    리슨 소켓은 systemd 가 계속 쥐고 있으므로 소켓 파일(/run/docker.sock)을 지우면 안 된다. 지우면 그 뒤 docker 명령은
    소켓을 찾지 못한다. Python 3.13 부터 asyncio 는 서버를 닫을 때 소켓 파일을 지운다(cleanup_socket).
    """
    monkeypatch.chdir(tmp_path)  # 유닉스 소켓 경로는 108바이트까지라 짧은 상대 경로로 연다
    path = "docker.sock"
    held = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)  # systemd 가 쥔 리슨 소켓
    held.bind(path)
    held.listen()
    # systemd 처럼 중계를 띄울 때마다 같은 리슨 소켓을 넘긴다
    monkeypatch.setattr(
        dockerproxy, "_activated_socket", lambda: socket.socket(fileno=os.dup(held.fileno()))
    )

    async def main():
        up = f"\0runboard-test-{uuid.uuid4().hex}"
        upstream = await asyncio.start_unix_server(lambda r, w: _fake_docker(r, w, []), path=up)

        def relay():
            proxy = DockerProxy(up, identify=lambda _sock: ALICE, real_dir=REAL_DIR)
            stop = asyncio.Event()
            return stop, asyncio.create_task(serve(proxy, stop=stop, grace=0.2))

        try:
            stop, serving = relay()
            reader, writer = await asyncio.open_unix_connection(path)
            writer.write(b"GET /_ping HTTP/1.1\r\nHost: docker\r\n\r\n")
            assert (await _response(reader))[1] == b"ok"
            writer.close()
            stop.set()
            await asyncio.wait_for(serving, 2)
            assert os.path.exists(path), "소켓 파일이 남아야 다음 docker 명령이 붙는다"

            reader, writer = await asyncio.open_unix_connection(path)  # 중계가 없는 사이에 온 명령
            writer.write(b"GET /_ping HTTP/1.1\r\nHost: docker\r\n\r\n")
            stop, serving = relay()
            response = await asyncio.wait_for(_response(reader), 2)
            writer.close()
            stop.set()
            await asyncio.wait_for(serving, 2)
            return response
        finally:
            upstream.close()
            held.close()

    head, body = asyncio.run(asyncio.wait_for(main(), 5))
    assert head.startswith("HTTP/1.1 200") and body == b"ok", "다음 중계가 대기열의 명령을 받았다"


def test_connection_starting_while_cutting_is_closed_at_once():
    """남은 연결을 끊기 시작한 뒤에야 시작한 연결은 dockerd 에 잇지 않고 바로 끊는다 (serve 에서는 생기지 않게 막는다)."""
    upstream_connections = 0

    async def docker(reader, writer):
        nonlocal upstream_connections
        upstream_connections += 1
        await _fake_docker(reader, writer, [])

    async def main():
        up, front = (f"\0runboard-test-{uuid.uuid4().hex}" for _ in range(2))
        upstream = await asyncio.start_unix_server(docker, path=up)
        proxy = DockerProxy(up, identify=lambda _sock: ALICE, real_dir=REAL_DIR)
        relay = await asyncio.start_unix_server(proxy.handle, path=front)
        try:
            assert await proxy.close_connections(0) == 0
            reader, writer = await asyncio.open_unix_connection(front)
            eof = await asyncio.wait_for(reader.read(), 1)
            writer.close()
            return eof
        finally:
            relay.close()
            upstream.close()

    assert asyncio.run(asyncio.wait_for(main(), 5)) == b""
    assert upstream_connections == 0


def test_cut_leaves_side_that_already_finished_closing():
    """handle 이 dockerd 쪽을 먼저 닫고 클라이언트 쪽이 다 나가기를 기다리는 사이, dockerd 쪽은 남은 데이터를 다 보내고
    닫힐 수 있다. asyncio 는 그렇게 닫힌 transport 에 abort() 하면 AttributeError 를 낸다. 끊을 때 그런 쪽은 그대로 둔다.
    """

    async def late_reader(reader, writer):
        try:
            await asyncio.sleep(0.1)  # 늦게 읽어 상대가 버퍼에 쌓게 한다
            while await reader.read(65536):
                pass
        finally:
            writer.close()

    async def main():
        path = f"\0runboard-test-{uuid.uuid4().hex}"
        server = await asyncio.start_unix_server(late_reader, path=path)
        try:
            _, upstream = await asyncio.open_unix_connection(path)
            upstream.write(b"x" * (4 * 1024 * 1024))
            upstream.close()
            await upstream.wait_closed()  # 남은 데이터를 다 보내고 닫혔다
            _, client = await asyncio.open_unix_connection(path)
            dockerproxy._Connection(asyncio.current_task(), client, upstream).abort()
            await client.wait_closed()  # 열린 쪽은 끊었다
        finally:
            server.close()

    asyncio.run(asyncio.wait_for(main(), 5))
