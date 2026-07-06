"""Periodischer Sync-Scheduler (Konzept Abschnitt 6, "Sync-Scheduler").

Bexio bietet keine vollwertigen Webhooks fuer alle benoetigten Dokumenttypen,
daher periodisches Pull-Polling statt Push.
"""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler

from app.bexio.client import BexioApiError, BexioAuthError
from app.config import get_settings
from app.db import SessionLocal
from app.sync.service import full_sync

logger = logging.getLogger(__name__)

_scheduler: BackgroundScheduler | None = None


def _run_sync_job() -> None:
    db = SessionLocal()
    try:
        stats = full_sync(db)
        logger.info("Scheduled sync completed: %s", stats)
    except BexioAuthError:
        logger.warning("Scheduled sync skipped: kein gueltiger Bexio-Token (siehe /bexio/login)")
    except BexioApiError:
        logger.exception("Scheduled sync failed with Bexio API error")
    finally:
        db.close()


def start_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        return
    settings = get_settings()
    _scheduler = BackgroundScheduler(timezone="UTC")
    _scheduler.add_job(
        _run_sync_job,
        "interval",
        minutes=settings.sync_interval_minutes,
        id="bexio_sync",
    )
    _scheduler.start()
    logger.info("Sync-Scheduler gestartet (alle %s Minuten)", settings.sync_interval_minutes)


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
