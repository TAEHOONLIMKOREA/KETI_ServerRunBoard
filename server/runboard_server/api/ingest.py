"""POST /api/v1/report — 에이전트 보고 수신."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Request, Response

from runboard_common.schemas import NodeReport
from runboard_server.api.auth import bearer_token, verify_node_token

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api/v1", tags=["ingest"])


@router.post("/report", status_code=204, response_class=Response)
def post_report(
    report: NodeReport, request: Request, authorization: str | None = Header(default=None)
) -> Response:
    cfg = request.app.state.config
    token = bearer_token(authorization)
    if not verify_node_token(cfg.auth, report.node.name, token, report.node.node_id):
        log.warning("인증 실패: node=%s", report.node.name)
        raise HTTPException(status_code=401, detail="invalid node token")
    request.app.state.engine.ingest(report)
    return Response(status_code=204)
