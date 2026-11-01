"""PID → 실행 계정, 프로세스 그룹, 컨테이너.

- 실행 계정·ppid·시작 시각·명령어: psutil. 다른 사용자 프로세스도 root 없이 읽힌다 (명령어는 환경에 따라 거부될 수 있음).
- 프로세스 그룹(pgid): Linux 만. 작업 묶음 키다.
- 컨테이너: /proc/<pid>/cgroup 에서 ID 를 뽑고 Docker 소켓에서 이름·이미지·라벨을 조회한다. 조회는 이것 하나뿐이다.
  Docker 를 못 쓰면 ID 만 보내고 오류를 남긴다. 환경변수는 읽지 않는다.

cgroup 문자열 파싱은 순수 함수라 어느 OS 에서나 테스트한다.
"""

from __future__ import annotations

import contextlib
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field

import psutil

from runboard_agent.masking import join_cmdline, mask_cmdline
from runboard_common.schemas import ContainerInfo, ReportError

log = logging.getLogger(__name__)

IS_LINUX = sys.platform.startswith("linux")

_CGROUP_PATTERNS = [
    re.compile(r"/docker/([0-9a-f]{64})"),  # cgroup v1, cgroupfs 드라이버
    re.compile(r"docker-([0-9a-f]{64})\.scope"),  # cgroup v2, systemd 드라이버
    re.compile(r"cri-containerd-([0-9a-f]{64})\.scope"),  # containerd / k8s
    re.compile(r"/containerd/([0-9a-f]{64})"),
    re.compile(r"/kubepods[^\n]*?/([0-9a-f]{64})"),
    re.compile(r"libpod-([0-9a-f]{64})"),  # podman
]


def container_id_from_cgroup(text: str) -> str | None:
    """/proc/<pid>/cgroup 내용에서 컨테이너 ID(64자 hex)를 찾는다. 없으면 None."""
    for line in text.splitlines():
        path = line.rsplit(":", 1)[-1]
        for pattern in _CGROUP_PATTERNS:
            match = pattern.search(path)
            if match:
                return match.group(1)
    return None


def read_cgroup(pid: int) -> str | None:
    try:
        with open(f"/proc/{pid}/cgroup", encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return None


@dataclass
class ProcessFacts:
    exec_username: str | None = None
    exec_uid: int | None = None
    ppid: int | None = None
    pgid: int | None = None
    cmdline: str = ""
    proc_start_ts: float | None = None
    container: ContainerInfo | None = None
    errors: list[ReportError] = field(default_factory=list)


class Enricher:
    def __init__(self, docker_enabled: bool = True, cache_ttl_s: float = 60.0) -> None:
        self.docker_enabled = docker_enabled and IS_LINUX
        self.cache_ttl_s = cache_ttl_s
        self._docker = None
        self._docker_retry_after = 0.0
        self._cache: dict[str, tuple[float, ContainerInfo]] = {}

    def facts(self, pid: int, scope: str) -> ProcessFacts:
        out = ProcessFacts()
        try:
            proc = psutil.Process(pid)
            with proc.oneshot():
                out.ppid = proc.ppid()
                out.proc_start_ts = proc.create_time()
                try:
                    out.exec_username = proc.username()
                except (psutil.AccessDenied, KeyError):
                    out.exec_username = None
                if hasattr(proc, "uids"):
                    with contextlib.suppress(psutil.AccessDenied):
                        out.exec_uid = proc.uids().real
                try:
                    out.cmdline = mask_cmdline(join_cmdline(proc.cmdline())) or proc.name()
                except psutil.AccessDenied:
                    out.cmdline = ""
                    out.errors.append(
                        ReportError(scope=scope, item="cmdline", code="NO_PERMISSION")
                    )
        except psutil.NoSuchProcess:
            out.errors.append(
                ReportError(scope=scope, item="process", code="ERROR", message="종료됨")
            )
            return out
        except psutil.AccessDenied:
            out.errors.append(ReportError(scope=scope, item="process", code="NO_PERMISSION"))

        if IS_LINUX:
            try:
                out.pgid = os.getpgid(pid)
            except (ProcessLookupError, PermissionError):
                out.pgid = None
            cgroup = read_cgroup(pid)
            cid = container_id_from_cgroup(cgroup) if cgroup else None
            if cid:
                out.container = self._container(cid, scope, out.errors)
        return out

    # ------------------------------------------------------------------ Docker

    def _container(self, cid: str, scope: str, errors: list[ReportError]) -> ContainerInfo:
        now = time.monotonic()
        cached = self._cache.get(cid)
        if cached and now - cached[0] < self.cache_ttl_s:
            return cached[1]
        info = ContainerInfo(id=cid)
        client = self._client(scope, errors)
        if client is not None:
            try:
                c = client.containers.get(cid)
                tags = c.image.tags if c.image is not None else []
                image = tags[0] if tags else (c.attrs.get("Config", {}).get("Image") or None)
                info = ContainerInfo(id=cid, name=c.name, image=image, labels=dict(c.labels or {}))
            except Exception as exc:  # docker.errors.NotFound 포함. 사라진 컨테이너는 ID 만 보낸다
                code = "ERROR" if type(exc).__name__ != "NotFound" else "NOT_FOUND"
                errors.append(
                    ReportError(scope=scope, item="container", code=code, message=str(exc)[:120])
                )
        self._cache[cid] = (now, info)
        return info

    def _client(self, scope: str, errors: list[ReportError]):
        if not self.docker_enabled:
            return None
        if self._docker is not None:
            return self._docker
        if time.monotonic() < self._docker_retry_after:
            errors.append(ReportError(scope=scope, item="container", code="NO_PERMISSION"))
            return None
        try:
            import docker

            self._docker = docker.from_env(timeout=2)
            self._docker.ping()
        except Exception as exc:
            self._docker = None
            self._docker_retry_after = time.monotonic() + 60.0
            log.warning("Docker 조회 불가 (60초 후 재시도): %s", str(exc)[:160])
            errors.append(
                ReportError(
                    scope=scope, item="container", code="NO_PERMISSION", message=str(exc)[:120]
                )
            )
        return self._docker
