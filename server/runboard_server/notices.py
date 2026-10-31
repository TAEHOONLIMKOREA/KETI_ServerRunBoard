"""공지 배너: 모든 화면의 헤더 아래에 한 줄씩 보이는 안내·주의.

PostgreSQL notices 표에 두고 웹 관리 화면(/admin '공지' 탭)과 관리 API 로 고친다. 엔진이 시작할 때와
고칠 때마다 DB 에서 다시 읽어 메모리에 들고, overview 는 그 사본에서 기한이 지나지 않은 것만 내보낸다.
등록 정보(registry.py)와 달리 판정과 무관해서 고쳐도 열린 작업을 다시 판정하지 않는다.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, BeforeValidator, Field

from runboard_server.registry import check_chars

MAX_TEXT = 200
UNTIL_MAX = 4_102_444_800  # 기한의 상한 (2100-01-01). 화면이 날짜로 적을 수 있는 범위 안에 둔다
LEVELS = ("warning", "info")  # 배너 순서. 주의가 안내보다 먼저다

Level = Literal["info", "warning"]


def _one_line(value: object) -> object:
    """앞뒤 공백을 지운다. 배너는 공지마다 한 줄이라 줄바꿈(\\r, \\n, U+2028 등)과 제어 문자는 받지 않는다."""
    if isinstance(value, str):
        value = value.strip()
        if not any(c.isprintable() and not c.isspace() for c in value):  # 비었거나 폭 없는 공백뿐
            raise ValueError("공지 내용을 적는다")
        if len(value.splitlines()) > 1:
            raise ValueError("공지는 한 줄로 적는다. 줄바꿈은 넣지 않는다")
        check_chars(value, "공지")
    return value


NoticeText = Annotated[str, BeforeValidator(_one_line)]


class NoticeFields(BaseModel):
    """공지에서 고칠 수 있는 값. 관리 API 의 입력 검증도 이 모델이 한다."""

    text: NoticeText = Field(max_length=MAX_TEXT, description="배너에 보이는 한 줄")
    level: Level = Field(default="info", description="info(안내) | warning(주의)")
    until: float | None = Field(
        default=None,
        ge=0,
        le=UNTIL_MAX,
        allow_inf_nan=False,
        description="epoch 초. 이 시각이 지나면 배너에서 내려간다 (지난 시각이면 바로). 비우면 지울 때까지",
    )


class NoticeEntry(NoticeFields):
    id: int = Field(description="DB 행 번호")
    created_ts: float = Field(description="등록 시각 (epoch 초). 고쳐도 바뀌지 않는다")

    def shown(self, now: float) -> bool:
        """지금 배너에 보이는가: 기한이 없거나 아직 지나지 않았다."""
        return self.until is None or self.until > now


def banner_order(notice: NoticeEntry) -> tuple[int, float, int]:
    """배너 순서: 주의가 안내보다 먼저, 같은 수준은 먼저 등록한 것부터."""
    return (LEVELS.index(notice.level), notice.created_ts, notice.id)
