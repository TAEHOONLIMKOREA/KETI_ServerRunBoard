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


def test_missing_settings_are_named(tmp_path: Path):
    with pytest.raises(ValueError, match="dbname, user, password"):
        load_db_url(_write(tmp_path, "[postgres_db]\nhost = h\nport = 5432\n"))
    with pytest.raises(ValueError, match=r"\[postgres_db\] 섹션이 없다"):
        load_db_url(_write(tmp_path, SETTINGS.replace("[postgres_db]", "[database]")))


def test_example_yaml_loads(tmp_path: Path):
    example = Path(__file__).resolve().parents[2] / "deploy" / "runboard.example.yaml"
    load_server_config(example, env={"RUNBOARD_SETTINGS": str(_write(tmp_path))})
