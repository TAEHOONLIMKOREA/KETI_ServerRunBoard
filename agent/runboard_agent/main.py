"""runboard-agent CLI.

runboard-agent run [--config PATH] [--server URL] [--token T] [--node NAME]
                   [--active-interval S] [--idle-interval S] [--idle-grace S]
                   [--fake] [--fake-gpus N] [--once] [--print]
runboard-agent version

보고 주기는 적응형이다. GPU 가 동작 중이면 active-interval(기본 1초), 모두 놀면 idle-interval(기본 30초).
규칙은 cadence.py 에 있다.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from runboard_agent import __version__
from runboard_agent.cadence import ReportCadence, is_active
from runboard_agent.collector import make_collector
from runboard_agent.config import DEFAULT_CONFIG_PATH, AgentConfig, load_config
from runboard_agent.reporter import Reporter

log = logging.getLogger("runboard_agent")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="runboard-agent", description="RunBoard GPU 노드 에이전트"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="수집·보고 루프 실행")
    run.add_argument("--config", type=Path, help=f"TOML 설정 파일 (기본 {DEFAULT_CONFIG_PATH})")
    run.add_argument("--server", help="서버 URL. 예: https://runboard.internal")
    run.add_argument("--token", help="노드 토큰")
    run.add_argument("--node", dest="node_name", help="노드 이름 (기본 hostname)")
    run.add_argument(
        "--active-interval",
        type=float,
        help="GPU 동작 중 보고 주기 초 (기본 1). 수집 주기이기도 하다",
    )
    run.add_argument("--idle-interval", type=float, help="GPU 유휴 시 보고 주기 초 (기본 30)")
    run.add_argument(
        "--idle-grace", type=float, help="동작이 멈춘 뒤 빠른 주기를 유지할 시간 초 (기본 5)"
    )
    run.add_argument("--interval", dest="active_interval", type=float, help=argparse.SUPPRESS)
    run.add_argument("--fake", action="store_true", help="가짜 수집기 사용")
    run.add_argument("--fake-gpus", type=int, help="가짜 GPU 수 (기본 8)")
    run.add_argument("--fake-boot-id", help="가짜 부팅 ID. 바꾸면 재부팅처럼 보인다")
    run.add_argument("--no-churn", action="store_true", help="가짜 수집기의 짧은 작업 생성 끄기")
    run.add_argument("--once", action="store_true", help="한 번만 수집·보고하고 종료")
    run.add_argument(
        "--print", dest="print_json", action="store_true", help="보고 JSON 을 stdout 에 출력"
    )
    run.add_argument("--log-level", default="INFO")

    sub.add_parser("version", help="버전 출력")
    return parser


def _overrides_from_args(args: argparse.Namespace) -> dict[str, Any]:
    fake: dict[str, Any] = {}
    if args.fake_gpus is not None:
        fake["gpu_count"] = args.fake_gpus
    if args.fake_boot_id:
        fake["boot_id"] = args.fake_boot_id
    if args.no_churn:
        fake["churn"] = False
    return {
        "server_url": args.server,
        "token": args.token,
        "node_name": args.node_name,
        "active_interval": args.active_interval,
        "idle_interval": args.idle_interval,
        "idle_grace": args.idle_grace,
        "collector": "fake" if args.fake else None,
        "fake": fake or None,
    }


def run_loop(
    cfg: AgentConfig,
    *,
    once: bool,
    print_json: bool,
    stop: threading.Event,
    clock: Callable[[], float] = time.monotonic,
) -> int:
    """수집은 active_interval 마다, 전송은 cadence 가 정한 때만 한다. print 도 전송과 같은 때 한다."""
    collector = make_collector(cfg)
    reporter = (
        Reporter(cfg.server_url, cfg.token or "", timeout=cfg.timeout, clock=clock)
        if cfg.server_url
        else None
    )
    cadence = ReportCadence(cfg.active_interval, cfg.idle_interval, cfg.idle_grace)
    log.info(
        "시작: node=%s collector=%s server=%s 주기=동작 중 %gs / 유휴 %gs",
        cfg.node_name,
        cfg.collector,
        cfg.server_url or "(전송 안 함)",
        cfg.active_interval,
        cfg.idle_interval,
    )
    exit_code = 0
    was_fast: bool | None = None
    try:
        while not stop.is_set():
            started = clock()
            try:
                report = collector.collect()
            except Exception:
                log.exception("수집 실패")
                if once:
                    return 1
                stop.wait(cfg.active_interval)
                continue

            now = clock()
            active = is_active(report)
            cadence.observe(active, now)
            fast = cadence.fast(now)
            if fast != was_fast:
                log.info(
                    "보고 주기 %gs (%s)",
                    cfg.active_interval if fast else cfg.idle_interval,
                    "GPU 동작 중" if fast else "GPU 유휴",
                )
                was_fast = fast
            report.node.report_interval_s = cadence.declared_interval(active)

            if once or cadence.due(now):
                if reporter is not None and not reporter.ready(now):
                    ok = False  # 백오프 중. due 는 유지되므로 백오프가 끝나면 바로 보낸다
                else:
                    if print_json:
                        print(report.model_dump_json(indent=2), flush=True)
                    ok = reporter.send(report) if reporter else True
                    if ok:
                        cadence.mark_sent(now)
                if once:
                    exit_code = 0 if ok else 1
                    break

            elapsed = clock() - started
            stop.wait(max(0.0, cfg.active_interval - elapsed))
    finally:
        collector.close()
        if reporter:
            reporter.close()
    return exit_code


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "version":
        print(__version__)
        return 0

    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한글 로그가 깨지지 않게
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        cfg = load_config(args.config, overrides=_overrides_from_args(args))
    except (FileNotFoundError, ValueError) as exc:
        log.error("설정 오류: %s", exc)
        return 2

    if not cfg.server_url and not args.print_json:
        log.error("--server 또는 --print 중 하나는 필요하다")
        return 2
    if cfg.server_url and not cfg.token:
        log.error("--token (또는 RUNBOARD_TOKEN) 이 필요하다")
        return 2

    stop = threading.Event()

    def _handle_signal(signum, frame):
        log.info("종료 신호 수신 (%s)", signum)
        stop.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, _handle_signal)

    return run_loop(cfg, once=args.once, print_json=args.print_json, stop=stop)


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
