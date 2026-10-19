from pathlib import Path

import pytest

from runboard_agent.config import AgentConfig, load_config


def test_defaults_without_file(tmp_path: Path):
    cfg = load_config(None, env={}, overrides=None)
    assert cfg.collector == "nvml"
    assert cfg.interval == 5.0
    assert cfg.server_url is None


def test_precedence_file_env_cli(tmp_path: Path):
    toml = tmp_path / "agent.toml"
    toml.write_text(
        'server_url = "http://file"\ntoken = "file-token"\ninterval = 7\n'
        "[fake]\ngpu_count = 2\nchurn = false\n",
        encoding="utf-8",
    )
    env = {"RUNBOARD_SERVER": "http://env", "RUNBOARD_INTERVAL": "9"}
    cfg = load_config(
        toml,
        env=env,
        overrides={"server_url": "http://cli", "token": None, "fake": {"gpu_count": 4}},
    )
    assert cfg.server_url == "http://cli", "CLI 가 환경변수와 파일을 이긴다"
    assert cfg.token == "file-token", "CLI 의 None 은 지정 안 함"
    assert cfg.interval == 9.0, "환경변수가 파일을 이긴다"
    assert cfg.fake.gpu_count == 4
    assert cfg.fake.churn is False, "중첩 설정은 깊게 합쳐진다"


def test_missing_explicit_file_is_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml", env={})


@pytest.mark.parametrize(
    "data",
    [
        {"interval": 0},
        {"collector": "magic"},
        {"fake": {"gpu_count": -1}},
    ],
)
def test_validation(data):
    with pytest.raises(ValueError):
        AgentConfig.from_dict(data)
