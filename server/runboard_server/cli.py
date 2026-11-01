"""runboard-server nodes: 돌고 있는 서버의 관리 API 로 노드를 보고 고친다.

    runboard-server nodes list                       [--server URL]
    runboard-server nodes rename <노드> <화면 이름>  [--server URL]
    runboard-server nodes remove <노드> [--yes]      [--server URL]

<노드> 는 식별자·화면 이름·호스트명 중 하나로, 노드 하나에만 맞아야 한다. 서버 주소는 --server (nodes 바로
뒤에 적어도 된다), 없으면 환경변수 RUNBOARD_SERVER_URL, 그것도 없으면 http://127.0.0.1:8000 (서버 기본
포트라 서버 컨테이너 안에서는 그대로 된다).
runboard.yaml·settings.ini 를 읽지 않고 DB 접속 정보도 필요 없다. 표준 라이브러리만 쓴다.
API·연결 오류는 서버가 적은 사유를 보여 주고 1, 사용법 오류는 2 (argparse) 로 끝난다.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Collection, Sequence
from http.client import HTTPException, InvalidURL
from typing import Any

DEFAULT_SERVER = "http://127.0.0.1:8000"
SERVER_ENV = "RUNBOARD_SERVER_URL"
TIMEOUT_S = 10.0
NODES_API = "/api/v1/admin/nodes"
# 웹 format.ts 의 connLabel 과 같다
CONN_LABEL = {"online": "정상", "delayed": "지연", "offline": "비활성화"}
FIELD_LABEL = {"display_name": "화면 이름"}
NODE_HELP = "노드 하나에만 맞는 식별자·화면 이름·호스트명"
SERVER_HELP = f"서버 주소 (기본: {SERVER_ENV}, 없으면 {DEFAULT_SERVER})"


class CliError(Exception):
    """사유를 그대로 보여 주고 종료 코드 1 로 끝나는 실패."""


# ---------------------------------------------------------------------- HTTP


def http(method: str, url: str, body: Any = None) -> tuple[int, bytes]:
    """요청 하나를 보내 (상태 코드, 본문) 을 돌려준다. 4xx·5xx 응답도 그대로 돌려준다.

    CLI 의 HTTP 는 전부 여기를 지난다. 테스트는 이 함수를 FastAPI TestClient 로 바꿔 끼운다.
    """
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=TIMEOUT_S) as res:
            return res.status, res.read()
    except urllib.error.HTTPError as exc:
        with exc:
            return exc.code, exc.read()
    # 포트가 숫자가 아닌 주소 등. InvalidURL 도 HTTPException 이라 아래보다 먼저 잡는다
    except (ValueError, InvalidURL) as exc:
        raise CliError(f"서버 주소가 올바르지 않다: {url} ({exc})") from None
    except OSError as exc:  # 연결 거부, 이름 풀이 실패, 시간 초과. URLError 도 OSError 다
        reason = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        raise CliError(f"서버에 연결할 수 없다: {url} ({reason})") from None
    except HTTPException as exc:  # HTTP 가 아닌 포트에 붙었다 (BadStatusLine 등)
        raise CliError(f"RunBoard 서버의 응답이 아니다: {url} ({exc!r})") from None


def call(server: str, method: str, path: str, body: Any = None) -> Any:
    """관리 API 를 부르고 JSON 본문을 돌려준다. 오류 응답이면 서버가 적은 사유로 CliError.

    RunBoard 는 오류도 JSON 으로 준다. 서버 안에서 처리하지 못한 예외(500)만 'Internal Server Error' 글자다.
    """
    url = server + path
    status, raw = http(method, url, body)
    try:
        payload = json.loads(raw) if raw else None
    except ValueError:
        if status < 500:  # HTTP 는 맞지만 다른 서버다 (웹 서버의 HTML 등)
            raise CliError(f"RunBoard 서버의 응답이 아니다 (HTTP {status}): {url}") from None
        payload = None
    if status >= 500:
        raise CliError(
            _detail(payload) or f"서버에서 오류가 났다 (HTTP {status}): {url}. 서버 로그를 본다"
        )
    if status >= 400:
        raise CliError(_detail(payload) or f"요청이 실패했다 (HTTP {status}): {url}")
    return payload


def _detail(payload: Any) -> str | None:
    """FastAPI 오류 본문의 사유. 검증 오류(422)는 항목마다 이어 붙인다."""
    detail = payload.get("detail") if isinstance(payload, dict) else None
    if isinstance(detail, str):
        return detail
    if isinstance(detail, list):
        return " / ".join(_issue(i) for i in detail if isinstance(i, dict)) or None
    return None


def _issue(issue: dict) -> str:
    """서버가 직접 쓴 문장(Value error)은 그대로, pydantic 기본 문장은 항목 이름을 앞에 붙인다 (웹 api.ts 와 같다)."""
    msg = str(issue.get("msg", ""))
    if msg.startswith("Value error, "):
        return msg.removeprefix("Value error, ")
    loc = issue.get("loc") or []
    field = str(loc[-1]) if len(loc) > 1 else ""
    return f"{FIELD_LABEL.get(field, field)}: {msg}" if field else msg


def server_url(value: str | None) -> str:
    """--server, RUNBOARD_SERVER_URL, 기본 주소 순. 스킴이 없으면 http:// 로 본다 (서버는 HTTP 만 연다)."""
    url = (value or os.environ.get(SERVER_ENV) or DEFAULT_SERVER).strip()
    if "://" not in url:
        url = f"http://{url}"
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:  # 닫히지 않은 IPv6 대괄호 등
        raise CliError(f"서버 주소가 올바르지 않다: {url} ({exc})") from None
    if parts.scheme not in ("http", "https"):
        raise CliError(f"서버 주소는 http:// 로 시작한다: {url}")
    if not parts.hostname:
        raise CliError(f"서버 주소에 호스트가 없다: {url}")
    return url.rstrip("/")


