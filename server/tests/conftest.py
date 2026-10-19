"""서버 테스트 공용 픽스처. fake 수집기 시나리오(agent/collector/fake.py)에 맞춘 설정."""

import hashlib

import pytest

from runboard_agent.collector.fake import FakeCollector
from runboard_common.schemas import NodeReport
from runboard_server.config import (
    AccountMapping,
    AuthSection,
    ServerConfig,
    ServerSection,
    ServiceEntry,
    UserEntry,
)

T0 = 1_760_600_000.0


def make_config(**overrides) -> ServerConfig:
    cfg = ServerConfig(
        server=ServerSection(db_url="sqlite://", persist_interval_s=30),
        auth=AuthSection(
            node_tokens={
                "fake01": "dev-token-fake01",
                "gpu01": "sha256:" + hashlib.sha256(b"dev-token-gpu01").hexdigest(),
            }
        ),
        users=[
            UserEntry(username="taehoon", display_name="임태훈", aliases=["th"]),
            UserEntry(username="minsu", display_name="김민수"),
            UserEntry(username="jihye"),
        ],
        account_mappings=[AccountMapping(uid=1001, user="taehoon")],
        services=[
            ServiceEntry(name="ollama", match_exec_account="ollama", owner="taehoon"),
            ServiceEntry(
                name="vllm",
                match_container_pattern="^vllm",
                match_image_pattern="vllm/vllm-openai",
                owner=None,
            ),
        ],
    )
    for key, value in overrides.items():
        setattr(cfg, key, value)
    return cfg


@pytest.fixture
def config() -> ServerConfig:
    return make_config()


def _fake_report(now: float = T0, node: str = "fake01", **kw) -> NodeReport:
    return FakeCollector(node_name=node, clock=lambda: now, **kw).collect()


@pytest.fixture
def fake_report():
    """fake 수집기 보고를 만드는 함수. importlib 모드라 conftest 를 import 하지 않고 픽스처로 받는다."""
    return _fake_report
