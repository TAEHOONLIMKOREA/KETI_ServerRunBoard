"""적응형 보고 주기. PLAN 4.4.1 의 고정 5초를 대체한다.

GPU 가 동작 중이면 active_interval(기본 1초), 모두 놀고 있으면 idle_interval(기본 30초) 마다 보고한다.
수집은 항상 active_interval 마다 해서, 유휴 노드에서 작업이 시작되면 다음 수집 때 바로 보고한다.
동작이 멈춘 뒤에도 idle_grace 동안은 빠른 주기를 유지한다. 서버가 세션 종료를 N회 연속 누락으로
판정하므로, 느린 주기로 바로 떨어지면 끝난 작업이 화면에 30초 가까이 남기 때문이다.

전부 순수 함수·값 객체라 시계 없이 테스트한다. 시각은 monotonic 초다.
"""

from __future__ import annotations

from dataclasses import dataclass

from runboard_common.schemas import NodeReport


def is_active(report: NodeReport) -> bool:
    """GPU 가 하나라도 동작 중인가. 프로세스가 있거나 사용률이 0 보다 크면 동작 중이다.

    사용률도 보는 이유: 프로세스 목록을 못 얻는 환경(권한, 일부 드라이버)에서도 동작을 놓치지 않기 위해서다.
    """
    return any(gpu.processes or (gpu.util_gpu or 0) > 0 for gpu in report.gpus)


@dataclass
class ReportCadence:
    active_interval: float
    idle_interval: float
    idle_grace: float
    last_active_at: float | None = None
    last_sent_at: float | None = None

    def observe(self, active: bool, now: float) -> None:
        if active:
            self.last_active_at = now

    def fast(self, now: float) -> bool:
        """최근 idle_grace 안에 동작이 관측됐으면 빠른 주기."""
        return self.last_active_at is not None and now - self.last_active_at <= self.idle_grace

    def interval(self, now: float) -> float:
        return self.active_interval if self.fast(now) else self.idle_interval

    def due(self, now: float) -> bool:
        """지금 보낼 때인가. 루프가 active_interval 마다 돌기 때문에 약간의 오차는 봐준다."""
        if self.last_sent_at is None:
            return True
        tolerance = 0.1 * self.active_interval
        return now - self.last_sent_at >= self.interval(now) - tolerance

    def mark_sent(self, now: float) -> None:
        self.last_sent_at = now

    def declared_interval(self, active: bool) -> float:
        """보고에 적어 보내는 '다음 보고까지의 최대 간격'.

        유예(idle_grace) 중이라도 유휴 보고에는 idle_interval 을 적는다. 다음 보고가 30초 뒤에 올 수도
        있다는 뜻이라, 서버가 그 사이를 지연·끊김으로 오판하지 않는다.
        """
        return self.active_interval if active else self.idle_interval
