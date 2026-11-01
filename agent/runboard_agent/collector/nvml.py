"""실제 수집기. nvidia-ml-py(pynvml) 로 GPU 와 프로세스를 읽고 enrich 로 보강한다.

실패한 항목은 errors 에 남기고 예외를 던지지 않는다. 값을 못 얻으면 0 이 아니라 None 이다.
NVML 초기화에 실패해도 노드 정보는 보고한다. 다음 수집 때 다시 초기화를 시도한다.
"""

from __future__ import annotations

import contextlib
import logging
import socket
import time
import uuid

import psutil

from runboard_agent import __version__
from runboard_agent.enrich import IS_LINUX, Enricher
from runboard_common.schemas import GpuReport, NodeInfo, NodeReport, ProcessReport, ReportError

log = logging.getLogger(__name__)

try:
    import pynvml
except ImportError:  # pragma: no cover
    pynvml = None

_BOOT_NAMESPACE = uuid.UUID("7c0d4d2f-9a1e-4b8c-b3f2-1e5a6c7d8e9f")


def read_boot_id() -> str:
    """재부팅마다 바뀌는 식별자. Linux 는 커널이 주고, 그 외는 (hostname, 부팅 시각) 으로 만든다."""
    if IS_LINUX:
        try:
            with open("/proc/sys/kernel/random/boot_id", encoding="ascii") as f:
                return f.read().strip()
        except OSError:
            pass
    return str(uuid.uuid5(_BOOT_NAMESPACE, f"{socket.gethostname()}:{int(psutil.boot_time())}"))


def local_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))  # 실제로 보내지 않는다. 라우팅용 로컬 주소만 얻는다
            return s.getsockname()[0]
    except OSError:
        return None


def _text(value) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _error_code(exc: Exception) -> str:
    if pynvml is not None:
        if isinstance(exc, pynvml.NVMLError_NotSupported):
            return "NOT_SUPPORTED"
        if isinstance(exc, pynvml.NVMLError_NoPermission):
            return "NO_PERMISSION"
    return "ERROR"


