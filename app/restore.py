"""Backups auflisten, herunterladen und zurueckspielen (Gegenstueck zu app/backup.py).

    python -m app.restore --list
    python -m app.restore --download 20260312T044500Z-abc123.jsonl.gz -o /tmp/bak.gz
    python -m app.restore --from-file /tmp/bak.gz --yes

Das Zurueckspielen **loescht den aktuellen Inhalt** der betroffenen Tabellen und
ersetzt ihn durch den Stand aus dem Backup - deshalb ist ``--yes`` Pflicht.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import logging
import sys
from decimal import Decimal
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import MetaData
from sqlalchemy.engine import Engine

from app.backup import BACKUP_FORMAT, S3Storage
from app.config import Settings, get_settings

logger = logging.getLogger(__name__)


def _coerce(value, column_type):
    """JSON kennt kein Datum und keine Dezimalzahl - hier zurueck in echte Typen."""
    if value is None:
        return None
    if isinstance(column_type, sa.Numeric) and not isinstance(value, Decimal):
        return Decimal(str(value))
    if isinstance(column_type, sa.DateTime) and isinstance(value, str):
        return dt.datetime.fromisoformat(value)
    if isinstance(column_type, sa.Date) and isinstance(value, str):
        return dt.date.fromisoformat(value)
    if isinstance(column_type, sa.Time) and isinstance(value, str):
        return dt.time.fromisoformat(value)
    if isinstance(column_type, sa.LargeBinary) and isinstance(value, str):
        return bytes.fromhex(value)
    return value


def read_dump(path: Path) -> tuple[dict, dict[str, list[dict]]]:
    """Liest eine Backup-Datei; Rueckgabe sind Kopfsatz und Zeilen je Tabelle."""
    rows_by_table: dict[str, list[dict]] = {}
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        if header.get("format") != BACKUP_FORMAT:
            raise ValueError(f"Keine Backup-Datei dieses Programms: {path}")
        for line in fh:
            line = line.strip()
            if not line:
                continue
            entry = json.loads(line)
            rows_by_table.setdefault(entry["table"], []).append(entry["row"])
    return header, rows_by_table


def _reset_sequences(conn, table: sa.Table) -> None:
    """Postgres: Auto-Increment-Zaehler hinter die eingespielten IDs setzen, sonst
    kollidiert der naechste INSERT mit einer bereits vergebenen ID."""
    if conn.dialect.name != "postgresql":
        return
    for column in table.columns:
        if not isinstance(column.type, (sa.Integer, sa.BigInteger)):
            continue
        sequence = conn.execute(
            sa.text("SELECT pg_get_serial_sequence(:t, :c)"),
            {"t": table.name, "c": column.name},
        ).scalar()
        if not sequence:
            continue
        conn.execute(
            sa.text(
                f"SELECT setval(:seq, COALESCE((SELECT MAX({column.name}) FROM {table.name}), 0) + 1, false)"
            ),
            {"seq": sequence},
        )


def restore(engine: Engine, path: Path) -> dict[str, int]:
    """Spielt ein Backup ein. Alles in einer Transaktion: entweder ganz oder gar nicht."""
    header, rows_by_table = read_dump(path)
    metadata = MetaData()
    metadata.reflect(bind=engine)
    counts: dict[str, int] = {}

    with engine.begin() as conn:
        # Erst alles leeren - in umgekehrter Abhaengigkeitsreihenfolge, damit keine
        # Fremdschluessel verletzt werden; danach in normaler Reihenfolge fuellen.
        for table in reversed(metadata.sorted_tables):
            if table.name in rows_by_table:
                conn.execute(table.delete())

        for table in metadata.sorted_tables:
            rows = rows_by_table.get(table.name)
            if not rows:
                continue
            columns = {c.name: c for c in table.columns}
            payload = [
                {
                    name: _coerce(value, columns[name].type)
                    for name, value in row.items()
                    if name in columns
                }
                for row in rows
            ]
            conn.execute(table.insert(), payload)
            _reset_sequences(conn, table)
            counts[table.name] = len(payload)

    logger.info(
        "Backup vom %s eingespielt: %s Zeilen in %s Tabellen.",
        header.get("created_at", "?"), sum(counts.values()), len(counts),
    )
    return counts


def _storage(settings: Settings) -> S3Storage:
    if not settings.backup_configured:
        raise SystemExit("Kein Backup-Speicher konfiguriert (BACKUP_S3_* Variablen fehlen).")
    return S3Storage(settings)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--list", action="store_true", help="Vorhandene Backups auflisten.")
    group.add_argument("--download", metavar="KEY", help="Backup herunterladen (Schluessel aus --list).")
    group.add_argument("--from-file", metavar="PFAD", help="Lokale Backup-Datei zurueckspielen.")
    parser.add_argument("-o", "--output", metavar="PFAD", help="Zieldatei fuer --download.")
    parser.add_argument("--yes", action="store_true", help="Bestaetigt das Ueberschreiben der Daten.")
    args = parser.parse_args(argv)

    settings = get_settings()
    logging.basicConfig(level=settings.log_level)

    if args.list:
        for key in _storage(settings).list_keys():
            print(key)
        return 0

    if args.download:
        storage = _storage(settings)
        target = Path(args.output or Path(args.download).name)
        storage._client.download_file(storage.bucket, args.download, str(target))
        print(f"Heruntergeladen nach {target}")
        return 0

    if not args.yes:
        parser.error(
            "Zurueckspielen loescht den aktuellen Datenbestand. Zum Bestaetigen --yes angeben."
        )
    from app.db import engine

    counts = restore(engine, Path(args.from_file))
    for table, count in sorted(counts.items()):
        print(f"{table}: {count}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
