"""조회 API (PLAN 6.2). 전부 읽기 전용이다."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request

from runboard_server import __version__
from runboard_server.views import (
    NodeOut,
    OverviewOut,
    UserDetailOut,
    UsersOut,
    WorkloadOut,
    WorkloadsOut,
)

router = APIRouter(prefix="/api/v1", tags=["query"])


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