class NvmlCollector:
    def __init__(
        self, node_name: str, node_id: str | None = None, enricher: Enricher | None = None
    ) -> None:
        self.node_name = node_name
        self.node_id = node_id
        self.enricher = enricher or Enricher()
        self.boot_id = read_boot_id()
        self._ready = False
        self._init_error: str | None = None
        self._last_util_ts: dict[str, int] = {}
        self._init()
        psutil.cpu_percent(interval=None)  # 첫 호출은 기준점만 잡는다

    def _init(self) -> None:
        if pynvml is None:
            self._init_error = "nvidia-ml-py 가 설치되지 않았다"
            return
        try:
            pynvml.nvmlInit()
            self._ready = True
            self._init_error = None
        except Exception as exc:
            self._ready = False
            self._init_error = f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        if self._ready and pynvml is not None:
            with contextlib.suppress(Exception):
                pynvml.nvmlShutdown()
            self._ready = False

    # ------------------------------------------------------------------ 수집

    def collect(self) -> NodeReport:
        errors: list[ReportError] = []
        now = time.time()
        vm = psutil.virtual_memory()
        if not self._ready:
            self._init()
        driver = cuda = None
        if self._ready:
            try:
                driver = _text(pynvml.nvmlSystemGetDriverVersion())
            except Exception as exc:
                errors.append(
                    ReportError(scope="node", item="driver_version", code=_error_code(exc))
                )
            try:
                v = int(pynvml.nvmlSystemGetCudaDriverVersion_v2())
                cuda = f"{v // 1000}.{(v % 1000) // 10}"
            except Exception:
                cuda = None

        node = NodeInfo(
            name=self.node_name,
            node_id=self.node_id,
            ip=local_ip(),
            boot_id=self.boot_id,
            driver_version=driver,
            cuda_version=cuda,
            agent_version=__version__,
            measured_ts=now,
            cpu_percent=psutil.cpu_percent(interval=None),
            mem_used_mb=int(vm.used / 2**20),
            mem_total_mb=int(vm.total / 2**20),
        )
        if not self._ready:
            errors.append(
                ReportError(scope="node", item="nvml_init", code="ERROR", message=self._init_error)
            )
            return NodeReport(node=node, gpus=[], errors=errors)

        gpus: list[GpuReport] = []
        try:
            count = int(pynvml.nvmlDeviceGetCount())
        except Exception as exc:
            errors.append(
                ReportError(
                    scope="node", item="device_count", code=_error_code(exc), message=str(exc)
                )
            )
            return NodeReport(node=node, gpus=[], errors=errors)
        for index in range(count):
            try:
                gpus.append(self._gpu(index, errors))
            except Exception as exc:
                errors.append(
                    ReportError(
                        scope=f"gpu:{index}", item="device", code=_error_code(exc), message=str(exc)
                    )
                )
        return NodeReport(node=node, gpus=gpus, errors=errors)

    def _gpu(self, index: int, errors: list[ReportError]) -> GpuReport:
        scope = f"gpu:{index}"
        handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        gpu_uuid = _text(pynvml.nvmlDeviceGetUUID(handle))
        name = _text(pynvml.nvmlDeviceGetName(handle))

        def read(item: str, fn, default=None):
            try:
                return fn()
            except Exception as exc:
                errors.append(ReportError(scope=scope, item=item, code=_error_code(exc)))
                return default

        mem = read("memory", lambda: pynvml.nvmlDeviceGetMemoryInfo(handle))
        util = read("utilization", lambda: pynvml.nvmlDeviceGetUtilizationRates(handle))
        temp = read(
            "temperature",
            lambda: int(pynvml.nvmlDeviceGetTemperature(handle, pynvml.NVML_TEMPERATURE_GPU)),
        )
        power = read("power", lambda: pynvml.nvmlDeviceGetPowerUsage(handle) / 1000.0)
        limit = read("power_limit", lambda: pynvml.nvmlDeviceGetEnforcedPowerLimit(handle) / 1000.0)
        mig = False
        try:
            current, _pending = pynvml.nvmlDeviceGetMigMode(handle)
            mig = current == pynvml.NVML_DEVICE_MIG_ENABLE
        except Exception:
            mig = False

        gpu = GpuReport(
            index=index,
            uuid=gpu_uuid,
            name=name,
            mem_total_mb=int(mem.total / 2**20) if mem else 0,
            mem_used_mb=int(mem.used / 2**20) if mem else None,
            util_gpu=int(util.gpu) if util else None,
            util_mem=int(util.memory) if util else None,
            temp_c=temp,
            power_w=round(power, 1) if power is not None else None,
            power_limit_w=round(limit, 1) if limit is not None else None,
            mig_enabled=mig,
        )
        gpu.processes = self._processes(handle, gpu_uuid, scope, errors)
        return gpu

    def _processes(
        self, handle, gpu_uuid: str, scope: str, errors: list[ReportError]
    ) -> list[ProcessReport]:
        found: dict[int, dict] = {}
        for kind, fn in (
            ("C", pynvml.nvmlDeviceGetComputeRunningProcesses),
            ("G", pynvml.nvmlDeviceGetGraphicsRunningProcesses),
        ):
            try:
                entries = fn(handle)
            except Exception as exc:
                errors.append(
                    ReportError(scope=scope, item=f"processes_{kind}", code=_error_code(exc))
                )
                continue
            for entry in entries:
                pid = int(entry.pid)
                used = getattr(entry, "usedGpuMemory", None)
                mem_mb = None if used is None or used < 0 or used >= 2**62 else int(used / 2**20)
                rec = found.setdefault(pid, {"types": set(), "mem": None})
                rec["types"].add(kind)
                if mem_mb is not None:
                    rec["mem"] = mem_mb if rec["mem"] is None else max(rec["mem"], mem_mb)

        util_by_pid = self._process_util(handle, gpu_uuid, scope, errors) if found else {}

        out: list[ProcessReport] = []
        for pid, rec in sorted(found.items()):
            pscope = f"{scope}/pid:{pid}"
            facts = self.enricher.facts(pid, pscope)
            errors.extend(facts.errors)
            types = rec["types"]
            ptype = "C+G" if types == {"C", "G"} else ("G" if types == {"G"} else "C")
            out.append(
                ProcessReport(
                    pid=pid,
                    ppid=facts.ppid,
                    pgid=facts.pgid,
                    type=ptype,
                    mem_used_mb=rec["mem"],
                    util_sm=util_by_pid.get(pid),
                    exec_username=facts.exec_username,
                    exec_uid=facts.exec_uid,
                    cmdline=facts.cmdline,
                    proc_start_ts=facts.proc_start_ts,
                    container=facts.container,
                )
            )
        return out

    def _process_util(
        self, handle, gpu_uuid: str, scope: str, errors: list[ReportError]
    ) -> dict[int, int]:
        """프로세스별 SM 사용률. 드라이버·GPU 에 따라 미지원이면 빈 dict 와 NOT_SUPPORTED 오류."""
        last = self._last_util_ts.get(gpu_uuid, 0)
        try:
            samples = pynvml.nvmlDeviceGetProcessUtilization(handle, last)
        except Exception as exc:
            if pynvml is not None and isinstance(exc, pynvml.NVMLError_NotFound):
                return {}  # 새 샘플이 없을 뿐이다
            errors.append(ReportError(scope=scope, item="process_util", code=_error_code(exc)))
            return {}
        out: dict[int, int] = {}
        newest = last
        for s in samples:
            newest = max(newest, int(getattr(s, "timeStamp", 0)))
            sm = int(getattr(s, "smUtil", 0))
            if 0 <= sm <= 100:
                out[int(s.pid)] = max(out.get(int(s.pid), 0), sm)
        self._last_util_ts[gpu_uuid] = newest
        return out
