"""서버 테스트 공용 픽스처. fake 수집기 시나리오(agent/collector/fake.py)에 맞춘 설정과 등록 정보.

DB 테스트는 PostgreSQL 에서 돈다. RUNBOARD_TEST_DB_URL(접속 URL, DB 이름은 아무것이나)의 서버에
runboard_test_<pid> DB 를 만들고 끝나면 지운다. CREATEDB 권한이 있어야 한다 (없으면 오류로 끝난다).
RUNBOARD_TEST_DB_URL 이 없으면 DB 테스트는 건너뛴다.
"""

import hashlib
import os

import pytest
from sqlalchemy import create_engine, make_url, text

from runboard_agent.collector.fake import FakeCollector
from runboard_common.schemas import NodeReport
from runboard_server.config import AuthSection, ServerConfig, ServerSection
from runboard_server.db.models import Base
from runboard_server.db.repo import Repository
from runboard_server.registry import (
    AccountMapping,
    MappingFields,
    Registry,
    ServiceEntry,
    ServiceFields,
    UserEntry,
)

T0 = 1_760_600_000.0


def make_config(**overrides) -> ServerConfig:
    cfg = ServerConfig(
        server=ServerSection(persist_interval_s=30),
        auth=AuthSection(
            node_tokens={
                "fake01": "dev-token-fake01",
                "gpu01": "sha256:" + hashlib.sha256(b"dev-token-gpu01").hexdigest(),
            }
        ),
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


def make_registry() -> Registry:
    return Registry(
        users=[
            UserEntry(username="taehoon", display_name="임태훈", aliases=["th"]),
            UserEntry(username="minsu", display_name="김민수"),
            UserEntry(username="jihye"),
        ],
        account_mappings=[AccountMapping(id=1, uid=1001, username="taehoon")],
        services=[
            ServiceEntry(id=1, name="ollama", match_exec_account="ollama", owner="taehoon"),
            ServiceEntry(
                id=2,
                name="vllm",
                match_container_pattern="^vllm",
                match_image_pattern="vllm/vllm-openai",
                owner=None,
            ),
        ],
    )


@pytest.fixture
def config() -> ServerConfig:
    return make_config()


@pytest.fixture
def registry() -> Registry:
    return make_registry()


def _fake_report(now: float = T0, node: str = "fake01", **kw) -> NodeReport:
    return FakeCollector(node_name=node, clock=lambda: now, **kw).collect()


@pytest.fixture
def fake_report():
    """fake 수집기 보고를 만드는 함수. importlib 모드라 conftest 를 import 하지 않고 픽스처로 받는다."""
    return _fake_report


@pytest.fixture(scope="session")
def pg_url():
    base = os.environ.get("RUNBOARD_TEST_DB_URL")
    if not base:
        pytest.skip("RUNBOARD_TEST_DB_URL 이 없다 (PostgreSQL 접속 URL). DB 테스트를 건너뛴다")
    url = make_url(base)
    name = f"runboard_test_{os.getpid()}"
    admin = create_engine(url, isolation_level="AUTOCOMMIT")
    with admin.connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))
    try:
        yield url.set(database=name).render_as_string(hide_password=False)
    finally:
        with admin.connect() as conn:
            conn.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
        admin.dispose()


@pytest.fixture
def repo(pg_url):
    """빈 표로 시작하는 저장소."""
    r = Repository(pg_url)
    tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with r.engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {tables} RESTART IDENTITY CASCADE"))
    yield r
    r.close()


@pytest.fixture
def seed():
    """등록 정보를 DB 에 넣는 함수. 관리 API 와 같은 저장소 메서드를 쓴다."""

    def _seed(repo: Repository, registry: Registry) -> None:
        for user in registry.users:
            repo.add_user(user)
        for mapping in registry.account_mappings:
            repo.add_mapping(MappingFields(**mapping.model_dump(exclude={"id"})))
        for service in registry.services:
            repo.add_service(ServiceFields(**service.model_dump(exclude={"id"})))

    return _seed
