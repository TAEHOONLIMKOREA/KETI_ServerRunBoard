"""서버 설정 (runboard.yaml) 과 DB 접속 정보 (settings.ini).

MVP 동안은 관리 페이지(v0.3) 대신 이 파일 하나로 노드 토큰, 사용자, 계정 매핑,
공용 서비스 레지스트리, 임계값을 관리한다 (IMPLEMENTATION.md 1장).
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

SETTINGS_FILE = "settings.ini"  # 실행 디렉터리 기준. RUNBOARD_SETTINGS 로 바꾼다
_DB_SECTION = "postgres_db"
_DB_KEYS = ("host", "port", "dbname", "user", "password")


class ServerSection(BaseModel):
    db_url: str | None = Field(
        default=None,
        description="DB 접속 URL. 보통은 비워 두고 settings.ini 의 [postgres_db] 로 채운다",
    )
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


def load_db_url(path: Path) -> str:
    """settings.ini 의 [postgres_db] 로 PostgreSQL 접속 URL 을 만든다. 비밀번호의 특수문자는 URL 이 이스케이프한다."""
    parser = configparser.ConfigParser(interpolation=None)
    parser.read(path, encoding="utf-8")
    if not parser.has_section(_DB_SECTION):
        raise ValueError(f"{path} 에 [{_DB_SECTION}] 섹션이 없다")
    db = parser[_DB_SECTION]
    missing = [key for key in _DB_KEYS if not db.get(key)]
    if missing:
        raise ValueError(f"{path} 의 [{_DB_SECTION}] 에 값이 없다: {', '.join(missing)}")
    return URL.create(
        "postgresql+psycopg",
        username=db["user"],
        password=db["password"],
        host=db["host"],
        port=db.getint("port"),
        database=db["dbname"],
    ).render_as_string(hide_password=False)


def _require_postgres(db_url: str) -> None:
    try:
        url = make_url(db_url)
    except ArgumentError as exc:
        raise ValueError(f"DB 접속 URL 을 읽을 수 없다: {exc}") from None
    if url.get_backend_name() != "postgresql":
        raise ValueError(f"DB 는 PostgreSQL 만 쓴다: {url.render_as_string(hide_password=True)}")


def load_server_config(
    path: Path | None = None, env: Mapping[str, str] | None = None
) -> ServerConfig:
    """YAML 파일을 읽고 환경변수로 일부를 덮어쓴다. YAML 이 없으면 기본값.

    DB 접속 URL 우선순위: RUNBOARD_DB_URL > settings.ini > YAML 의 server.db_url. 셋 다 없으면 시작하지 않는다.
    """
    env = os.environ if env is None else env
    if path is None and env.get("RUNBOARD_CONFIG"):
        path = Path(env["RUNBOARD_CONFIG"])
    data: dict[str, Any] = {}
    if path is not None:
        if not path.exists():
            raise FileNotFoundError(f"설정 파일이 없다: {path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = ServerConfig.model_validate(data)
    settings = Path(env.get("RUNBOARD_SETTINGS") or SETTINGS_FILE)
    if settings.exists():
        cfg.server.db_url = load_db_url(settings)
    elif env.get("RUNBOARD_SETTINGS"):
        raise FileNotFoundError(f"DB 접속 설정 파일이 없다: {settings}")
    if env.get("RUNBOARD_DB_URL"):
        cfg.server.db_url = env["RUNBOARD_DB_URL"]
    if not cfg.server.db_url:
        raise FileNotFoundError(
            f"DB 접속 정보가 없다: {settings.resolve()} 를 만들고 [{_DB_SECTION}] 에 {', '.join(_DB_KEYS)} 를 적는다"
        )
    _require_postgres(cfg.server.db_url)
    if env.get("RUNBOARD_PERSIST_INTERVAL"):
        cfg.server.persist_interval_s = float(env["RUNBOARD_PERSIST_INTERVAL"])
    if env.get("RUNBOARD_WEB_DIST"):
        cfg.server.web_dist = env["RUNBOARD_WEB_DIST"]
    if env.get("RUNBOARD_SHARED_TOKEN"):
        cfg.auth.shared_token = env["RUNBOARD_SHARED_TOKEN"]
    return cfg
