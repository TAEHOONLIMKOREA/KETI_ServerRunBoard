"""API 가 409·422 로 돌려주는 예외. 엔진·저장소가 HTTP 를 모르게 여기 둔다."""


class Conflict(Exception):
    """요청이 지금 상태와 맞지 않는다 (이미 있는 이름, 보고 중인 노드 삭제 등)."""


class Invalid(Exception):
    """입력 검증은 지났지만 DB 가 받지 않는 값이다 (문자열의 NUL 등). 422 로 돌려준다."""
