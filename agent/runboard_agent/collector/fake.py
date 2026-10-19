"""가짜 수집기. GPU 없는 PC 에서 서버·UI 를 개발하고, 테스트 픽스처로 쓴다.

시나리오는 PLAN 4.2 의 유형·소유자 판정 경로를 모두 지나가도록 짰다.

| GPU | 내용 | 기대 판정 |
|---|---|---|
| 0, 1 | 컨테이너 taehoon_torch 안의 DDP 학습 2프로세스 (root, 라벨 runboard.owner=taehoon) | container, 작업 1개 |
| 2 | 호스트 계정 minsu 의 jupyter. 메모리만 잡고 사용률 0% | personal, 유휴 |
| 3 | ollama serve (계정 ollama). 프로세스별 사용률 미지원 | shared_service |
| 4 | 90초 주기로 40초 동안 짧은 작업이 생겼다 사라짐 (churn) | personal, 세션 종료 테스트 |
| 5 | 호스트 계정 jihye 의 train.py + DataLoader 워커 (같은 pgid) | personal, 작업 1개 세션 2개 |
| 6 | 컨테이너 안 root, 라벨 없음, 컨테이너명 규칙에도 안 맞음 | 미확인 |
| 7 | 빈 GPU | free |

gpu_count 가 8 보다 작으면 앞에서부터 그만큼만 만든다.
"""

from __future__ import annotations

import math
import time
import uuid

from runboard_agent import __version__
from runboard_common.schemas import (
    ContainerInfo,
    GpuReport,
    NodeInfo,
    NodeReport,
    ProcessReport,
    ReportError,
)

_NAMESPACE = uuid.UUID("3d2a9f1e-5c6b-4e7d-8a9b-0c1d2e3f4a5b")
_GPU_NAME = "NVIDIA A100 80GB PCIe (fake)"
_MEM_TOTAL = 81920
_EPOCH = 1_760_572_800  # 2026-10-16 00:00 UTC. 프로세스 시작 시각의 기준
CHURN_PERIOD_S = 90
CHURN_ALIVE_S = 40


def stable_boot_id(node_name: str) -> str:
    """에이전트를 재시작해도 같은 부팅 ID. 재부팅을 흉내 내려면 fake.boot_id 를 바꾼다."""
    return str(uuid.uuid5(_NAMESPACE, f"runboard-fake:{node_name}"))


def _wave(now: float, base: int, amp: int, period: float, phase: float = 0.0) -> int:
    value = base + amp * math.sin(2 * math.pi * now / period + phase)
    return max(0, min(100, round(value)))


