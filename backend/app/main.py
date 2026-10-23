"""Точка входа: FastAPI + фоновый воркер агентов + статика фронтенда."""

from __future__ import annotations

import logging
import time
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .api.routes import router
from .config import settings
from .db import SessionLocal, init_db, wait_for_db

logging.basicConfig(level=settings.log_level, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("ostrovok")


@asynccontextmanager
async def lifespan(app: FastAPI):
    t0 = time.perf_counter()
    wait_for_db()
    init_db()
    from .agents.orchestrator import reapply_overrides, start_worker
    from .region import get_region
    from .seed import bootstrap

    rd = get_region()
    _ = rd.rasters, rd.buildings  # прогреваем данные региона
    with SessionLocal() as session:
        mode = bootstrap(session)
        session.commit()
        reapply_overrides(session)
    if settings.worker_enabled:
        start_worker()
    log.info("Готово за %.1f с (данные: %s, LLM: %s). Откройте http://localhost:%s", time.perf_counter() - t0, mode,
             "включена" if settings.llm_enabled else "нет ключа — работают правила", settings.app_port)
    yield


app = FastAPI(title="Островок: знания об отелях, которых нет в карточках", version="1.0", lifespan=lifespan)
app.include_router(router)


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Ошибка обработки %s", request.url.path)
    return JSONResponse(status_code=500, content={"detail": f"{exc.__class__.__name__}: {exc}"})


def _static_dir() -> Path | None:
    candidates = [settings.static_dir] if settings.static_dir else []
    candidates.append(Path(__file__).resolve().parents[2] / "frontend" / "dist")
    for c in candidates:
        if c and (c / "index.html").exists():
            return c
    return None


_static = _static_dir()
if _static is not None:
    app.mount("/assets", StaticFiles(directory=_static / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        target = _static / path
        if path and target.is_file():
            return FileResponse(target)
        return FileResponse(_static / "index.html")
else:
    @app.get("/", include_in_schema=False)
    def root():
        return {"status": "ok", "note": "фронтенд не собран; API: /docs"}
