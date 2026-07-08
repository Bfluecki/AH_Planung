"""FastAPI-Einstiegspunkt (Konzept Abschnitt 6, Backend API)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes_bexio import router as bexio_router
from app.api.routes_export import router as export_router
from app.api.routes_planning import router as planning_router
from app.api.routes_sync import router as sync_router
from app.auth import ensure_seed_admin
from app.config import get_settings
from app.db import SessionLocal
from app.scheduler import start_scheduler, stop_scheduler
from app.web.routes_admin import router as admin_router
from app.web.routes_analytics import router as analytics_router
from app.web.routes_auth import router as auth_router
from app.web.routes_dashboard import router as dashboard_router

settings = get_settings()
logging.basicConfig(level=settings.log_level)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Seed-Admin anlegen (falls noch keine Benutzer existieren), bevor Requests kommen.
    db = SessionLocal()
    try:
        ensure_seed_admin(db)
    except Exception:
        logging.getLogger(__name__).exception("Seed-Admin konnte nicht angelegt werden")
    finally:
        db.close()
    start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="AH Planung", lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=60 * 60 * 12)

static_dir = Path(__file__).parent / "web" / "static"
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

app.include_router(auth_router)
app.include_router(dashboard_router)
app.include_router(analytics_router)
app.include_router(admin_router)
app.include_router(bexio_router)
app.include_router(sync_router)
app.include_router(planning_router)
app.include_router(export_router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}
