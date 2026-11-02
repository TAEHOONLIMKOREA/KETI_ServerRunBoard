from pathlib import Path

import pytest

from runboard_agent.config import AgentConfig, load_config


def test_defaults_without_file(tmp_path: Path):
    cfg = load_config(None, env={}, overrides=None)
    assert cfg.collector == "nvml"
    assert (cfg.active_interval, cfg.idle_interval, cfg.idle_grace) == (1.0, 30.0, 5.0)
    assert cfg.server_url is None


def test_precedence_file_env_cli(tmp_path: Path):
    toml = tmp_path / "agent.toml"
    toml.write_text(
        'server_url = "http://file"\ntoken = "file-token"\n'
        "active_interval = 2\nidle_interval = 40\n"
        "[fake]\ngpu_count = 2\nchurn = false\n",
        encoding="utf-8",
    )
    env = {"RUNBOARD_SERVER": "http://env", "RUNBOARD_ACTIVE_INTERVAL": "3"}
    cfg = load_config(
        toml,
        env=env,
        overrides={"server_url": "http://cli", "token": None, "fake": {"gpu_count": 4}},
    )
    assert cfg.server_url == "http://cli", "CLI 가 환경변수와 파일을 이긴다"
    assert cfg.token == "file-token", "CLI 의 None 은 지정 안 함"
    assert cfg.active_interval == 3.0, "환경변수가 파일을 이긴다"
    assert cfg.idle_interval == 40.0
    assert cfg.fake.gpu_count == 4
    assert cfg.fake.churn is False, "중첩 설정은 깊게 합쳐진다"


def test_legacy_interval_means_active_interval(tmp_path: Path):
    toml = tmp_path / "agent.toml"
    toml.write_text("interval = 7\n", encoding="utf-8")
    cfg = load_config(toml, env={})
    assert cfg.active_interval == 7.0 and cfg.idle_interval == 30.0
    assert load_config(None, env={"RUNBOARD_INTERVAL": "9"}).active_interval == 9.0
    both = AgentConfig.from_dict({"interval": 7, "active_interval": 2})
    assert both.active_interval == 2.0, "둘 다 있으면 새 이름"


def test_legacy_interval_keeps_its_layer_priority(tmp_path: Path):
    toml = tmp_path / "agent.toml"
    toml.write_text("active_interval = 2\n", encoding="utf-8")
    legacy = {"RUNBOARD_INTERVAL": "9"}
    both = {"RUNBOARD_ACTIVE_INTERVAL": "3", "RUNBOARD_INTERVAL": "9"}

    def active(env, overrides=None):
        return load_config(toml, env=env, overrides=overrides).active_interval

    assert active(legacy) == 9.0, "예전 이름이어도 환경변수가 파일을 이긴다"
    assert active(both) == 3.0, "같은 곳에 둘 다 있으면 새 이름"
    assert active(legacy, {"active_interval": 4}) == 4.0, "CLI 가 환경변수를 이긴다"
    assert active(both, {"interval": 5}) == 5.0, "CLI 의 예전 이름도 CLI 우선순위"
    toml.write_text("interval = 7\nactive_interval = 2\n", encoding="utf-8")
    assert active({}) == 2.0, "파일에 둘 다 있으면 새 이름"


def test_missing_explicit_file_is_error(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        load_config(tmp_path / "nope.toml", env={})


@pytest.mark.parametrize(
    "data",
    [
        {"active_interval": 0},
        {"idle_interval": 0},
        {"active_interval": 5, "idle_interval": 2},
        {"idle_grace": -1},
        {"collector": "magic"},
        {"fake": {"gpu_count": -1}},
    ],
)
def test_validation(data):
    with pytest.raises(ValueError):
        AgentConfig.from_dict(data)


def test_node_id_from_env_and_file(tmp_path: Path):
    assert load_config(None, env={}).node_id is None
    assert load_config(None, env={"RUNBOARD_NODE_ID": "abc-123"}).node_id == "abc-123"
    toml = tmp_path / "agent.toml"
    toml.write_text("node_id = 'from-file'", encoding="utf-8")
    assert load_config(toml, env={}).node_id == "from-file"
