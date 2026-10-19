"""작업(workload) 묶음 키와 이름 (PLAN 4.2).

- 컨테이너가 있으면 컨테이너 ID 로 묶는다. DDP 8프로세스가 한 컨테이너면 작업 1개.
- 없으면 (노드, 부팅 ID, 프로세스 그룹 ID). torchrun·multiprocessing 자식은 pgid 를 공유한다.
- 작업명: runboard.job 라벨 → 컨테이너명 → 명령어의 스크립트명 → 실행 파일명.
"""

from __future__ import annotations

import posixpath

from runboard_common.schemas import ProcessReport
from runboard_server.core.owner import OwnerDecision


def workload_key(node_name: str, boot_id: str, proc: ProcessReport) -> str:
    if proc.container is not None:
        return f"{node_name}:c:{proc.container.id[:12]}"
    group = proc.pgid if proc.pgid is not None else proc.pid
    return f"{node_name}:p:{boot_id}:{group}"


def script_name(cmdline: str) -> str | None:
    """`python -m torch.distributed.run train.py --x` → train.py, `python -m mod` → mod, 아니면 argv0 의 파일명."""
    tokens = cmdline.split()
    if not tokens:
        return None
    for token in tokens:
        if token.endswith((".py", ".sh")) and not token.startswith("-"):
            return posixpath.basename(token)
    for i, token in enumerate(tokens[:-1]):
        if token == "-m":
            return tokens[i + 1]
    return posixpath.basename(tokens[0]) or None


def workload_name(proc: ProcessReport, decision: OwnerDecision) -> str:
    if decision.job:
        return decision.job
    if decision.service_name:
        return decision.service_name
    if proc.container is not None and proc.container.name:
        return proc.container.name
    return script_name(proc.cmdline) or f"pid {proc.pid}"
