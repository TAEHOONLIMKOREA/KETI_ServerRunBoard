"""관리 API (/api/v1/admin). 웹 관리 화면(/admin)과 runboard-server nodes 명령(cli.py)이 쓴다.

사용자·계정 매핑·공용 서비스는 DB 에 쓰고 다시 읽어 엔진에 넣는다. DB 가 원본이고 엔진은 그 사본으로 판정한다.
공지도 DB 에 쓰고 다시 읽지만 판정과 무관하므로 등록 정보와 따로 넣는다 (작업을 다시 판정하지 않는다).
노드는 이미 DB 에 있으므로 목록, 화면 이름 변경과 삭제만 한다.
로그인이 없다. 대시보드처럼 주소를 아는 누구나 고칠 수 있다 (README '운영 배포').
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, Request, Response
from pydantic import BaseModel, Field, field_validator

from runboard_server.errors import Conflict
from runboard_server.notices import NoticeEntry, NoticeFields
from runboard_server.registry import (
    MAX_UID,
    AccountMapping,
    MappingFields,
    Registry,
    ServiceEntry,
    ServiceFields,
    UserEntry,
    UserFields,
    check_chars,
)
from runboard_server.views import NodeBrief, NodeOut

router = APIRouter(prefix="/api/v1/admin", tags=["admin"])

# 경로의 행 번호. MAX_UID 는 DB 정수 열의 최댓값이다. 그 밖의 값은 PostgreSQL 이 비교도 못 해 500 이 된다
RowId = Annotated[int, Path(ge=1, le=MAX_UID, description="DB 행 번호")]


class UserOrder(BaseModel):
    usernames: list[str] = Field(
        description="사용자 전체를 새 순서대로. 앞 8명이 대시보드 고유색을 받는다"
    )


class NodeRename(BaseModel):
    display_name: str = Field(max_length=64)

    @field_validator("display_name")
    @classmethod
    def _display_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("화면 이름을 적는다")
        return check_chars(value, "화면 이름")


def _repo(request: Request):
    repo = request.app.state.repo
    if repo is None:
        raise HTTPException(status_code=503, detail="DB 없이 실행 중이라 고칠 수 없다")
    return repo


def _registry(request: Request) -> Registry:
    return request.app.state.engine.registry


def _reload(request: Request) -> Registry:
    registry = _repo(request).load_registry()
    request.app.state.engine.set_registry(registry)
    return registry


def _not_found(subject: str, key: object) -> HTTPException:
    """subject 는 조사까지: '사용자가', '계정 매핑이'."""
    return HTTPException(status_code=404, detail=f"{subject} 없다: {key}")


def _require_user(registry: Registry, username: str | None) -> None:
    if username is not None and registry.user(username) is None:
        raise HTTPException(status_code=422, detail=f"등록되지 않은 사용자다: {username}")


def _check_names(registry: Registry, username: str, names: list[str]) -> None:
    """이름·별칭은 사용자끼리 겹치면 안 된다 (대소문자 무시). 겹치면 판정이 어느 쪽인지 알 수 없다."""
    for name in names:
        owner = registry.name_owner(name)
        if owner is not None and owner != username:
            raise Conflict(f"{owner} 사용자가 이미 쓰는 이름·별칭이다: {name}")


# ---------------------------------------------------------------------- 사용자


@router.get("/users", response_model=list[UserEntry])
def list_users(request: Request) -> list[UserEntry]:
    return _registry(request).users


@router.post("/users", response_model=UserEntry, status_code=201)
def create_user(user: UserEntry, request: Request) -> UserEntry:
    repo = _repo(request)
    registry = _registry(request)
    if registry.user(user.username) is not None:
        raise Conflict(f"이미 등록된 사용자다: {user.username}")
    _check_names(registry, user.username, [user.username, *user.aliases])
    repo.add_user(user)
    _reload(request)
    return user


@router.put("/users/{username}", response_model=UserEntry)
def update_user(username: str, fields: UserFields, request: Request) -> UserEntry:
    repo = _repo(request)
    _check_names(_registry(request), username, fields.aliases)
    if not repo.update_user(username, fields):
        raise _not_found("사용자가", username)
    _reload(request)
    return UserEntry(username=username, **fields.model_dump())


@router.delete("/users/{username}", status_code=204, response_class=Response)
def delete_user(username: str, request: Request) -> Response:
    """계정 매핑도 같이 지워지고 담당 서비스는 담당자가 비워진다. 작업 이력의 소유자 이름은 남는다."""
    if not _repo(request).delete_user(username):
        raise _not_found("사용자가", username)
    _reload(request)
    return Response(status_code=204)


@router.put("/user-order", response_model=list[UserEntry])
def reorder_users(order: UserOrder, request: Request) -> list[UserEntry]:
    if not _repo(request).reorder_users(order.usernames):
        raise Conflict("사용자 목록이 지금과 다르다. 새로 읽고 다시 한다")
    return _reload(request).users


# ---------------------------------------------------------------------- 계정 매핑


def _check_mapping(registry: Registry, mapping: MappingFields, mapping_id: int | None) -> None:
    _require_user(registry, mapping.username)
    for other in registry.account_mappings:
        if other.id != mapping_id and (other.node, other.uid) == (mapping.node, mapping.uid):
            where = mapping.node or "모든 노드"
            raise Conflict(
                f"같은 노드·uid 매핑이 이미 있다: {where}, uid {mapping.uid} → {other.username}"
            )


@router.get("/account-mappings", response_model=list[AccountMapping])
def list_mappings(request: Request) -> list[AccountMapping]:
    return _registry(request).account_mappings


@router.post("/account-mappings", response_model=AccountMapping, status_code=201)
def create_mapping(mapping: MappingFields, request: Request) -> AccountMapping:
    repo = _repo(request)
    _check_mapping(_registry(request), mapping, None)
    mapping_id = repo.add_mapping(mapping)
    _reload(request)
    return AccountMapping(id=mapping_id, **mapping.model_dump())


@router.put("/account-mappings/{mapping_id}", response_model=AccountMapping)
def update_mapping(mapping_id: RowId, mapping: MappingFields, request: Request) -> AccountMapping:
    repo = _repo(request)
    _check_mapping(_registry(request), mapping, mapping_id)
    if not repo.update_mapping(mapping_id, mapping):
        raise _not_found("계정 매핑이", mapping_id)
    _reload(request)
    return AccountMapping(id=mapping_id, **mapping.model_dump())


@router.delete("/account-mappings/{mapping_id}", status_code=204, response_class=Response)
def delete_mapping(mapping_id: RowId, request: Request) -> Response:
    if not _repo(request).delete_mapping(mapping_id):
        raise _not_found("계정 매핑이", mapping_id)
    _reload(request)
    return Response(status_code=204)


# ---------------------------------------------------------------------- 공용 서비스


def _check_service(registry: Registry, service: ServiceFields, service_id: int | None) -> None:
    _require_user(registry, service.owner)
    for other in registry.services:
        if other.id != service_id and other.name.lower() == service.name.lower():
            raise Conflict(f"같은 이름의 서비스가 이미 있다: {other.name}")


@router.get("/services", response_model=list[ServiceEntry])
def list_services(request: Request) -> list[ServiceEntry]:
    return _registry(request).services


@router.post("/services", response_model=ServiceEntry, status_code=201)
def create_service(service: ServiceFields, request: Request) -> ServiceEntry:
    repo = _repo(request)
    _check_service(_registry(request), service, None)
    service_id = repo.add_service(service)
    _reload(request)
    return ServiceEntry(id=service_id, **service.model_dump())


@router.put("/services/{service_id}", response_model=ServiceEntry)
def update_service(service_id: RowId, service: ServiceFields, request: Request) -> ServiceEntry:
    repo = _repo(request)
    _check_service(_registry(request), service, service_id)
    if not repo.update_service(service_id, service):
        raise _not_found("서비스가", service_id)
    _reload(request)
    return ServiceEntry(id=service_id, **service.model_dump())


@router.delete("/services/{service_id}", status_code=204, response_class=Response)
def delete_service(service_id: RowId, request: Request) -> Response:
    if not _repo(request).delete_service(service_id):
        raise _not_found("서비스가", service_id)
    _reload(request)
    return Response(status_code=204)


# ---------------------------------------------------------------------- 공지


def _reload_notices(request: Request) -> None:
    request.app.state.engine.set_notices(_repo(request).load_notices())


@router.get("/notices", response_model=list[NoticeEntry])
def list_notices(request: Request) -> list[NoticeEntry]:
    """기한이 지난 것까지 전부, 배너 순서. 대시보드(overview)에는 기한 전인 것만 나간다."""
    return request.app.state.engine.notices


@router.post("/notices", response_model=NoticeEntry, status_code=201)
def create_notice(notice: NoticeFields, request: Request) -> NoticeEntry:
    created = _repo(request).add_notice(notice, request.app.state.engine.clock())
    _reload_notices(request)
    return created


@router.put("/notices/{notice_id}", response_model=NoticeEntry)
def update_notice(notice_id: RowId, notice: NoticeFields, request: Request) -> NoticeEntry:
    """일찍 내리려면 until 을 지난 시각으로 저장한다."""
    updated = _repo(request).update_notice(notice_id, notice)
    if updated is None:
        raise _not_found("공지가", notice_id)
    _reload_notices(request)
    return updated


@router.delete("/notices/{notice_id}", status_code=204, response_class=Response)
def delete_notice(notice_id: RowId, request: Request) -> Response:
    if not _repo(request).delete_notice(notice_id):
        raise _not_found("공지가", notice_id)
    _reload_notices(request)
    return Response(status_code=204)


# ---------------------------------------------------------------------- 노드


@router.get("/nodes", response_model=list[NodeBrief])
def list_nodes(request: Request) -> list[NodeBrief]:
    """화면 이름·식별자·연결 상태만 담은 목록. 대시보드와 같은 순서다."""
    return request.app.state.engine.node_list()


@router.patch("/nodes/{key}", response_model=NodeOut)
def rename_node(key: str, body: NodeRename, request: Request) -> NodeOut:
    out = request.app.state.engine.rename_node(key, body.display_name)
    if out is None:
        raise _not_found("노드가", key)
    return out


@router.delete("/nodes/{key}", status_code=204, response_class=Response)
def delete_node(key: str, request: Request) -> Response:
    """끊긴 노드만 지운다. 그 에이전트가 다시 보고하면 새 Server_n 으로 다시 등록된다."""
    if not request.app.state.engine.delete_node(key):
        raise _not_found("노드가", key)
    return Response(status_code=204)
