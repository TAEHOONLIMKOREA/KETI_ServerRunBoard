"""runboard-server 진입점.

    runboard-server [--config runboard.yaml] [--host 0.0.0.0] [--port 8000]

lifespan 에서 DB 복원과 10초 주기 tick 태스크를 돌린다 (APScheduler 없이 asyncio).
web_dist 가 설정돼 있으면 빌드된 프론트를 / 에서 정적 서빙한다.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from runboard_server import __version__
from runboard_server.api import ingest, query
from runboard_server.config import ServerConfig, load_server_config
from runboard_server.core.engine import Engine
from runboard_server.db.repo import Repository

log = logging.getLogger("runboard_server")

TICK_INTERVAL_S = 10.0


async def _ticker(engine: Engine, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            await asyncio.to_thread(engine.tick)
        except Exception:
            log.exception("tick 실패")


def create_app(
    cfg: ServerConfig | None = None,
    repo: Repository | None = None,
    *,
    start_scheduler: bool = True,
    use_db: bool = True,
) -> FastAPI:
    cfg = cfg or load_server_config()
    if repo is None and use_db:
        repo = Repository(cfg.server.db_url)
    engine = Engine(cfg, repo)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        engine.restore()
        task = asyncio.create_task(_ticker(engine, TICK_INTERVAL_S)) if start_scheduler else None
        db = repo.engine.url.render_as_string(hide_password=True) if repo else "없음"
        log.info("RunBoard server %s 시작 (db=%s)", __version__, db)
        try:
            yield
        finally:
            if task:
                task.cancel()
            if repo is not None:
                repo.close()

    app = FastAPI(title="RunBoard", version=__version__, lifespan=lifespan)
    app.state.config = cfg
    app.state.engine = engine
    app.state.repo = repo
    app.include_router(ingest.router)
    app.include_router(query.router)

    if cfg.server.web_dist:
        _mount_web(app, Path(cfg.server.web_dist))
    return app


def _mount_web(app: FastAPI, dist: Path) -> None:
    index = dist / "index.html"
    if not index.is_file():
        log.warning("web_dist 에 index.html 이 없다: %s (API 만 제공)", dist)
        return
    assets = dist / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")
    dist_resolved = dist.resolve()

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404)
        candidate = (dist / full_path).resolve() if full_path else None
        if candidate and candidate.is_file() and candidate.is_relative_to(dist_resolved):
            return FileResponse(candidate)
        return FileResponse(index)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="runboard-server", description="RunBoard 중앙 서버")
    parser.add_argument("--config", type=Path, help="runboard.yaml 경로 (기본: RUNBOARD_CONFIG)")
    parser.add_argument("--host")
    parser.add_argument("--port", type=int)
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--version", action="version", version=__version__)
    args = parser.parse_args(argv)

    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")  # Windows 콘솔에서 한글 로그가 깨지지 않게
    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )
    try:
        cfg = load_server_config(args.config)
    except (FileNotFoundError, ValueError) as exc:  # 값 누락·YAML 검증 실패는 ValueError
        log.error("%s", exc)
        return 2

    import uvicorn

    app = create_app(cfg)
    uvicorn.run(
        app,
        host=args.host or cfg.server.host,
        port=args.port or cfg.server.port,
        workers=1,
        log_level=args.log_level.lower(),
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