class FakeCollector:
    def __init__(
        self,
        node_name: str,
        gpu_count: int = 8,
        churn: bool = True,
        boot_id: str | None = None,
        clock=time.time,
    ) -> None:
        self.node_name = node_name
        self.gpu_count = max(0, min(8, gpu_count))
        self.churn = churn
        self.boot_id = boot_id or stable_boot_id(node_name)
        self._clock = clock

    def close(self) -> None:
        return None

    def collect(self) -> NodeReport:
        now = self._clock()
        gpus = [self._gpu(i, now) for i in range(self.gpu_count)]
        errors: list[ReportError] = []
        if self.gpu_count > 3:
            errors.append(ReportError(scope="gpu:3", item="process_util", code="NOT_SUPPORTED"))
        return NodeReport(
            node=NodeInfo(
                name=self.node_name,
                ip="10.0.0.11",
                boot_id=self.boot_id,
                driver_version="550.54.15",
                cuda_version="12.4",
                agent_version=__version__,
                measured_ts=now,
                cpu_percent=float(_wave(now, 20, 10, 120)),
                mem_used_mb=51200,
                mem_total_mb=262144,
            ),
            gpus=gpus,
            errors=errors,
        )

    def _gpu(self, index: int, now: float) -> GpuReport:
        builders = {
            0: self._ddp_gpu,
            1: self._ddp_gpu,
            2: self._idle_jupyter_gpu,
            3: self._ollama_gpu,
            4: self._churn_gpu,
            5: self._jihye_gpu,
            6: self._unknown_container_gpu,
            7: self._free_gpu,
        }
        return builders[index](index, now)

    @staticmethod
    def _uuid(index: int) -> str:
        return f"GPU-fake-{index:02d}-0000-0000-000000000000"

    def _base(self, index: int, mem_used: int, util: int | None, temp: int) -> GpuReport:
        return GpuReport(
            index=index,
            uuid=self._uuid(index),
            name=_GPU_NAME,
            mem_total_mb=_MEM_TOTAL,
            mem_used_mb=mem_used,
            util_gpu=util,
            util_mem=None if util is None else max(0, util - 20),
            temp_c=temp,
            power_w=60.0 + (util or 0) * 2.4,
            power_limit_w=300.0,
        )

    def _ddp_gpu(self, index: int, now: float) -> GpuReport:
        util = _wave(now, 85, 10, 40, phase=index)
        gpu = self._base(index, 65_000, util, 68)
        gpu.processes.append(
            ProcessReport(
                pid=4001 + index,
                ppid=4000,
                pgid=4000,
                mem_used_mb=64_500,
                util_sm=util,
                exec_username="root",
                exec_uid=0,
                cmdline=f"python -m torch.distributed.run train.py --cfg configs/seg.yaml --rank {index}",
                proc_start_ts=_EPOCH + 3600 * 3,
                container=ContainerInfo(
                    id="ab12cd34ef5601234567890123456789012345678901234567890123456789ab",
                    name="taehoon_torch",
                    image="pytorch/pytorch:2.4.0-cuda12.4-cudnn9-devel",
                    labels={
                        "runboard.owner": "taehoon",
                        "runboard.project": "pbf-defect",
                        "runboard.job": "seg-train",
                    },
                ),
            )
        )
        return gpu

    def _idle_jupyter_gpu(self, index: int, now: float) -> GpuReport:
        gpu = self._base(index, 30_200, 0, 38)
        gpu.processes.append(
            ProcessReport(
                pid=3100,
                ppid=3099,
                pgid=3099,
                mem_used_mb=30_000,
                util_sm=0,
                exec_username="minsu",
                exec_uid=1002,
                cmdline="/home/minsu/.venv/bin/python -m ipykernel_launcher -f kernel-7.json",
                proc_start_ts=_EPOCH - 3600 * 9,
                container=None,
            )
        )
        return gpu

    def _ollama_gpu(self, index: int, now: float) -> GpuReport:
        util = _wave(now, 4, 4, 25)
        gpu = self._base(index, 36_800, util, 41)
        gpu.processes.append(
            ProcessReport(
                pid=2210,
                ppid=1,
                pgid=2210,
                mem_used_mb=36_500,
                util_sm=None,
                exec_username="ollama",
                exec_uid=998,
                cmdline="/usr/local/bin/ollama serve",
                proc_start_ts=_EPOCH - 86400 * 2,
                container=None,
            )
        )
        return gpu

    def _churn_gpu(self, index: int, now: float) -> GpuReport:
        cycle = int(now // CHURN_PERIOD_S)
        alive = self.churn and (now % CHURN_PERIOD_S) < CHURN_ALIVE_S
        if not alive:
            return self._base(index, 300, 0, 35)
        util = _wave(now, 60, 20, 15)
        gpu = self._base(index, 12_000, util, 55)
        gpu.processes.append(
            ProcessReport(
                pid=6000 + cycle % 1000,
                ppid=1,
                pgid=6000 + cycle % 1000,
                mem_used_mb=11_800,
                util_sm=util,
                exec_username="taehoon",
                exec_uid=1001,
                cmdline="python eval.py --ckpt runs/best.pt",
                proc_start_ts=float(cycle * CHURN_PERIOD_S),
                container=None,
            )
        )
        return gpu

    def _jihye_gpu(self, index: int, now: float) -> GpuReport:
        util = _wave(now, 95, 4, 20)
        gpu = self._base(index, 79_000, util, 74)
        common = dict(
            exec_username="jihye",
            exec_uid=1003,
            proc_start_ts=_EPOCH - 3600 * 27,
            container=None,
        )
        gpu.processes.append(
            ProcessReport(
                pid=5100,
                ppid=1,
                pgid=5100,
                mem_used_mb=72_000,
                util_sm=util,
                cmdline="python train.py --model resnet50 --epochs 300",
                **common,
            )
        )
        gpu.processes.append(
            ProcessReport(
                pid=5101,
                ppid=5100,
                pgid=5100,
                mem_used_mb=6_500,
                util_sm=2,
                cmdline="python train.py --model resnet50 --epochs 300",
                **common,
            )
        )
        return gpu

    def _unknown_container_gpu(self, index: int, now: float) -> GpuReport:
        util = _wave(now, 40, 30, 50)
        gpu = self._base(index, 20_000, util, 52)
        gpu.processes.append(
            ProcessReport(
                pid=7300,
                ppid=7299,
                pgid=7299,
                mem_used_mb=19_500,
                util_sm=util,
                exec_username="root",
                exec_uid=0,
                cmdline="python app.py",
                proc_start_ts=_EPOCH - 3600 * 5,
                container=ContainerInfo(
                    id="ffee0011223344556677889900aabbccddeeff00112233445566778899aabbcc",
                    name="stoic_lovelace",
                    image="nvcr.io/nvidia/pytorch:24.05-py3",
                    labels={},
                ),
            )
        )
        return gpu

    def _free_gpu(self, index: int, now: float) -> GpuReport:
        return self._base(index, 1, 0, 31)
