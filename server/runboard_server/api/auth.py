"""에이전트 토큰 검증. 노드별 토큰은 평문 또는 'sha256:<hex>', 등록 토큰은 평문. 비교는 상수 시간."""

from __future__ import annotations

import hashlib
import hmac

from runboard_server.config import AuthSection


def bearer_token(authorization: str | None) -> str:
    if not authorization:
        return ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer":
        return ""
    return token.strip()


def _matches(expected: str, token: str) -> bool:
    if expected.startswith("sha256:"):
        digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
        return hmac.compare_digest(expected[len("sha256:") :].lower(), digest)
    return hmac.compare_digest(expected, token)


def verify_node_token(
    auth: AuthSection, node_name: str, token: str, node_id: str | None = None
) -> bool:
    """노드별 토큰(노드 이름 또는 node_id 로 등록)이나 공용 등록 토큰 중 하나가 맞으면 통과."""
    if not token:
        return False
    for name in (node_name, node_id):
        expected = auth.node_tokens.get(name) if name else None
        if expected and _matches(expected, token):
            return True
    return bool(auth.shared_token) and hmac.compare_digest(auth.shared_token or "", token)
