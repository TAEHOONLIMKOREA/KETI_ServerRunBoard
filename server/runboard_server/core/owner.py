"""작업 유형과 소유자 판정.

에이전트는 사실만 보낸다. 여기서 사실을 규칙에 넣어 유형(personal / container / shared_service)과
소유자를 정한다. 판정할 수 없으면 소유자는 None, 근거는 "none" 이다. 추정해서 이름을 붙이지 않는다.

유형 판정 순서
  1. 서비스 레지스트리 매칭 (실행 계정, 컨테이너명 패턴, 이미지 패턴) → shared_service
  2. 컨테이너 안에서 실행 → container
  3. 그 외 → personal

소유자 판정 순서 (먼저 맞는 규칙이 이긴다)
  1. 컨테이너 라벨 runboard.owner. 노드에 Docker 소켓 중계가 있으면 중계가 실행 계정으로 덮어쓴 값이다
  2. 컨테이너명 규칙 <user>_<용도>. user 가 등록된 사용자·별칭일 때만.
     중계를 거친 컨테이너(runboard.launcher 라벨)에는 쓰지 않는다
  3. 공용 서비스면 레지스트리의 담당자
  4. 계정 매핑: (노드, uid) → 사용자. 없으면 (모든 노드, uid). 그것도 없으면
     실행 계정명이 등록된 사용자·별칭과 같을 때. uid 0(root) 은 명시적 매핑이 있을 때만
  5. 없으면 미확인
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from runboard_common.schemas import LAUNCHER_LABEL, ProcessReport
from runboard_server.config import OwnerRules
from runboard_server.registry import Registry, ServiceEntry

WorkloadType = Literal["personal", "container", "shared_service"]
OwnerSource = Literal["label", "container_rule", "service", "account_map", "none"]
MAX_COLOR_SLOTS = 8  # 대시보드 범주색 수. 등록 사용자 순서대로 앞 8명이 고유색을 받는다


@dataclass(frozen=True)
class OwnerDecision:
    workload_type: WorkloadType
    owner: str | None
    source: OwnerSource
    service_name: str | None = None
    project: str | None = None
    job: str | None = None


@dataclass(frozen=True)
class _CompiledService:
    entry: ServiceEntry
    exec_account: str | None
    container_re: re.Pattern[str] | None
    image_re: re.Pattern[str] | None

    def matches(self, proc: ProcessReport) -> bool:
        if (
            self.exec_account
            and proc.exec_username
            and (proc.exec_username.lower() == self.exec_account)
        ):
            return True
        if proc.container is not None:
            if (
                self.container_re
                and proc.container.name
                and self.container_re.search(proc.container.name)
            ):
                return True
            if (
                self.image_re
                and proc.container.image
                and self.image_re.search(proc.container.image)
            ):
                return True
        return False


class OwnerResolver:
    """판정 규칙(runboard.yaml owner)과 등록 정보(DB)로 만든다. 등록 정보가 바뀌면 새로 만든다."""

    def __init__(self, rules: OwnerRules, registry: Registry) -> None:
        self._canonical: dict[str, str] = {}
        self._display: dict[str, str] = {}
        for user in registry.users:
            self._canonical[user.username.lower()] = user.username
            for alias in user.aliases:
                self._canonical[alias.lower()] = user.username
            self._display[user.username] = user.display_name or user.username
        self._slot: dict[str, int] = {
            u.username: i for i, u in enumerate(registry.users[:MAX_COLOR_SLOTS])
        }

        self._acct_by_node: dict[tuple[str, int], str] = {}
        self._acct_any: dict[int, str] = {}
        for mapping in registry.account_mappings:
            if mapping.node:
                self._acct_by_node[(mapping.node, mapping.uid)] = mapping.username
            else:
                self._acct_any[mapping.uid] = mapping.username

        self._services = [
            _CompiledService(
                entry=s,
                exec_account=s.match_exec_account.lower() if s.match_exec_account else None,
                container_re=re.compile(s.match_container_pattern)
                if s.match_container_pattern
                else None,
                image_re=re.compile(s.match_image_pattern) if s.match_image_pattern else None,
            )
            for s in registry.services
        ]
        self._container_rule = (
            re.compile(rules.container_name_rule) if rules.container_name_rule else None
        )
        self._label_owner = rules.label_owner
        self._label_project = rules.label_project
        self._label_job = rules.label_job
        self._service_by_name = {s.name: s for s in registry.services}

    def canonical(self, name: str | None) -> str | None:
        """등록된 사용자·별칭이면 대표 username, 아니면 None."""
        if not name:
            return None
        return self._canonical.get(name.strip().lower())

    def display_name(self, username: str | None) -> str | None:
        if username is None:
            return None
        return self._display.get(username, username)

    def color_slot(self, username: str | None) -> int | None:
        """대시보드 색 번호 (0~7). 등록 사용자 순서대로 앞 8명. 그 밖은 None (중립색)."""
        if username is None:
            return None
        return self._slot.get(username)

    def service(self, name: str) -> ServiceEntry | None:
        return self._service_by_name.get(name)

    def classify(self, proc: ProcessReport, node_name: str | Sequence[str]) -> OwnerDecision:
        """node_name 은 노드를 가리키는 이름들. 식별자·호스트명·화면 이름 중 어느 것으로 매핑해도 맞는다."""
        names = (node_name,) if isinstance(node_name, str) else tuple(n for n in node_name if n)
        labels = proc.container.labels if proc.container else {}
        project = labels.get(self._label_project) or None
        job = labels.get(self._label_job) or None

        matched = next((s for s in self._services if s.matches(proc)), None)
        if matched:
            workload_type: WorkloadType = "shared_service"
        elif proc.container is not None:
            workload_type = "container"
        else:
            workload_type = "personal"
        service_name = matched.entry.name if matched else None

        owner, source = self._resolve_owner(proc, names, labels, matched)
        return OwnerDecision(
            workload_type=workload_type,
            owner=owner,
            source=source,
            service_name=service_name,
            project=project,
            job=job,
        )

    def _resolve_owner(
        self,
        proc: ProcessReport,
        names: tuple[str, ...],
        labels: dict[str, str],
        service: _CompiledService | None,
    ) -> tuple[str | None, OwnerSource]:
        label_owner = (labels.get(self._label_owner) or "").strip()
        if label_owner:
            return self.canonical(label_owner) or label_owner, "label"

        # 노드의 Docker 소켓 중계(에이전트 docker-proxy)를 거친 컨테이너는 실행 계정을 이미 안다.
        # 그런데도 소유자 라벨이 없으면 root 나 서비스가 띄운 것이라 이름으로 짐작하지 않는다
        by_proxy = LAUNCHER_LABEL in labels
        if (
            proc.container is not None
            and proc.container.name
            and self._container_rule
            and not by_proxy
        ):
            match = self._container_rule.match(proc.container.name)
            if match:
                user = self.canonical(match.groupdict().get("user") or match.group(0))
                if user:
                    return user, "container_rule"

        if service is not None and service.entry.owner:
            return self.canonical(service.entry.owner) or service.entry.owner, "service"

        if proc.exec_uid is not None:
            by_node = (self._acct_by_node.get((n, proc.exec_uid)) for n in names)
            mapped = next((m for m in by_node if m), None) or self._acct_any.get(proc.exec_uid)
            if mapped:
                return self.canonical(mapped) or mapped, "account_map"
        if proc.exec_uid != 0:
            user = self.canonical(proc.exec_username)
            if user:
                return user, "account_map"

        return None, "none"
