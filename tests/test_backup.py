"""Backup und Wiederherstellung - ohne Netzwerk, mit einem Speicher-Double."""
import datetime as dt
import gzip
import json
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.backup import _build_key, prune, run_backup, write_dump
from app.config import Settings
from app.db import Base
from app.models import AdminConfig, Invoice, MonthlyBudget, User
from app.restore import read_dump, restore


class FakeStorage:
    """Ersetzt S3Storage: haelt die Objekte im Speicher statt im Netz."""

    def __init__(self, prefix="ah-planung"):
        self.objects: dict[str, bytes] = {}
        self.prefix = prefix

    def list_keys(self):
        return sorted(self.objects)

    def upload(self, path: Path, key: str):
        self.objects[key] = Path(path).read_bytes()

    def delete(self, key: str):
        self.objects.pop(key, None)


def _settings(**overrides) -> Settings:
    base = dict(
        backup_s3_bucket="bucket",
        backup_s3_access_key_id="key",
        backup_s3_secret_access_key="secret",
        backup_s3_prefix="ah-planung",
        backup_keep=3,
    )
    base.update(overrides)
    return Settings(**base)


@pytest.fixture
def engine():
    engine = create_engine(
        "sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add(User(username="admin", password_hash="hash", role="admin", first_name="Max"))
    session.add(AdminConfig(id=1, monthly_budget_chf=Decimal("28333.30"), current_planning_year=2026))
    session.add(MonthlyBudget(year=2026, month=3, budget_chf=Decimal("31000.55")))
    session.add(
        Invoice(
            id=7, document_nr="RE-007", invoice_date=dt.date(2026, 3, 12),
            total=Decimal("1234.56"), raw={"kb_item_status_id": 9, "total_net": "1234.56"},
        )
    )
    session.commit()
    session.close()
    return engine


def test_dump_contains_every_table_and_keeps_decimals_exact(engine, tmp_path):
    target = tmp_path / "dump.jsonl.gz"
    counts = write_dump(engine, target, deployment_id="dep123")

    with gzip.open(target, "rt", encoding="utf-8") as fh:
        header = json.loads(fh.readline())
        entries = [json.loads(line) for line in fh if line.strip()]

    assert header["format"] == "ah-planung-backup"
    assert header["deployment_id"] == "dep123"
    # Alle Tabellen der Datenbank sind erfasst, auch die leeren.
    assert {"users", "admin_config", "monthly_budgets", "invoices", "bookings"} <= set(header["tables"])
    assert counts["users"] == 1 and counts["invoices"] == 1 and counts["bookings"] == 0

    budget = next(e["row"] for e in entries if e["table"] == "monthly_budgets")
    # Betraege als String, damit kein float die Rappen verfaelscht.
    assert budget["budget_chf"] == "31000.55"
    invoice = next(e["row"] for e in entries if e["table"] == "invoices")
    assert invoice["invoice_date"] == "2026-03-12"
    assert invoice["raw"] == {"kb_item_status_id": 9, "total_net": "1234.56"}


def test_backup_then_restore_reproduces_the_data(engine, tmp_path):
    target = tmp_path / "dump.jsonl.gz"
    write_dump(engine, target)

    # Datenbestand zerstoeren, wie es eine missglueckte Migration tun koennte.
    session = sessionmaker(bind=engine)()
    session.query(Invoice).delete()
    session.query(MonthlyBudget).delete()
    session.commit()
    session.close()

    restore(engine, target)

    session = sessionmaker(bind=engine)()
    invoice = session.get(Invoice, 7)
    assert invoice.document_nr == "RE-007"
    assert invoice.invoice_date == dt.date(2026, 3, 12)
    assert invoice.total == Decimal("1234.56")
    assert invoice.raw["kb_item_status_id"] == 9
    assert session.query(MonthlyBudget).one().budget_chf == Decimal("31000.55")
    assert session.query(User).one().username == "admin"
    session.close()


def test_restore_rejects_a_foreign_file(tmp_path):
    path = tmp_path / "fremd.jsonl.gz"
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        fh.write(json.dumps({"format": "etwas-anderes"}) + "\n")
    with pytest.raises(ValueError):
        read_dump(path)


def test_run_backup_uploads_and_keeps_only_the_newest(engine):
    storage = FakeStorage()
    for old in ("ah-planung/20260101T000000.000Z.jsonl.gz", "ah-planung/20260102T000000.000Z.jsonl.gz",
                "ah-planung/20260103T000000.000Z.jsonl.gz"):
        storage.objects[old] = b"alt"

    result = run_backup(_settings(), engine=engine, storage=storage)

    assert result["status"] == "ok"
    assert result["rows"] == 4
    # backup_keep=3: der aelteste Stand ist weg, das neue Backup liegt drin.
    assert len(storage.objects) == 3
    assert "ah-planung/20260101T000000.000Z.jsonl.gz" not in storage.objects
    assert result["key"] in storage.objects


def test_one_backup_per_deployment(engine, monkeypatch):
    monkeypatch.setenv("RAILWAY_DEPLOYMENT_ID", "deploy-abc")
    storage = FakeStorage()

    first = run_backup(_settings(), engine=engine, storage=storage)
    second = run_backup(_settings(), engine=engine, storage=storage)

    assert first["status"] == "ok"
    # Ein Neustart derselben Version legt kein zweites Backup an ...
    assert second["status"] == "skipped" and second["reason"] == "already_done"
    assert len(storage.objects) == 1
    # ... erzwingen laesst es sich trotzdem.
    assert run_backup(_settings(), engine=engine, storage=storage, force=True)["status"] == "ok"
    assert len(storage.objects) == 2


def test_without_configuration_nothing_happens(engine):
    result = run_backup(Settings(backup_s3_bucket=""), engine=engine)
    assert result["status"] == "skipped" and result["reason"] == "not_configured"


def test_storage_failure_is_reported_not_raised(engine):
    class BrokenStorage(FakeStorage):
        def upload(self, path, key):
            raise OSError("Speicher nicht erreichbar")

    result = run_backup(_settings(), engine=engine, storage=BrokenStorage())
    assert result["status"] == "failed"
    assert "Speicher nicht erreichbar" in result["error"]


def test_failed_backup_blocks_the_start_only_when_required(engine, monkeypatch):
    from app import backup as backup_module

    monkeypatch.setattr(backup_module, "run_backup", lambda *a, **k: {"status": "failed", "error": "x"})
    monkeypatch.setattr(backup_module, "_write_audit", lambda result: None)

    monkeypatch.setattr(backup_module, "get_settings", lambda: _settings(backup_required=False))
    assert backup_module.main([]) == 0

    monkeypatch.setattr(backup_module, "get_settings", lambda: _settings(backup_required=True))
    assert backup_module.main([]) == 1


def test_key_sorts_chronologically():
    older = _build_key("ah-planung", "dep1", dt.datetime(2026, 3, 12, 4, 45))
    newer = _build_key("ah-planung", "dep2", dt.datetime(2026, 3, 12, 5, 45))
    assert older < newer
    assert newer.endswith("-dep2.jsonl.gz")


def test_two_runs_in_the_same_second_do_not_overwrite_each_other():
    same_second = dt.datetime(2026, 3, 12, 4, 45, 30, 120000)
    later = dt.datetime(2026, 3, 12, 4, 45, 30, 870000)
    assert _build_key("p", "d", same_second) != _build_key("p", "d", later)
    assert _build_key("p", "d", same_second) < _build_key("p", "d", later)


def test_prune_keeps_everything_when_keep_is_zero():
    storage = FakeStorage()
    storage.objects = {"ah-planung/a": b"1", "ah-planung/b": b"2"}
    assert prune(storage, 0) == []
    assert len(storage.objects) == 2


# ---------------------------------------------------------------------------
# Durchlauf gegen eine echte S3-API (moto). Prueft zusaetzlich S3Storage selbst,
# das die uebrigen Tests durch FakeStorage ersetzen.
# ---------------------------------------------------------------------------
moto = pytest.importorskip("moto", reason="moto nur fuer den S3-Durchlauf noetig")


def test_full_round_trip_against_a_real_s3_api(engine, tmp_path):
    import boto3

    with moto.mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket="ah-backups")
        settings = _settings(
            backup_s3_bucket="ah-backups", backup_s3_region="us-east-1", backup_keep=2
        )
        from app.backup import S3Storage

        storage = S3Storage(settings)
        for _ in range(4):
            assert run_backup(settings, engine=engine, storage=storage, force=True)["status"] == "ok"

        # Rotation greift auch gegen den echten Dienst.
        keys = storage.list_keys()
        assert len(keys) == 2

        local = tmp_path / "zurueck.jsonl.gz"
        storage._client.download_file("ah-backups", keys[-1], str(local))

        session = sessionmaker(bind=engine)()
        session.query(Invoice).delete()
        session.commit()
        session.close()

        restore(engine, local)

        session = sessionmaker(bind=engine)()
        invoice = session.get(Invoice, 7)
        assert invoice.total == Decimal("1234.56")
        assert invoice.invoice_date == dt.date(2026, 3, 12)
        session.close()
