"""FastAPI-Einstiegspunkt (Konzept Abschnitt 6, Backend API)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes_bexio import router as bexio_router
from app.api.routes_export import router as export_router
from app.api.routes_planning import router as planning_router
from app.api.routes_sync import router as sync_router
from app.config import get_settings
from app.scheduler import start_scheduler, stop_scheduler
from app.web.routes_dashboard import router as dashboard_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="AH Planung", lifespan=lifespan)

static_dir = Path(__file__).parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(dashboard_router)
app.include_router(bexio_router)
app.include_router(sync_router)
app.include_router(planning_router)
app.include_router(export_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