# ---------------------------------------------------------------------- 노드 찾기·표


def _nodes(server: str) -> list[dict]:
    nodes = call(server, "GET", NODES_API)
    if not isinstance(nodes, list):
        raise CliError(f"RunBoard 서버의 응답이 아니다: {server}{NODES_API}")
    return nodes


def resolve(nodes: list[dict], name: str) -> dict:
    """식별자·화면 이름·호스트명 중 하나가 name 인 노드. 노드 하나에만 맞아야 한다. 없거나 여럿이면 CliError.

    식별자가 맞아도 먼저 고르지 않는다. node_id 를 붙여 다시 설치한 노드는 호스트명으로 등록됐던 옛 노드와
    호스트명이 같고 그 호스트명이 옛 노드의 식별자다. 새로 등록된 노드의 식별자가 다른 노드의 화면 이름일
    수도 있다. 이럴 때 말없이 한쪽을 고치거나 지우지 않는다.
    """
    found = [n for n in nodes if name in (n["key"], n["display_name"], n["hostname"])]
    if len(found) == 1:
        return found[0]
    if not found:
        raise CliError(f"노드가 없다: {name}. 'runboard-server nodes list' 로 이름과 식별자를 본다")
    listed = ", ".join(_label(n) for n in found)
    raise CliError(
        f"'{name}' 에 맞는 노드가 {len(found)}개다: {listed}. "
        "노드 하나에만 맞는 화면 이름이나 식별자로 지정한다"
    )


def _label(node: dict) -> str:
    """'Server_1 (식별자 fake01, 호스트명 fake01)'."""
    return f"{node['display_name']} (식별자 {node['key']}, 호스트명 {node['hostname']})"


