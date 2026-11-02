"""에이전트 설정. 우선순위: 기본값 < TOML 파일 < 환경변수 < CLI 인자.

보고 주기는 적응형이다 (cadence.py):
- active_interval: GPU 가 동작 중(프로세스가 있거나 사용률 > 0)일 때의 보고 주기. 수집 주기이기도 하다
- idle_interval:   GPU 가 모두 놀고 있을 때의 보고 주기
- idle_grace:      동작이 멈춘 뒤에도 이 시간 동안은 active_interval 로 보고한다
"interval"(환경변수 RUNBOARD_INTERVAL) 은 예전 이름으로 active_interval 과 같다. 파일·환경변수·CLI 마다 먼저
새 이름으로 바꾼 뒤 합치므로 우선순위도 같다 (환경변수 RUNBOARD_INTERVAL 이 파일의 active_interval 을 이긴다).
한 곳에 둘 다 있으면 active_interval 을 쓴다.
node_id 는 재부팅·호스트명 변경 뒤에도 같은 노드로 인식되게 하는 고정 식별자다. 설치 스크립트가 UUID 를 써 준다.
"""

from __future__ import annotations

import os
import socket
import sys
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

DEFAULT_CONFIG_PATH = Path("/etc/runboard/agent.toml")

ENV_KEYS: dict[str, str] = {
    "RUNBOARD_SERVER": "server_url",
    "RUNBOARD_TOKEN": "token",
    "RUNBOARD_NODE_NAME": "node_name",
    "RUNBOARD_NODE_ID": "node_id",
    "RUNBOARD_ACTIVE_INTERVAL": "active_interval",
    "RUNBOARD_IDLE_INTERVAL": "idle_interval",
    "RUNBOARD_IDLE_GRACE": "idle_grace",
    "RUNBOARD_INTERVAL": "interval",  # 예전 이름 = active_interval
    "RUNBOARD_TIMEOUT": "timeout",
    "RUNBOARD_COLLECTOR": "collector",
}

COLLECTORS = ("nvml", "fake")


@dataclass(frozen=True)
class FakeOptions:
    gpu_count: int = 8
    churn: bool = True
    boot_id: str | None = None


@dataclass(frozen=True)
class AgentConfig:
    server_url: str | None = None
    token: str | None = None
    node_name: str = field(default_factory=socket.gethostname)
    node_id: str | None = None
    active_interval: float = 1.0
    idle_interval: float = 30.0
    idle_grace: float = 5.0
    timeout: float = 5.0
    collector: str = "nvml"
    fake: FakeOptions = field(default_factory=FakeOptions)

    def validate(self) -> None:
        for name in ("active_interval", "idle_interval", "timeout"):
            value = getattr(self, name)
            if value <= 0:
                raise ValueError(f"{name} 은 0 보다 커야 한다: {value}")
        if self.idle_grace < 0:
            raise ValueError(f"idle_grace 는 0 이상이어야 한다: {self.idle_grace}")
        if self.idle_interval < self.active_interval:
            raise ValueError(
                f"idle_interval({self.idle_interval}) 은 "
                f"active_interval({self.active_interval}) 보다 짧을 수 없다"
            )
        if self.collector not in COLLECTORS:
            raise ValueError(f"collector 는 {COLLECTORS} 중 하나: {self.collector}")
        if self.fake.gpu_count < 0:
            raise ValueError("fake.gpu_count 는 0 이상")

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> AgentConfig:
        data = _resolve_legacy(data)
        fake_raw = dict(data.get("fake") or {})
        fake = FakeOptions(
            gpu_count=int(fake_raw.get("gpu_count", FakeOptions.gpu_count)),
            churn=_as_bool(fake_raw.get("churn", FakeOptions.churn)),
            boot_id=fake_raw.get("boot_id"),
        )
        kwargs: dict[str, Any] = {"fake": fake}
        for key in ("server_url", "token", "node_name", "node_id", "collector"):
            if data.get(key) is not None:
                kwargs[key] = str(data[key])
        for key in ("active_interval", "idle_interval", "idle_grace", "timeout"):
            if data.get(key) is not None:
                kwargs[key] = float(data[key])
        cfg = cls(**kwargs)
        cfg.validate()
        return cfg


def _resolve_legacy(layer: Mapping[str, Any]) -> dict[str, Any]:
    """예전 이름 interval 을 active_interval 로 바꾼다. 한 층에 둘 다 있으면 active_interval 을 쓴다."""
    out = dict(layer)
    legacy = out.pop("interval", None)
    if legacy is not None and out.get("active_interval") is None:
        out["active_interval"] = legacy
    return out


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _deep_merge(base: dict[str, Any], extra: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(base)
    for key, value in extra.items():
        if value is None:
            continue
        if isinstance(value, Mapping) and isinstance(out.get(key), Mapping):
            out[key] = _deep_merge(dict(out[key]), value)
        else:
            out[key] = value
    return out


def load_config(
    path: Path | None = None,
    env: Mapping[str, str] | None = None,
    overrides: Mapping[str, Any] | None = None,
) -> AgentConfig:
    """설정을 합쳐 AgentConfig 를 만든다.

    path 가 None 이면 기본 경로가 존재할 때만 읽는다. 명시된 경로가 없으면 오류다.
    overrides 는 CLI 인자. None 값은 "지정 안 함" 으로 보고 무시한다.
    """
    env = os.environ if env is None else env
    merged: dict[str, Any] = {}

    if path is None:
        if DEFAULT_CONFIG_PATH.exists():
            path = DEFAULT_CONFIG_PATH
    elif not path.exists():
        raise FileNotFoundError(f"설정 파일이 없다: {path}")
    # 예전 이름 interval 은 층마다 먼저 바꾼다. 합친 뒤에 바꾸면 파일의 active_interval 이 환경변수 interval 을 이긴다
    if path is not None:
        toml_values = tomllib.loads(path.read_text(encoding="utf-8"))
        merged = _deep_merge(merged, _resolve_legacy(toml_values))

    env_values = {ENV_KEYS[k]: v for k, v in env.items() if k in ENV_KEYS}
    merged = _deep_merge(merged, _resolve_legacy(env_values))

    if overrides:
        merged = _deep_merge(merged, _resolve_legacy(overrides))

    return AgentConfig.from_dict(merged)
