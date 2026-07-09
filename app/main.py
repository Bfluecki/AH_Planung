"""FastAPI-Einstiegspunkt (Konzept Abschnitt 6, Backend API)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, status
from fastapi.exception_handlers import http_exception_handler as default_http_exception_handler
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException
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

_templates = Jinja2Templates(directory=str(Path(__file__).parent / "web" / "templates"))


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    """Zugriffsverweigerungen (403, z.B. Nicht-Admin auf Admin-Seite) als elegante
    HTML-Seite statt als roher JSON-Fehler ausgeben. Alles andere (inkl. der
    303-Weiterleitungen auf /login bzw. /passwort) bleibt beim Standardverhalten."""
    if exc.status_code == status.HTTP_403_FORBIDDEN:
        return _templates.TemplateResponse(
            "fehler.html",
            {
                "request": request,
                "titel": "Kein Zugriff",
                "nachricht": exc.detail or "Diese Seite ist nur für Administratoren.",
            },
            status_code=status.HTTP_403_FORBIDDEN,
        )
    return await default_http_exception_handler(request, exc)

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
