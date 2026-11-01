"""서버로 보고를 보낸다. 실패하면 지수 백오프(2초부터 두 배씩, 최대 60초), 과거 데이터는 재전송하지 않는다.

항상 최신 상태만 보낸다. 로컬 버퍼는 없어 실패한 보고는 버린다. 백오프 중에는 ready() 가 False 라서 루프가
보내지 않는다.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

import httpx

from runboard_agent import __version__
from runboard_common.schemas import NodeReport

log = logging.getLogger(__name__)

REPORT_PATH = "/api/v1/report"
MAX_BACKOFF_S = 60.0


class Reporter:
    def __init__(
        self,
        server_url: str,
        token: str,
        timeout: float = 5.0,
        max_backoff: float = MAX_BACKOFF_S,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.server_url = server_url.rstrip("/")
        self.max_backoff = max_backoff
        self.failures = 0
        self.backoff = 0.0
        self.retry_at = 0.0
        self._clock = clock
        self._client = httpx.Client(
            base_url=self.server_url,
            timeout=timeout,
            headers={
                "Authorization": f"Bearer {token}",
                "User-Agent": f"runboard-agent/{__version__}",
                "Content-Type": "application/json",
            },
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def ready(self, now: float | None = None) -> bool:
        """백오프가 끝났으면 True. now 는 clock 과 같은 기준(monotonic 초)이다."""
        return (self._clock() if now is None else now) >= self.retry_at

    def send(self, report: NodeReport) -> bool:
        try:
            response = self._client.post(REPORT_PATH, content=report.model_dump_json())
        except httpx.HTTPError as exc:
            self._on_failure(f"{type(exc).__name__}: {exc}")
            return False

        if response.status_code in (200, 202, 204):
            self._on_success()
            return True

        detail = response.text[:200].replace("\n", " ")
        if response.status_code in (401, 403):
            self._on_failure(f"인증 실패 HTTP {response.status_code}: 토큰을 확인하라. {detail}")
        else:
            self._on_failure(f"HTTP {response.status_code}: {detail}")
        return False

    def _on_success(self) -> None:
        if self.failures:
            log.info("서버 연결 복구 (실패 %d회 후)", self.failures)
        self.failures = 0
        self.backoff = 0.0
        self.retry_at = 0.0

    def _on_failure(self, message: str) -> None:
        self.failures += 1
        self.backoff = min(self.max_backoff, float(2 ** min(self.failures, 6)))
        self.retry_at = self._clock() + self.backoff
        if self.failures == 1 or self.failures % 10 == 0:
            log.warning(
                "보고 실패 (%d회, 다음 재시도 %.0fs 후): %s", self.failures, self.backoff, message
            )
