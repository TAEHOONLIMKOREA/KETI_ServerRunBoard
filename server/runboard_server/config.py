"""서버 설정 (runboard.yaml).

MVP 동안은 관리 페이지(v0.3) 대신 이 파일 하나로 노드 토큰, 사용자, 계정 매핑,
공용 서비스 레지스트리, 임계값을 관리한다 (IMPLEMENTATION.md 1장).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field


class ServerSection(BaseModel):
    db_url: str = "sqlite:///data/runboard.db"
    persist_interval_s: float = Field(default=30.0, gt=0)
    web_dist: str | None = Field(default=None, description="빌드된 프론트 디렉터리. 없으면 API 만")
    host: str = "0.0.0.0"
    port: int = 8000
    display_name_prefix: str = Field(
        default="Server_",
        description="처음 본 노드의 화면 이름 접두어. Server_1, Server_2 … 순으로 붙는다",
    )


class AuthSection(BaseModel):
    node_tokens: dict[str, str] = Field(
        default_factory=dict, description="노드 이름 → 토큰. 평문 또는 'sha256:<hex>'"
    )
    shared_token: str | None = Field(
        default=None,
        description="등록 토큰. 이 토큰으로 보고하는 노드는 이름을 미리 등록하지 않아도 참여한다 (재시작 불필요)",
    )


class Thresholds(BaseModel):
    """판정 임계값. delayed_s·offline_s 는 에이전트가 보고에 적은 보고 주기에 더해서 쓴다 (core/detector.py)."""

    delayed_s: float = 15.0
    offline_s: float = 30.0
    offline_close_after_s: float = 86400.0
    activity_window_s: float = 60.0
    activity_low_pct: float = 10.0
    temp_warn_c: int = 85
    session_miss_reports: int = Field(default=2, ge=1)
    occupied_mem_pct: float = 70.0
    busy_util_pct: float = 90.0
    clock_skew_max_s: float = 60.0
    util_history_s: float = 120.0


class OwnerRules(BaseModel):
    container_name_rule: str | None = Field(
        default=r"^(?P<user>[A-Za-z][A-Za-z0-9]*)[_-]",
        description="컨테이너명에서 사용자를 뽑는 정규식. 그룹 이름은 user",
    )
    label_owner: str = "runboard.owner"
    label_project: str = "runboard.project"
    label_job: str = "runboard.job"


class UserEntry(BaseModel):
    username: str
    display_name: str | None = None
    email: str | None = None
    slack_id: str | None = None
    aliases: list[str] = Field(default_factory=list, description="호스트 계정명 등 다른 이름")


class AccountMapping(BaseModel):
    node: str | None = Field(default=None, description="None 이면 모든 노드")
    uid: int
    user: str


class ServiceEntry(BaseModel):
    name: str
    match_exec_account: str | None = None
    match_container_pattern: str | None = None
    match_image_pattern: str | None = None
    owner: str | None = Field(default=None, description="담당자 username")
    idle_exempt: bool = True


class ServerConfig(BaseModel):
    server: ServerSection = Field(default_factory=ServerSection)
    auth: AuthSection = Field(default_factory=AuthSection)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    owner: OwnerRules = Field(default_factory=OwnerRules)
    users: list[UserEntry] = Field(default_factory=list)
    account_mappings: list[AccountMapping] = Field(default_factory=list)
    services: list[ServiceEntry] = Field(default_factory=list)


def load_server_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> ServerConfig:
    """YAML 파일을 읽고 환경변수로 일부를 덮어쓴다. 파일이 없으면 기본값."""
    env = os.environ if env is None else env
    if path is None and env.get("RUNBOARD_CONFIG"):
        path = Path(env["RUNBOARD_CONFIG"])
    data: dict[str, Any] = {}
    if path is not None:
        if not path.exists():
            raise FileNotFoundError(f"설정 파일이 없다: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = ServerConfig.model_validate(data)
    if env.get("RUNBOARD_DB_URL"):
        cfg.server.db_url = env["RUNBOARD_DB_URL"]
    if env.get("RUNBOARD_PERSIST_INTERVAL"):
        cfg.server.persist_interval_s = float(env["RUNBOARD_PERSIST_INTERVAL"])
    if env.get("RUNBOARD_WEB_DIST"):
        cfg.server.web_dist = env["RUNBOARD_WEB_DIST"]
    if env.get("RUNBOARD_SHARED_TOKEN"):
        cfg.auth.shared_token = env["RUNBOARD_SHARED_TOKEN"]
    return cfg
