"""Automatisches Datenbank-Backup, das vor jedem Deploy laeuft.

Aufgerufen wird das Modul im Startbefehl (siehe railway.json) *vor*
``alembic upgrade head`` - also bevor eine Migration die Datenbank anfassen kann::

    python -m app.backup && alembic upgrade head && uvicorn app.main:app ...

Gesichert wird der komplette Datenbestand als gzip-komprimierte JSON-Lines-Datei
in einem S3-kompatiblen Objektspeicher (AWS S3, Backblaze B2, Wasabi, Cloudflare
R2, MinIO). Die Tabellen werden aus der *tatsaechlichen* Datenbank gelesen
(Reflection) und nicht aus app/models.py - zum Backup-Zeitpunkt gilt noch das alte
Schema, und neue Tabellen sind so automatisch mit dabei.

Grundsaetze:
- Ohne konfigurierten Speicher wird uebersprungen, nicht abgebrochen.
- Ein Fehler nimmt die App nur dann offline, wenn BACKUP_REQUIRED=true gesetzt ist.
- Pro Deployment entsteht genau ein Backup; ein Neustart derselben Version legt
  kein weiteres an.

Wiederherstellen: siehe README.md, Abschnitt "Backup".
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path

from sqlalchemy import MetaData, select
from sqlalchemy.engine import Engine

from app.config import Settings, get_settings

logger = logging.getLogger(__name__)

BACKUP_FORMAT = "ah-planung-backup"
BACKUP_FORMAT_VERSION = 1

# Zeilen pro Lesevorgang - haelt den Speicherbedarf unabhaengig von der Tabellengroesse.
_CHUNK_SIZE = 1000


def _json_default(value):
    """Typen, die json nicht von Haus aus kann (Decimal, Datum, Bytes)."""
    if isinstance(value, Decimal):
        # Als String, damit beim Zurueckspielen keine Rundung durch float entsteht.
        return str(value)
    if isinstance(value, (dt.datetime, dt.date, dt.time)):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return value.total_seconds()
    if isinstance(value, (bytes, bytearray, memoryview)):
        return bytes(value).hex()
    if isinstance(value, set):
        return sorted(value)
    raise TypeError(f"Nicht serialisierbarer Typ im Backup: {type(value)!r}")


def write_dump(engine: Engine, target: Path, deployment_id: str = "") -> dict[str, int]:
    """Schreibt alle Tabellen als gzip-JSON-Lines nach ``target``.

    Erste Zeile ist ein Kopfsatz mit Metadaten, danach folgt pro Datenzeile ein
    Objekt ``{"table": ..., "row": {...}}``. Rueckgabe: Zeilenzahl je Tabelle.
    """
    metadata = MetaData()
    metadata.reflect(bind=engine)
    tables = sorted(metadata.tables.values(), key=lambda t: t.name)
    counts: dict[str, int] = {}

    with gzip.open(target, "wt", encoding="utf-8") as fh:
        header = {
            "format": BACKUP_FORMAT,
            "version": BACKUP_FORMAT_VERSION,
            "created_at": dt.datetime.now(dt.timezone.utc).isoformat(),
            "deployment_id": deployment_id,
            "dialect": engine.dialect.name,
            "tables": [t.name for t in tables],
        }
        fh.write(json.dumps(header, ensure_ascii=False) + "\n")

        with engine.connect() as conn:
            for table in tables:
                count = 0
                # stream_results: Serverseitiger Cursor statt alles in den Speicher.
                result = conn.execution_options(stream_results=True).execute(select(table))
                for batch in iter(lambda r=result: r.fetchmany(_CHUNK_SIZE), []):
                    for row in batch:
                        payload = {"table": table.name, "row": dict(row._mapping)}
                        fh.write(json.dumps(payload, ensure_ascii=False, default=_json_default) + "\n")
                        count += 1
                result.close()
                counts[table.name] = count
    return counts


class S3Storage:
    """Duenne Huelle um boto3 - nur die drei Operationen, die das Backup braucht."""

    def __init__(self, settings: Settings):
        import boto3  # lokal importiert, damit der Rest der App ohne boto3 laeuft

        self.bucket = settings.backup_s3_bucket
        self.prefix = settings.backup_s3_prefix.strip("/")
        self._client = boto3.client(
            "s3",
            endpoint_url=settings.backup_s3_endpoint_url or None,
            region_name=settings.backup_s3_region or None,
            aws_access_key_id=settings.backup_s3_access_key_id,
            aws_secret_access_key=settings.backup_s3_secret_access_key,
        )

    def list_keys(self) -> list[str]:
        keys: list[str] = []
        token = None
        while True:
            kwargs = {"Bucket": self.bucket, "Prefix": f"{self.prefix}/"}
            if token:
                kwargs["ContinuationToken"] = token
            response = self._client.list_objects_v2(**kwargs)
            keys.extend(item["Key"] for item in response.get("Contents", []))
            if not response.get("IsTruncated"):
                return sorted(keys)
            token = response.get("NextContinuationToken")

    def upload(self, path: Path, key: str) -> None:
        self._client.upload_file(str(path), self.bucket, key)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def _deployment_id() -> str:
    """Kennung des laufenden Deploys; Railway setzt diese Variablen selbst."""
    for name in ("RAILWAY_DEPLOYMENT_ID", "RAILWAY_GIT_COMMIT_SHA", "RAILWAY_SNAPSHOT_ID"):
        value = os.environ.get(name, "").strip()
        if value:
            return value[:12]
    return ""


def _build_key(prefix: str, deployment_id: str, now: dt.datetime) -> str:
    # Zeitstempel zuerst: dadurch entspricht die alphabetische Sortierung der
    # zeitlichen, was das Aufraeumen alter Backups trivial macht. Millisekunden,
    # damit zwei Laeufe in derselben Sekunde (z.B. mit --force) sich nicht
    # gegenseitig ueberschreiben.
    stamp = now.strftime("%Y%m%dT%H%M%S.%f")[:-3] + "Z"
    suffix = f"-{deployment_id}" if deployment_id else ""
    return f"{prefix.strip('/')}/{stamp}{suffix}.jsonl.gz"


def prune(storage: S3Storage, keep: int) -> list[str]:
    """Loescht die aeltesten Backups, bis nur noch ``keep`` uebrig sind."""
    if keep <= 0:
        return []
    keys = storage.list_keys()
    doomed = keys[: max(0, len(keys) - keep)]
    for key in doomed:
        storage.delete(key)
    return doomed


def run_backup(
    settings: Settings | None = None,
    engine: Engine | None = None,
    storage: S3Storage | None = None,
    force: bool = False,
) -> dict:
    """Erstellt ein Backup und laedt es hoch.

    Rueckgabe beschreibt das Ergebnis: ``status`` ist "ok", "skipped" (kein Speicher
    konfiguriert bzw. dieses Deployment schon gesichert) oder "failed".
    """
    settings = settings or get_settings()
    if not settings.backup_configured:
        logger.warning(
            "Backup uebersprungen: BACKUP_S3_BUCKET/-ACCESS_KEY_ID/-SECRET_ACCESS_KEY "
            "sind nicht gesetzt. Es wird nichts gesichert."
        )
        return {"status": "skipped", "reason": "not_configured"}

    if engine is None:
        from app.db import engine as default_engine

        engine = default_engine

    deployment_id = _deployment_id()
    try:
        storage = storage or S3Storage(settings)

        if deployment_id and not force:
            # Neustart derselben Version (Crash, Sleep/Wake) soll kein weiteres
            # Backup erzeugen - gesichert wird einmal pro Deploy.
            if any(f"-{deployment_id}.jsonl.gz" in key for key in storage.list_keys()):
                logger.info("Backup fuer Deployment %s existiert bereits.", deployment_id)
                return {"status": "skipped", "reason": "already_done", "deployment_id": deployment_id}

        now = dt.datetime.now(dt.timezone.utc)
        key = _build_key(settings.backup_s3_prefix, deployment_id, now)

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "backup.jsonl.gz"
            counts = write_dump(engine, path, deployment_id)
            size = path.stat().st_size
            storage.upload(path, key)

        removed = prune(storage, settings.backup_keep)
        total = sum(counts.values())
        logger.info(
            "Backup erstellt: %s (%s Zeilen aus %s Tabellen, %.1f KB), %s alte geloescht.",
            key, total, len(counts), size / 1024, len(removed),
        )
        return {
            "status": "ok",
            "key": key,
            "bytes": size,
            "rows": total,
            "tables": counts,
            "pruned": removed,
        }
    except Exception as exc:  # noqa: BLE001 - jeder Fehler wird gleich behandelt
        logger.exception("Backup fehlgeschlagen")
        return {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}


def _write_audit(result: dict) -> None:
    """Ergebnis im Audit-Log festhalten, damit es auf der Admin-Seite sichtbar ist."""
    try:
        from sqlalchemy import inspect

        from app.audit import log_action
        from app.db import SessionLocal, engine

        if not inspect(engine).has_table("audit_log"):
            # Allererster Deploy: die Migration lief noch nie, es gibt noch kein Log.
            logger.info("Audit-Log-Tabelle existiert noch nicht - Ergebnis nur im Deploy-Log.")
            return

        detail = {k: v for k, v in result.items() if k != "tables"}
        db = SessionLocal()
        try:
            log_action(db, "system", f"backup_{result['status']}", json.dumps(detail, ensure_ascii=False))
        finally:
            db.close()
    except Exception:
        logger.exception("Backup-Ergebnis konnte nicht ins Audit-Log geschrieben werden")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Datenbank-Backup erstellen und hochladen.")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Auch dann sichern, wenn fuer dieses Deployment schon ein Backup existiert.",
    )
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)
    result = run_backup(settings, force=args.force)
    _write_audit(result)

    if result["status"] == "failed" and settings.backup_required:
        # Nur mit BACKUP_REQUIRED=true bricht der Start ab - sonst gilt: lieber eine
        # laufende App ohne frisches Backup als eine Anwendung, die gar nicht startet.
        logger.error("BACKUP_REQUIRED=true und Backup fehlgeschlagen - Start wird abgebrochen.")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