def _width(text: str) -> int:
    """터미널에서 차지하는 칸 수. 한글 같은 전각 문자는 두 칸이다."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


def table(header: Sequence[str], rows: Sequence[Sequence[str]], right: Collection[int] = ()) -> str:
    """칸을 맞춘 표. right 는 오른쪽에 붙일 열 번호."""
    lines = [header, *rows]
    widths = [max(_width(line[i]) for line in lines) for i in range(len(header))]
    out = []
    for line in lines:
        cells = []
        for i, cell in enumerate(line):
            pad = " " * (widths[i] - _width(cell))
            cells.append(pad + cell if i in right else cell + pad)
        out.append("  ".join(cells).rstrip())
    return "\n".join(out)


def ago(seconds: float) -> str:
    """'12초 전'. 웹 format.ts 의 fmtAgo 와 같다."""
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}초 전"
    if s < 3600:
        return f"{s // 60}분 전"
    if s < 86400:
        return f"{s // 3600}시간 전"
    return f"{s // 86400}일 전"


def _status(node: dict) -> str:
    return CONN_LABEL.get(node["conn_status"], node["conn_status"])


def _node_path(node: dict) -> str:
    return f"{NODES_API}/{urllib.parse.quote(node['key'], safe='')}"


def _confirm(question: str) -> bool:
    try:
        answer = input(f"{question} [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print()
        return False
    return answer.strip().lower() in ("y", "yes")


# ---------------------------------------------------------------------- 명령


def cmd_list(server: str, args: argparse.Namespace) -> int:
    nodes = _nodes(server)
    if not nodes:
        print("아직 보고한 노드가 없다.")
        return 0
    header = ("화면 이름", "식별자", "호스트명", "상태", "마지막 보고", "GPU")
    rows = [
        (
            n["display_name"],
            n["key"],
            n["hostname"],
            _status(n),
            ago(n["age_s"]),
            str(n["gpu_count"]),
        )
        for n in nodes
    ]
    print(table(header, rows, right={5}))
    return 0


def cmd_rename(server: str, args: argparse.Namespace) -> int:
    node = resolve(_nodes(server), args.node)
    out = call(server, "PATCH", _node_path(node), {"display_name": args.name})
    print(
        f"화면 이름을 바꿨다: {node['display_name']} → {out['display_name']} (식별자 {node['key']})"
    )
    return 0


def cmd_remove(server: str, args: argparse.Namespace) -> int:
    """보고 중인 노드는 서버가 거절한다 (409). 확인은 거절 여부와 상관없이 먼저 받는다."""
    node = resolve(_nodes(server), args.node)
    if not args.yes:
        if sys.stdin is None or not sys.stdin.isatty():
            raise CliError(
                "확인할 터미널이 없어 지우지 않는다. 터미널에서 실행하거나 --yes 를 붙인다"
            )
        question = (
            f"{_label(node)} 노드를 지운다 (상태 {_status(node)}, 마지막 보고 {ago(node['age_s'])}). "
            "열린 작업은 '노드 삭제'로 끝나고 지난 작업·세션 이력은 남는다. 계속할까?"
        )
        if not _confirm(question):
            print("지우지 않았다.")
            return 1
    call(server, "DELETE", _node_path(node))
    print(f"노드를 지웠다: {_label(node)}")
    print("이 노드의 에이전트가 다시 보고하면 새 이름(Server_n)으로 다시 등록된다.")
    return 0


def add_commands(parser: argparse.ArgumentParser) -> None:
    """runboard-server 에 관리 명령을 붙인다. 명령 없이 실행하면 지금처럼 서버를 띄운다 (main.py)."""
    commands = parser.add_subparsers(
        dest="command",
        title="관리 명령",
        metavar="nodes",
        description="명령 없이 실행하면 서버를 띄운다. 관리 명령은 돌고 있는 서버의 관리 API 를 부른다",
    )
    nodes = commands.add_parser(
        "nodes",
        help="노드 목록, 화면 이름 변경, 끊긴 노드 삭제",
        description=f"서버 주소는 --server, 없으면 {SERVER_ENV}, 그것도 없으면 {DEFAULT_SERVER}",
    )
    nodes.add_argument("--server", metavar="URL", help=SERVER_HELP)
    # 동작 뒤에 적어도 된다. 적지 않았을 때 nodes 뒤에 적은 값을 None 으로 덮지 않게 기본값을 두지 않는다
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--server", metavar="URL", default=argparse.SUPPRESS, help=SERVER_HELP)
    actions = nodes.add_subparsers(dest="action", metavar="{list,rename,remove}", required=True)

    sub = actions.add_parser("list", parents=[common], help="노드 목록 (대시보드 순서)")
    sub.set_defaults(handler=cmd_list)

    sub = actions.add_parser("rename", parents=[common], help="화면 이름 변경")
    sub.add_argument("node", help=NODE_HELP)
    sub.add_argument(
        "name", help="새 화면 이름. 다른 노드의 화면 이름·식별자·호스트명과 겹치지 않게, 64자 이하"
    )
    sub.set_defaults(handler=cmd_rename)

    sub = actions.add_parser("remove", parents=[common], help="끊긴(비활성화) 노드 삭제")
    sub.add_argument("node", help=NODE_HELP)
    sub.add_argument("-y", "--yes", action="store_true", help="묻지 않고 지운다")
    sub.set_defaults(handler=cmd_remove)


def run(args: argparse.Namespace) -> int:
    """관리 명령 하나를 실행하고 종료 코드를 돌려준다."""
    try:
        return args.handler(server_url(args.server), args)
    except CliError as exc:
        print(f"오류: {exc}", file=sys.stderr)
        return 1
