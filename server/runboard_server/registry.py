"""등록 정보: 사용자·별칭, 공용 서비스 (DB 표 users·services).

PostgreSQL 에 두고 웹 관리 화면(/admin)과 관리 API 로 고친다. 서버는 시작할 때와 고칠 때마다 DB 에서
다시 읽어 소유자 판정(core/owner.py)에 넣는다. 관리 API 의 입력 검증도 이 모델들이 한다.
"""

from __future__ import annotations

import re
import unicodedata
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, Field, field_validator, model_validator

_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_NAME_RULE = "영문·숫자로 시작하고 영문·숫자·. _ - 만 쓴다 (64자 이하)"


def _blank_to_none(value: object) -> object:
    """앞뒤 공백을 지우고 빈 문자열은 None 으로. 웹 폼의 빈 칸이 그대로 들어온다."""
    if isinstance(value, str):
        return value.strip() or None
    return value


Text = Annotated[str | None, BeforeValidator(_blank_to_none)]


def _check_name(value: str, what: str) -> str:
    value = value.strip()
    if not _NAME_RE.match(value):
        raise ValueError(f"{what} '{value}': {_NAME_RULE}")
    return value


def check_chars(value: str, what: str) -> str:
    """제어 문자(탭·NUL 등)를 받지 않는다. 공지와 노드 화면 이름이 쓴다 (화면에 한 줄로 보인다).

    여기 등록 정보 필드에는 걸지 않는다. DB 에서 읽을 때도 같은 모델로 검증하므로 전에 들어간 행이 빠진다.
    그쪽 NUL 은 저장소가 422 로 돌려준다 (Repository._writing). 짝 없는 서로게이트(UTF-8 이 아닌 CLI 인자
    등)는 어느 필드든 pydantic 의 문자열 검증이 막는다.
    """
    if any(unicodedata.category(c) == "Cc" for c in value):
        raise ValueError(f"{what}에 제어 문자(탭 등)는 넣지 않는다")
    return value


class UserFields(BaseModel):
    """사용자에서 고칠 수 있는 값. username 은 작업 이력에 그 문자열로 남아 있어서 바꾸지 않는다."""

    display_name: Text = Field(default=None, max_length=64)
    email: Text = Field(default=None, max_length=128)
    slack_id: Text = Field(default=None, max_length=64)
    aliases: list[str] = Field(default_factory=list, description="호스트 계정명 등 다른 이름")

    @field_validator("aliases")
    @classmethod
    def _aliases(cls, value: list[str]) -> list[str]:
        out: list[str] = []
        for alias in value:
            if not alias.strip():
                continue
            alias = _check_name(alias, "별칭")
            if alias.lower() not in {a.lower() for a in out}:
                out.append(alias)
        return out


class UserEntry(UserFields):
    username: str = Field(
        description="대표 이름. 실행 계정명이 이것이나 별칭과 같으면 이 사용자로 인식된다"
    )

    @field_validator("username")
    @classmethod
    def _username(cls, value: str) -> str:
        return _check_name(value, "사용자 이름")


class ServiceFields(BaseModel):
    name: str = Field(max_length=64)
    match_exec_account: Text = Field(
        default=None, max_length=64, description="이 실행 계정의 프로세스"
    )
    match_container_pattern: Text = Field(
        default=None, max_length=200, description="컨테이너명 정규식"
    )
    match_image_pattern: Text = Field(default=None, max_length=200, description="이미지 정규식")
    owner: Text = Field(default=None, max_length=64, description="담당자 username")
    idle_exempt: bool = True

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("서비스 이름을 적는다")
        return value

    @field_validator("match_container_pattern", "match_image_pattern")
    @classmethod
    def _pattern(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"정규식이 올바르지 않다: {exc}") from None
        return value

    @model_validator(mode="after")
    def _has_rule(self) -> ServiceFields:
        if not (
            self.match_exec_account or self.match_container_pattern or self.match_image_pattern
        ):
            raise ValueError(
                "매칭 규칙(실행 계정, 컨테이너명 패턴, 이미지 패턴)을 하나 이상 적는다"
            )
        return self


class ServiceEntry(ServiceFields):
    id: int = Field(default=0, description="DB 행 번호")


class Registry(BaseModel):
    users: list[UserEntry] = Field(
        default_factory=list, description="이 순서대로 앞 8명이 대시보드 고유색을 받는다"
    )
    services: list[ServiceEntry] = Field(default_factory=list)

    def user(self, username: str) -> UserEntry | None:
        return next((u for u in self.users if u.username == username), None)

    def name_owner(self, name: str) -> str | None:
        """이 이름을 username 이나 별칭으로 쓰는 사용자 (대소문자 무시)."""
        key = name.lower()
        for u in self.users:
            if key == u.username.lower() or key in (a.lower() for a in u.aliases):
                return u.username
        return None
