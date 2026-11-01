"""서버 설정: runboard.yaml 과 DB 접속 정보 settings.ini."""

from pathlib import Path

import pytest
from sqlalchemy import make_url

from runboard_server.config import load_db_url, load_server_config

SETTINGS = """\
[postgres_db]
host = db.internal
port = 6543
dbname = runboard
user = rb
password = p@ss:w/rd%
"""


def _write(tmp_path: Path, text: str = SETTINGS) -> Path:
    path = tmp_path / "settings.ini"
    path.write_text(text, encoding="utf-8")
    return path


def test_settings_ini_builds_postgres_url(tmp_path: Path):
    url = make_url(load_db_url(_write(tmp_path)))
    assert url.drivername == "postgresql+psycopg"
    assert (url.host, url.port, url.database, url.username) == (
        "db.internal",
        6543,
        "runboard",
        "rb",
    )
    assert url.password == "p@ss:w/rd%", "특수문자는 URL 이 이스케이프하고 % 는 보간하지 않는다"


def test_db_url_precedence(tmp_path: Path):
    yaml_file = tmp_path / "runboard.yaml"
    yaml_file.write_text("server:\n  db_url: postgresql://yaml-host/rb\n", encoding="utf-8")
    env = {"RUNBOARD_SETTINGS": str(_write(tmp_path))}
    cfg = load_server_config(yaml_file, env=env)
    assert make_url(cfg.server.db_url).host == "db.internal", "settings.ini 가 YAML 을 이긴다"
    env["RUNBOARD_DB_URL"] = "postgresql+psycopg://env-host/rb"
    assert load_server_config(yaml_file, env=env).server.db_url == env["RUNBOARD_DB_URL"], (
        "환경변수가 가장 세다"
    )


def test_without_settings_ini(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # 레포 루트의 실제 settings.ini 를 읽지 않게
    with pytest.raises(FileNotFoundError, match=r"\[postgres_db\] 에 host, port, dbname"):
        load_server_config(None, env={})
    yaml_file = tmp_path / "runboard.yaml"
    yaml_file.write_text("server:\n  db_url: postgresql://yaml-host/rb\n", encoding="utf-8")
    cfg = load_server_config(yaml_file, env={})
    assert cfg.server.db_url == "postgresql://yaml-host/rb", "YAML 의 db_url 도 읽는다"
    with pytest.raises(FileNotFoundError, match=r"missing\.ini"):
        load_server_config(yaml_file, env={"RUNBOARD_SETTINGS": "missing.ini"})


def test_only_postgres_is_accepted(tmp_path: Path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="PostgreSQL 만"):
        load_server_config(None, env={"RUNBOARD_DB_URL": "sqlite:///data/runboard.db"})
    with pytest.raises(ValueError, match="읽을 수 없다"):
        load_server_config(None, env={"RUNBOARD_DB_URL": "not a url"})


def test_registry_sections_in_yaml_are_rejected(tmp_path: Path):
    """사용자·매핑·서비스는 DB 로 옮겼다. 예전 YAML 을 그대로 쓰면 조용히 무시하지 않고 알린다."""
    yaml_file = tmp_path / "runboard.yaml"
    yaml_file.write_text("users:\n  - username: a\nservices: []\n", encoding="utf-8")
    env = {"RUNBOARD_SETTINGS": str(_write(tmp_path))}
    with pytest.raises(ValueError, match=r"users, services 는 이제 DB 에서 관리한다"):
        load_server_config(yaml_file, env=env)


def test_missing_settings_are_named(tmp_path: Path):
    with pytest.raises(ValueError, match="dbname, user, password"):
        load_db_url(_write(tmp_path, "[postgres_db]\nhost = h\nport = 5432\n"))
    with pytest.raises(ValueError, match=r"\[postgres_db\] 섹션이 없다"):
        load_db_url(_write(tmp_path, SETTINGS.replace("[postgres_db]", "[database]")))


def test_retention_from_yaml_and_env(tmp_path: Path):
    env = {"RUNBOARD_SETTINGS": str(_write(tmp_path))}
    retention = load_server_config(None, env=env).server.retention
    assert (retention.raw_days, retention.agg_days, retention.sessions_days) == (7, 90, 365)

    yaml_file = tmp_path / "runboard.yaml"
    yaml_file.write_text("server:\n  retention:\n    raw_days: 3\n", encoding="utf-8")
    env["RUNBOARD_RETENTION_AGG_DAYS"] = "30.5"
    retention = load_server_config(yaml_file, env=env).server.retention
    assert (retention.raw_days, retention.agg_days, retention.sessions_days) == (3, 30.5, 365), (
        "환경변수가 YAML 을 덮어쓴다"
    )
    env["RUNBOARD_RETENTION_SESSIONS_DAYS"] = "0"
    with pytest.raises(ValueError, match="sessions_days"):
        load_server_config(yaml_file, env=env)  # 0 이면 끝난 세션이 전부 지워진다
    env["RUNBOARD_RETENTION_SESSIONS_DAYS"] = "inf"
    with pytest.raises(ValueError, match="finite"):
        load_server_config(yaml_file, env=env)  # 무한이면 기한을 계산하지 못해 tick 마다 실패한다


@pytest.mark.parametrize("value", [".inf", "36501"])
def test_retention_must_be_finite_and_bounded(tmp_path: Path, value: str):
    """'계속 보존' 으로 .inf 를 적거나 아주 큰 값을 적으면 기한이 bucket_ts(BIGINT) 범위를 넘는다."""
    yaml_file = tmp_path / "runboard.yaml"
    yaml_file.write_text(f"server:\n  retention:\n    agg_days: {value}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="agg_days"):
        load_server_config(yaml_file, env={"RUNBOARD_SETTINGS": str(_write(tmp_path))})
    yaml_file.write_text("server:\n  retention:\n    agg_days: 36500\n", encoding="utf-8")
    cfg = load_server_config(yaml_file, env={"RUNBOARD_SETTINGS": str(_write(tmp_path))})
    assert cfg.server.retention.agg_days == 36500, "100년까지는 받는다"


def test_example_yaml_loads(tmp_path: Path):
    example = Path(__file__).resolve().parents[2] / "deploy" / "runboard.example.yaml"
    cfg = load_server_config(example, env={"RUNBOARD_SETTINGS": str(_write(tmp_path))})
    assert cfg.server.retention.raw_days == 7 and cfg.server.retention.agg_days == 90
