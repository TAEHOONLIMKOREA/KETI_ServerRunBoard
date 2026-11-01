"""GPU 이력 집계 (gpu_samples_5m). 전부 순수 함수다.

원본 샘플(gpu_samples, persist_interval 마다)을 GPU·5분 버킷별로 묶어 한 행으로 만들고(aggregate),
7일 이력은 5분 행을 1시간으로 다시 묶는다(rollup). 다시 묶을 때 평균은 샘플 수로 가중한다.
지표마다 None 은 빼고 계산하고, 전부 None 이면 None 이다. 수집 불가는 0 이 아니다.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Literal

BUCKET_S = 300  # 5분 집계 버킷
HOUR_S = 3600
DAY_S = 86400
RAW_MAX_S = DAY_S  # 원본 조회는 24시간 이내만. 더 길면 5분·1시간 집계를 본다

Step = Literal["raw", "5m", "1h"]
Range = Literal["24h", "7d"]
STEP_S: dict[str, int] = {"5m": BUCKET_S, "1h": HOUR_S}
# 노드 이력 범위 → (점 간격, 점 수). 24시간은 5분 288점, 7일은 1시간 168점
RANGES: dict[str, tuple[int, int]] = {"24h": (BUCKET_S, 288), "7d": (HOUR_S, 168)}


@dataclass(frozen=True, slots=True)
class Sample:
    """원본 샘플 하나. 이름은 gpu_samples 열과 같다. occupied 는 저장 시점에 열린 세션이 있었는가."""

    ts: float
    util_gpu: int | None = None
    mem_used_mb: int | None = None
    temp_c: int | None = None
    power_w: float | None = None
    occupied: bool | None = None


@dataclass(frozen=True, slots=True)
class Point:
    """구간 하나의 집계. ts 는 구간 시작이고 나머지 이름은 gpu_samples_5m 열과 같다.

    5분 행과 1시간 점이 같은 모양이다. samples 는 묶은 원본 샘플 수로, 다시 묶을 때 가중치다.
    """

    ts: float
    util_avg: float | None = None
    util_max: int | None = None
    mem_avg_mb: float | None = None
    mem_max_mb: int | None = None
    temp_max: int | None = None
    power_avg: float | None = None
    occupied_ratio: float | None = None
    samples: int = 0


def bucket_start(ts: float, size: int = BUCKET_S) -> int:
    """ts 가 든 구간의 시작. size 의 배수다."""
    return int(ts // size) * size


def closed_until(now: float, size: int, grace: float) -> int:
    """now 에 닫혀 있는 마지막 구간의 끝. 그 앞 구간은 전부 닫혔다.

    구간은 끝나고 grace 가 더 지나야 닫힌다. 측정 시각이 그만큼 늦게 찍힌 샘플도 제 구간에 들어간다.
    """
    return bucket_start(now - grace, size)


def _mean(values: Sequence[float]) -> float | None:
    return sum(values) / len(values) if values else None


def aggregate(ts: float, samples: Iterable[Sample]) -> Point:
    """한 GPU·한 구간의 원본 샘플 → 집계 점 하나. ts 는 구간 시작."""
    samples = list(samples)
    utils = [s.util_gpu for s in samples if s.util_gpu is not None]
    mems = [s.mem_used_mb for s in samples if s.mem_used_mb is not None]
    temps = [s.temp_c for s in samples if s.temp_c is not None]
    powers = [s.power_w for s in samples if s.power_w is not None]
    occupied = [1.0 if s.occupied else 0.0 for s in samples if s.occupied is not None]
    return Point(
        ts=ts,
        util_avg=_mean(utils),
        util_max=max(utils, default=None),
        mem_avg_mb=_mean(mems),
        mem_max_mb=max(mems, default=None),
        temp_max=max(temps, default=None),
        power_avg=_mean(powers),
        occupied_ratio=_mean(occupied),
        samples=len(samples),
    )


def raw_point(sample: Sample) -> Point:
    """원본 샘플 하나를 점으로. 평균과 최댓값이 그 샘플 값이다."""
    return aggregate(sample.ts, [sample])


def _weighted(pairs: Iterable[tuple[float | None, int]]) -> float | None:
    """(값, 샘플 수) 의 가중 평균. 값이 None 인 것은 뺀다."""
    kept = [(value, weight) for value, weight in pairs if value is not None and weight > 0]
    total = sum(weight for _, weight in kept)
    return sum(value * weight for value, weight in kept) / total if total else None


def _merge(ts: int, points: list[Point]) -> Point:
    def avg(name: str) -> float | None:
        return _weighted((getattr(p, name), p.samples) for p in points)

    def peak(name: str) -> int | None:
        return max((v for p in points if (v := getattr(p, name)) is not None), default=None)

    return Point(
        ts=ts,
        util_avg=avg("util_avg"),
        util_max=peak("util_max"),
        mem_avg_mb=avg("mem_avg_mb"),
        mem_max_mb=peak("mem_max_mb"),
        temp_max=peak("temp_max"),
        power_avg=avg("power_avg"),
        occupied_ratio=avg("occupied_ratio"),
        samples=sum(p.samples for p in points),
    )


def rollup(points: Iterable[Point], size: int) -> list[Point]:
    """점들을 size 구간으로 다시 묶는다 (5분 → 1시간). 데이터가 있는 구간만, 시각 순."""
    groups: dict[int, list[Point]] = {}
    for p in points:
        groups.setdefault(bucket_start(p.ts, size), []).append(p)
    return [_merge(ts, group) for ts, group in sorted(groups.items())]


def fill(points: Iterable[Point], start: int, size: int, count: int) -> list[Point]:
    """start 부터 size 간격으로 count 칸인 고정 격자. 데이터가 없는 칸은 값이 None 인 점, 격자 밖 점은 버린다."""
    by_ts = {p.ts: p for p in points}
    return [by_ts.get(ts) or Point(ts=ts) for ts in range(start, start + size * count, size)]
