"""이력 집계 순수 함수: 버킷 경계, 5분 집계, 1시간 재집계, 고정 격자."""

import pytest

from runboard_server.core.history import (
    BUCKET_S,
    HOUR_S,
    Point,
    Sample,
    aggregate,
    bucket_start,
    closed_until,
    fill,
    raw_point,
    rollup,
)

H0 = 1_760_598_000  # 정각. 5분 경계이기도 하다


def test_bucket_start_aligns_down_to_size():
    assert bucket_start(H0) == H0
    assert bucket_start(H0 + 299.9) == H0
    assert bucket_start(H0 + 300) == H0 + 300
    assert bucket_start(H0 + 3599, HOUR_S) == H0
    assert isinstance(bucket_start(H0 + 0.5), int), "bucket_ts 는 정수 열이다"


def test_closed_until_waits_for_grace():
    assert closed_until(H0 + 300 + 59, BUCKET_S, 60) == H0, "끝나고 60초가 지나기 전에는 열려 있다"
    assert closed_until(H0 + 300 + 60, BUCKET_S, 60) == H0 + 300
    assert closed_until(H0 + 3600 + 60, HOUR_S, 60) == H0 + 3600
    assert closed_until(H0 + 3600 + 59, HOUR_S, 60) == H0, "1시간 구간도 마지막 5분이 닫혀야 닫힌다"


def test_aggregate_ignores_none_per_metric():
    samples = [
        Sample(ts=H0, util_gpu=10, mem_used_mb=1000, temp_c=50, power_w=100.0, occupied=True),
        Sample(ts=H0 + 30, util_gpu=30, temp_c=60, occupied=False),
        Sample(ts=H0 + 60, mem_used_mb=3000, power_w=200.0),
    ]
    assert aggregate(H0, samples) == Point(
        ts=H0,
        util_avg=20.0,
        util_max=30,
        mem_avg_mb=2000.0,
        mem_max_mb=3000,
        temp_max=60,
        power_avg=150.0,
        occupied_ratio=0.5,
        samples=3,
    ), "지표마다 값이 있는 샘플만 쓴다. samples 는 전체 수"


def test_aggregate_all_none_stays_none():
    point = aggregate(H0, [Sample(ts=H0), Sample(ts=H0 + 30)])
    assert point == Point(ts=H0, samples=2), "수집 불가는 0 이 아니라 None"


def test_raw_point_uses_the_sample_value_for_avg_and_max():
    point = raw_point(Sample(ts=H0 + 12.5, util_gpu=7, mem_used_mb=512, occupied=True))
    assert (point.ts, point.util_avg, point.util_max) == (H0 + 12.5, 7, 7)
    assert (point.mem_avg_mb, point.mem_max_mb, point.occupied_ratio) == (512, 512, 1.0)
    assert point.temp_max is None and point.power_avg is None
    assert raw_point(Sample(ts=H0, occupied=False)).occupied_ratio == 0.0


def test_rollup_weights_averages_by_samples():
    rows = [
        Point(
            ts=H0,
            util_avg=10.0,
            util_max=20,
            mem_avg_mb=100.0,
            mem_max_mb=150,
            temp_max=50,
            power_avg=100.0,
            occupied_ratio=1.0,
            samples=10,
        ),
        Point(
            ts=H0 + 300,
            util_avg=40.0,
            util_max=90,
            temp_max=70,
            power_avg=200.0,
            occupied_ratio=0.0,
            samples=5,
        ),
        Point(ts=H0 + HOUR_S + 600, samples=2),
    ]
    first, second = rollup(rows, HOUR_S)
    assert first.ts == H0 and first.samples == 15
    assert first.util_avg == pytest.approx((10.0 * 10 + 40.0 * 5) / 15)
    assert first.power_avg == pytest.approx((100.0 * 10 + 200.0 * 5) / 15)
    assert first.occupied_ratio == pytest.approx(10 / 15)
    assert (first.util_max, first.temp_max) == (90, 70), "최댓값은 최댓값"
    assert (first.mem_avg_mb, first.mem_max_mb) == (100.0, 150), "None 인 행은 그 지표에서 뺀다"
    assert second == Point(ts=H0 + HOUR_S, samples=2), "전부 None 이면 None"


def test_rollup_orders_by_time_and_skips_empty_hours():
    rows = [Point(ts=H0 + 3 * HOUR_S, samples=1), Point(ts=H0, samples=1)]
    assert [p.ts for p in rollup(rows, HOUR_S)] == [H0, H0 + 3 * HOUR_S]
    assert rollup([], HOUR_S) == []


def test_fill_makes_a_fixed_grid():
    data = Point(ts=H0 + 300, util_avg=5.0, samples=1)
    outside = Point(ts=H0 + 900, util_avg=9.0, samples=1)
    grid = fill([data, outside], H0, BUCKET_S, 3)
    assert [p.ts for p in grid] == [H0, H0 + 300, H0 + 600]
    assert grid[1] == data
    assert grid[0] == Point(ts=H0) and grid[2] == Point(ts=H0 + 600), "빈 칸은 값이 None 인 점"
