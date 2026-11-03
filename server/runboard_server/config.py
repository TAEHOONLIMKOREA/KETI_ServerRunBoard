"""서버 설정 (runboard.yaml) 과 DB 접속 정보 (settings.ini).

runboard.yaml 에는 실행 설정, 노드 토큰, 임계값, 소유자 판정 규칙만 둔다. 사용자·공용 서비스는
DB 에 두고 웹 관리 화면에서 고친다 (registry.py).
DB 접속 정보는 git 에 올리지 않는 settings.ini 에 따로 둔다. 코드에는 접속 정보가 없다. DB 는 PostgreSQL 만 쓴다.
"""

from __future__ import annotations

import configparser
import os
from collections.abc import Mapping
from pathlib import Path
from typing import Annotated, Any

import yaml
from pydantic import BaseModel, Field
from sqlalchemy.engine import URL, make_url
from sqlalchemy.exc import ArgumentError

SETTINGS_FILE = "settings.ini"  # 실행 디렉터리 기준. RUNBOARD_SETTINGS 로 바꾼다
_DB_SECTION = "postgres_db"
_DB_KEYS = ("host", "port", "dbname", "user", "password")
_MOVED_TO_DB = ("users", "services")  # 예전 YAML 섹션. 지금은 DB 의 등록 정보
_RETENTION_ENV = {
    "raw_days": "RUNBOARD_RETENTION_RAW_DAYS",
    "agg_days": "RUNBOARD_RETENTION_AGG_DAYS",
    "sessions_days": "RUNBOARD_RETENTION_SESSIONS_DAYS",
}
# 보존 기간(일). 0 보다 크고 유한해야 한다. 무한이나 터무니없이 큰 값은 기한이 bucket_ts(BIGINT) 범위를
# 넘으므로 100년을 상한으로 둔다. '계속 보존' 은 없으니 길게 두려면 상한까지 쓴다
RETENTION_MAX_DAYS = 36500
Days = Annotated[float, Field(gt=0, le=RETENTION_MAX_DAYS, allow_inf_nan=False)]


class RetentionSection(BaseModel):
    """보존 기간(일). 서버가 시작할 때와 1시간마다 지난 행을 지운다. 노드·GPU 행은 남긴다."""

    raw_days: Days = Field(default=7, description="원본 샘플 (gpu_samples)")
    agg_days: Days = Field(default=90, description="5분 집계 (gpu_samples_5m)")
    sessions_days: Days = Field(
        default=365, description="끝난 세션, 그리고 세션이 다 지워진 끝난 작업"
    )


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
    retention: RetentionSection = Field(default_factory=RetentionSection)


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
    ignore_commands: list[str] = Field(
        default_factory=lambda: ["Xorg"],
        description="보고에서 빼는 프로세스의 실행 파일명 (명령어 첫 토큰의 파일명). 데스크톱 화면 서버처럼 GPU 를 조금 쓰지만 작업이 아닌 것",
    )


class ServerConfig(BaseModel):
    server: ServerSection = Field(default_factory=ServerSection)
    auth: AuthSection = Field(default_factory=AuthSection)
    thresholds: Thresholds = Field(default_factory=Thresholds)
    owner: OwnerRules = Field(default_factory=OwnerRules)


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
        moved = [key for key in _MOVED_TO_DB if key in data]
        if moved:
            raise ValueError(
                f"{path} 의 {', '.join(moved)} 는 이제 DB 에서 관리한다. "
                "웹 관리 화면(/admin)에서 등록하고 YAML 에서는 지운다"
            )
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
    retention = {key: env[name] for key, name in _RETENTION_ENV.items() if env.get(name)}
    if retention:  # 0 이하(모든 행이 기한을 넘긴다)나 inf 를 막도록 YAML 값과 같은 검증을 거친다
        cfg.server.retention = RetentionSection.model_validate(
            cfg.server.retention.model_dump() | retention
        )
    if env.get("RUNBOARD_WEB_DIST"):
        cfg.server.web_dist = env["RUNBOARD_WEB_DIST"]
    if env.get("RUNBOARD_SHARED_TOKEN"):
        cfg.auth.shared_token = env["RUNBOARD_SHARED_TOKEN"]
    return cfg
