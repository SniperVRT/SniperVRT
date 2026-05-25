"""FastAPI app entrypoint. Mounts the API router and serves the SPA from /frontend."""
from __future__ import annotations

import contextlib
import logging
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import api_router
from backend.app.core.config import LOG_DIR, get_config, get_settings
from backend.app.core.db import init_db
from backend.app.data import ensure_dataset
from backend.app.paper.runtime import get_runtime


def _setup_logging() -> None:
    settings = get_settings()
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(LOG_DIR / "platform.log"),
        ],
    )


_setup_logging()
log = logging.getLogger("snipervrt")


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    cfg = get_config()
    log.info("DB initialized; symbol=%s tf=%s", cfg.data.default_symbol, cfg.data.default_timeframe)
    try:
        res = ensure_dataset()
        log.info("dataset ready: %s", res)
    except Exception as e:
        log.exception("dataset bootstrap failed: %s", e)
    if cfg.paper.enable_on_start:
        try:
            get_runtime().start()
            log.info("paper runtime started on boot")
        except Exception as e:
            log.exception("paper runtime auto-start failed: %s", e)
    yield
    try:
        get_runtime().stop()
    except Exception:
        pass


app = FastAPI(
    title="SniperVRT BTC AI Trading Platform",
    version="0.1.0",
    description="Research → backtest → paper-trade → governance → (locked) live.",
    lifespan=lifespan,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(api_router, prefix="/api")


# Frontend SPA
FRONTEND_DIR = Path(__file__).resolve().parents[2] / "frontend"
STATIC_DIR = FRONTEND_DIR / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/")
def root():
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"ok": True, "name": "SniperVRT", "ts": int(time.time()),
                         "ui": "frontend index missing"})


# Catch-all so the SPA can own client-side routing if needed
@app.get("/{path:path}")
def spa_fallback(path: str):
    if path.startswith("api/"):
        return JSONResponse({"detail": "not found"}, status_code=404)
    index = FRONTEND_DIR / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"detail": "not found"}, status_code=404)
