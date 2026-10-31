"""Docker 소켓 중계. 새 컨테이너에 실행한 계정을 라벨로 붙인다 (Linux 전용, root 로 실행).

dockerd 의 진짜 소켓은 root 만 여는 자리(/run/runboard-docker/docker.sock)로 옮기고, 이 중계가 원래 자리
(/run/docker.sock)에서 같은 권한(root:docker 0660)으로 요청을 받아 그대로 넘긴다. 사용자는 Docker 를 그대로 쓴다.
설치·되돌리기는 deploy/start-agent.sh 가 한다.

고치는 요청은 컨테이너 생성(POST /containers/create) 하나다.
- 실행 계정: 접속한 프로세스의 로그인 계정(/proc/<pid>/loginuid). sudo 를 거쳐도 바뀌지 않는다.
  로그인 기록이 없으면(서비스가 띄운 경우) 그 프로세스의 uid.
- runboard.launcher 에 실행 계정을 적는다. 이 라벨이 있으면 서버는 컨테이너 이름으로 소유자를 짐작하지 않는다.
- runboard.owner 에도 같은 값을 적는다. 사용자가 적은 값은 덮어쓴다. 실행 계정이 root 면 사람이 아니므로 지운다.
- 진짜 소켓 폴더를 컨테이너에 마운트하는 요청은 거부한다. 중계를 건너뛰는 가장 쉬운 길이라서다.
나머지 요청과 attach·exec·BuildKit 같은 스트림 연결은 바이트 그대로 넘긴다. 응답은 해석하지 않는다.

docker 권한은 사실상 root 권한이라 작정한 우회(/ 마운트 등)까지 막지는 못한다. 평소 사용을 정확히 남기는 것이 목적이다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import signal
import socket
import struct
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from runboard_common.schemas import LAUNCHER_LABEL

log = logging.getLogger(__name__)

REAL_SOCKET = "/run/runboard-docker/docker.sock"
OWNER_LABEL = "runboard.owner"
UNSET_LOGINUID = 4294967295  # (uid_t)-1: 로그인 세션 밖에서 시작된 프로세스
SD_LISTEN_FDS_START = 3
MAX_HEAD = 64 * 1024
MAX_CREATE_BODY = 8 * 1024 * 1024
CHUNK = 64 * 1024

_CREATE = re.compile(r"^(?:/v[\d.]+)?/containers/create$")
# Upgrade 헤더 없이 연결을 넘겨받는(hijack) 예전 클라이언트도 있어 경로로도 본다
_HIJACK = re.compile(
    r"^(?:/v[\d.]+)?/(?:containers/[^/]+/attach(?:/ws)?|exec/[^/]+/start|session|grpc)$"
)
_REASONS = {400: "Bad Request", 403: "Forbidden", 413: "Payload Too Large", 502: "Bad Gateway"}


@dataclass(frozen=True)
class Launcher:
    """컨테이너를 만든 계정. uid 0 이면 사람으로 보지 않는다."""

    uid: int
    name: str
    pid: int


class Denied(Exception):
    """중계가 거부한 요청. 메시지는 docker CLI 에 'Error response from daemon: ...' 으로 보인다."""

    def __init__(self, message: str, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


def peer_launcher(sock: socket.socket) -> Launcher:
    """유닉스 소켓 건너편 프로세스의 실행 계정."""
    creds = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("iII"))
    pid, uid, _gid = struct.unpack("iII", creds)
    login = _loginuid(pid)
    account = uid if login is None else login
    return Launcher(uid=account, name=_account_name(account), pid=pid)


def _loginuid(pid: int) -> int | None:
    try:
        value = int(Path(f"/proc/{pid}/loginuid").read_text().strip())
    except (OSError, ValueError):
        return None
    return None if value == UNSET_LOGINUID else value


def _account_name(uid: int) -> str:
    import pwd  # Linux 전용 모듈이라 쓸 때 불러온다 (Windows 개발 환경에서도 import 는 되게)

    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return f"uid:{uid}"


def stamp(body: dict, launcher: Launcher, *, real_dir: str, owner_label: str = OWNER_LABEL) -> dict:
    """컨테이너 생성 요청 본문에 실행 계정 라벨을 붙인다. 진짜 소켓 폴더를 마운트하면 Denied."""
    for source in _bind_sources(body):
        if _within(source, real_dir):
            raise Denied(
                f"runboard: Docker 진짜 소켓 폴더({real_dir})는 컨테이너에 마운트할 수 없다"
            )
    labels = dict(body.get("Labels") or {})
    labels[LAUNCHER_LABEL] = launcher.name
    if launcher.uid == 0:
        labels.pop(owner_label, None)
    else:
        labels[owner_label] = launcher.name
    body["Labels"] = labels
    return body


def _bind_sources(body: dict) -> Iterator[str]:
    host = body.get("HostConfig") or {}
    for bind in host.get("Binds") or []:
        source = str(bind).split(":", 1)[0]
        if source.startswith("/"):  # 아니면 이름 있는 볼륨
            yield source
    for mount in host.get("Mounts") or []:
        if isinstance(mount, dict) and mount.get("Type") == "bind" and mount.get("Source"):
            yield str(mount["Source"])


def _within(path: str, directory: str) -> bool:
    real, base = os.path.realpath(path), os.path.realpath(directory)
    return real == base or real.startswith(base.rstrip("/") + "/")


# ---------------------------------------------------------------------- HTTP/1.1 요청 흐름


@dataclass
class _Head:
    raw: bytes
    method: str
    target: str
    path: str
    headers: dict[str, str]  # 소문자 키

    @property
    def upgrade(self) -> bool:
        connection = self.headers.get("connection", "").lower()
        return (
            "upgrade" in connection or "upgrade" in self.headers or bool(_HIJACK.match(self.path))
        )

    @property
    def chunked(self) -> bool:
        return "chunked" in self.headers.get("transfer-encoding", "").lower()


def _parse_head(raw: bytes) -> _Head:
    lines = raw.decode("latin-1").split("\r\n")
    method, target, _version = lines[0].split(" ", 2)
    headers: dict[str, str] = {}
    for line in lines[1:]:
        if not line:
            continue
        name, _, value = line.partition(":")
        key = name.strip().lower()
        headers[key] = f"{headers[key]}, {value.strip()}" if key in headers else value.strip()
    return _Head(raw, method.upper(), target, urlsplit(target).path or target, headers)


def _with_length(head: _Head, length: int) -> bytes:
    """본문을 바꾼 요청 머리. 길이 관련 헤더와 Expect 를 빼고 Content-Length 를 새로 단다."""
    lines = head.raw.decode("latin-1").split("\r\n")
    drop = ("content-length", "transfer-encoding", "expect")
    kept = [
        lines[0],
        *(x for x in lines[1:] if x and x.split(":", 1)[0].strip().lower() not in drop),
    ]
    kept.append(f"Content-Length: {length}")
    return ("\r\n".join(kept) + "\r\n\r\n").encode("latin-1")


async def _read_body(reader: asyncio.StreamReader, head: _Head, limit: int) -> bytes:
    if head.chunked:
        parts: list[bytes] = []
        total = 0
        while True:
            size = int((await reader.readline()).split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                while (await reader.readline()) not in (b"\r\n", b"\n", b""):
                    pass  # 트레일러
                return b"".join(parts)
            total += size
            if total > limit:
                raise Denied("runboard: 컨테이너 생성 요청이 너무 크다", 413)
            parts.append(await reader.readexactly(size))
            await reader.readexactly(2)
    length = int(head.headers.get("content-length") or 0)
    if length > limit:
        raise Denied("runboard: 컨테이너 생성 요청이 너무 크다", 413)
    return await reader.readexactly(length) if length else b""


async def _copy_body(
    reader: asyncio.StreamReader, writer: asyncio.StreamWriter, head: _Head
) -> None:
    """본문을 고치지 않고 그대로 흘려보낸다. 다음 요청의 시작을 알려고 끝만 찾는다."""
    if head.chunked:
        while True:
            line = await reader.readline()
            if not line:
                raise asyncio.IncompleteReadError(line, None)
            writer.write(line)
            size = int(line.split(b";", 1)[0].strip() or b"0", 16)
            if size == 0:
                while True:
                    trailer = await reader.readline()
                    writer.write(trailer)
                    if trailer in (b"\r\n", b"\n", b""):
                        return
            await _copy_exact(reader, writer, size + 2)
    await _copy_exact(reader, writer, int(head.headers.get("content-length") or 0))


async def _copy_exact(reader: asyncio.StreamReader, writer: asyncio.StreamWriter, n: int) -> None:
    while n > 0:
        data = await reader.read(min(n, CHUNK))
        if not data:
            raise asyncio.IncompleteReadError(b"", n)
        writer.write(data)
        await writer.drain()
        n -= len(data)


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while data := await reader.read(CHUNK):
            writer.write(data)
            await writer.drain()
    except OSError:  # 상대가 끊었다
        pass


async def _reply(writer: asyncio.StreamWriter, status: int, message: str) -> None:
    body = json.dumps({"message": message}, ensure_ascii=False).encode()
    head = (
        f"HTTP/1.1 {status} {_REASONS.get(status, 'Error')}\r\n"
        f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\nConnection: close\r\n\r\n"
    )
    try:
        writer.write(head.encode() + body)
        await writer.drain()
    except OSError:
        pass


def _write_eof(writer: asyncio.StreamWriter) -> None:
    try:
        if writer.can_write_eof():
            writer.write_eof()
    except OSError:
        pass


class DockerProxy:
    def __init__(
        self,
        upstream: str = REAL_SOCKET,
        *,
        owner_label: str = OWNER_LABEL,
        identify: Callable[[socket.socket], Launcher] = peer_launcher,
        real_dir: str | None = None,
    ) -> None:
        self.upstream = upstream
        self.owner_label = owner_label
        self.identify = identify
        self.real_dir = real_dir or os.path.dirname(upstream)

    async def handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            launcher = self.identify(writer.get_extra_info("socket"))
            try:
                up_reader, up_writer = await asyncio.open_unix_connection(
                    self.upstream, limit=MAX_HEAD
                )
            except OSError as exc:
                await _reply(
                    writer, 502, f"runboard: Docker 데몬({self.upstream})에 연결할 수 없다: {exc}"
                )
                return
            responses = asyncio.create_task(_pipe(up_reader, writer))
            requests = asyncio.create_task(self._forward(reader, up_writer, writer, launcher))
            done, _ = await asyncio.wait({responses, requests}, return_when=asyncio.FIRST_COMPLETED)
            if requests in done:
                # 클라이언트가 보내기를 끝냈다. dockerd 에 알리고 남은 응답(attach 출력 등)을 끝까지 넘긴다
                _write_eof(up_writer)
                await responses
            else:
                requests.cancel()
            up_writer.close()
        except Exception:
            log.exception("연결 처리 실패")
        finally:
            writer.close()

    async def _forward(
        self,
        reader: asyncio.StreamReader,
        up: asyncio.StreamWriter,
        client: asyncio.StreamWriter,
        launcher: Launcher,
    ) -> None:
        try:
            while True:
                try:
                    raw = await reader.readuntil(b"\r\n\r\n")
                except asyncio.IncompleteReadError:
                    return  # 클라이언트가 닫았다
                except asyncio.LimitOverrunError:
                    await _reply(client, 400, "runboard: 요청 머리가 너무 크다")
                    return
                try:
                    head = _parse_head(raw)
                except ValueError:
                    await _reply(client, 400, "runboard: 요청을 읽을 수 없다")
                    return
                if head.method == "POST" and _CREATE.match(head.path):
                    try:
                        up.write(await self._stamped(reader, client, head, launcher))
                    except Denied as exc:
                        log.warning(
                            "거부: %s (uid %d, pid %d) %s",
                            launcher.name,
                            launcher.uid,
                            launcher.pid,
                            exc,
                        )
                        await _reply(client, exc.status, str(exc))
                        return
                else:
                    up.write(head.raw)
                    await _copy_body(reader, up, head)
                await up.drain()
                if head.upgrade:
                    await _pipe(reader, up)  # attach·exec·BuildKit: 이후는 날것 그대로
                    return
        except (asyncio.IncompleteReadError, OSError):
            return

    async def _stamped(
        self,
        reader: asyncio.StreamReader,
        client: asyncio.StreamWriter,
        head: _Head,
        launcher: Launcher,
    ) -> bytes:
        if "100-continue" in head.headers.get("expect", "").lower():
            client.write(b"HTTP/1.1 100 Continue\r\n\r\n")  # 본문을 먼저 받아야 고칠 수 있다
            await client.drain()
        body = await _read_body(reader, head, MAX_CREATE_BODY)
        try:
            data = json.loads(body or b"{}")
        except ValueError:
            data = None
        if not isinstance(data, dict):
            return _with_length(head, len(body)) + body  # 이상한 본문은 dockerd 가 판단하게 그대로
        stamp(data, launcher, real_dir=self.real_dir, owner_label=self.owner_label)
        name = parse_qs(urlsplit(head.target).query).get("name", ["(이름 없음)"])[0]
        log.info(
            "컨테이너 생성: %s ← %s (uid %d, pid %d)%s",
            name,
            launcher.name,
            launcher.uid,
            launcher.pid,
            "" if launcher.uid else ", root 라 소유자 라벨 없음",
        )
        new = json.dumps(data).encode()
        return _with_length(head, len(new)) + new


# ---------------------------------------------------------------------- 실행


def _activated_socket() -> socket.socket | None:
    """systemd 소켓 활성화로 받은 리슨 소켓 (sd_listen_fds 와 같은 규칙)."""
    if (
        os.environ.get("LISTEN_PID") != str(os.getpid())
        or int(os.environ.get("LISTEN_FDS") or 0) < 1
    ):
        return None
    for key in ("LISTEN_PID", "LISTEN_FDS", "LISTEN_FDNAMES"):
        os.environ.pop(key, None)
    return socket.socket(fileno=SD_LISTEN_FDS_START)


def _share_with_docker_group(path: str) -> None:
    """직접 연 소켓도 원래 Docker 소켓처럼 root:docker 0660 으로 둔다."""
    import grp  # Linux 전용

    os.chmod(path, 0o660)
    if os.geteuid() == 0:
        try:
            os.chown(path, 0, grp.getgrnam("docker").gr_gid)
        except KeyError:
            log.warning("docker 그룹이 없어 %s 는 root 만 쓸 수 있다", path)


async def serve(proxy: DockerProxy, *, listen: str | None = None) -> None:
    sock = _activated_socket()
    if sock is not None:
        server = await asyncio.start_unix_server(proxy.handle, sock=sock, limit=MAX_HEAD)
        where = "systemd 소켓"
    elif listen:
        server = await asyncio.start_unix_server(proxy.handle, path=listen, limit=MAX_HEAD)
        _share_with_docker_group(listen)
        where = listen
    else:
        raise ValueError("--listen 이 없고 systemd 소켓도 받지 못했다")
    log.info(
        "Docker 소켓 중계 시작: %s → %s (라벨 %s, %s)",
        where,
        proxy.upstream,
        proxy.owner_label,
        LAUNCHER_LABEL,
    )
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    async with server:
        await stop.wait()
    log.info("Docker 소켓 중계 종료")
