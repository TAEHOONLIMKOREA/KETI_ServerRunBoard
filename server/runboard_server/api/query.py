"""조회 API (/api/v1). 전부 읽기 전용이다. 이력 조회만 DB 를 읽고 나머지는 엔진의 메모리 상태를 읽는다."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from runboard_server import __version__
from runboard_server.core.engine import Engine
from runboard_server.core.history import RAW_MAX_S, Range, Step
from runboard_server.views import (
    GpuHistoryOut,
    NodeHistoryOut,
    NodeOut,
    OverviewOut,
    UserDetailOut,
    UsersOut,
    WorkloadOut,
    WorkloadsOut,
)

router = APIRouter(prefix="/api/v1", tags=["query"])
# from·to 로 받는 epoch 초의 범위 (1970 ~ 2100). 터무니없이 큰 값은 bucket_ts(BIGINT) 비교에서 DB 오류가 난다
_TS_MAX = 4_102_444_800


def _history(request: Request) -> Engine:
    """이력은 DB 에만 있다."""
    if request.app.state.repo is None:
        raise HTTPException(status_code=503, detail="DB 없이 실행 중이라 이력이 없다")
    return request.app.state.engine


@router.get("/overview", response_model=OverviewOut)
def overview(request: Request) -> OverviewOut:
    return request.app.state.engine.overview()


@router.get("/nodes", response_model=list[NodeOut])
def nodes(request: Request) -> list[NodeOut]:
    return request.app.state.engine.overview().nodes


@router.get("/nodes/{name}", response_model=NodeOut)
def node(name: str, request: Request) -> NodeOut:
    out = request.app.state.engine.node_view(name)
    if out is None:
        raise HTTPException(status_code=404, detail="node not found")
    return out


@router.get("/nodes/{key}/history", response_model=NodeHistoryOut)
def node_history(key: str, request: Request, range: Range = "24h") -> NodeHistoryOut:
    """노드 상세 이력 차트용. 24h 는 5분 288점, 7d 는 1시간 168점. GPU 별로 빈 칸 없는 고정 격자다."""
    out = _history(request).node_history(key, range)
    if out is None:
        raise HTTPException(status_code=404, detail="node not found")
    return out


@router.get("/gpus/{uuid}/history", response_model=GpuHistoryOut)
def gpu_history(
    uuid: str,
    request: Request,
    start: float | None = Query(
        default=None,
        alias="from",
        ge=0,
        le=_TS_MAX,
        allow_inf_nan=False,
        description="epoch 초. 기본은 to 의 24시간 전",
    ),
    end: float | None = Query(
        default=None,
        alias="to",
        ge=0,
        le=_TS_MAX,
        allow_inf_nan=False,
        description="epoch 초. 기본은 지금",
    ),
    step: Step = "5m",
) -> GpuHistoryOut:
    """[from, to) 의 이력. 데이터가 있는 점만 돌려준다. raw 는 24시간 이내만."""
    engine = _history(request)
    end = engine.clock() if end is None else end
    start = end - RAW_MAX_S if start is None else start
    if start >= end:
        raise HTTPException(status_code=422, detail="from 은 to 보다 앞이어야 한다")
    if step == "raw" and end - start > RAW_MAX_S:
        raise HTTPException(
            status_code=422,
            detail="raw 는 24시간 이내만 조회한다. 더 긴 구간은 step=5m 이나 1h 로 본다",
        )
    out = engine.gpu_history(uuid, start, end, step)
    if out is None:
        raise HTTPException(status_code=404, detail="gpu not found")
    return out


@router.get("/workloads", response_model=WorkloadsOut)
def workloads(
    request: Request,
    owner: str | None = Query(default=None, description="username 또는 _unknown"),
    node: str | None = None,
    type: str | None = None,
    project: str | None = None,
    status: str | None = None,
) -> WorkloadsOut:
    return request.app.state.engine.workloads_view(
        owner=owner, node=node, type_=type, project=project, status=status
    )


@router.get("/workloads/{workload_id}", response_model=WorkloadOut)
def workload(workload_id: str, request: Request) -> WorkloadOut:
    out = request.app.state.engine.workload_view(workload_id)
    if out is None:
        raise HTTPException(status_code=404, detail="workload not found")
    return out


@router.get("/users", response_model=UsersOut)
def users(request: Request) -> UsersOut:
    return request.app.state.engine.users_view()


@router.get("/users/{username}", response_model=UserDetailOut)
def user(username: str, request: Request) -> UserDetailOut:
    out = request.app.state.engine.user_view(username)
    if out is None:
        raise HTTPException(status_code=404, detail="user not found")
    return out


@router.get("/healthz")
def healthz(request: Request) -> dict:
    engine = request.app.state.engine
    return {"status": "ok", "version": __version__, "nodes": len(engine.state.nodes)}
