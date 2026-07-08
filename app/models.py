"""SQLAlchemy-Modelle.

Zwei Gruppen von Tabellen:
1. Rohdaten-Cache (Contact, Quote, Order, Invoice, CreditNote, LineItem) - 1:1-Spiegel
   der relevanten Bexio-Felder, ergaenzt um das komplette Rohobjekt als JSON fuer
   Nachvollziehbarkeit und um spaeter weitere Felder erschliessen zu koennen, ohne
   eine Migration zu brauchen.
2. Abgeleitete Daten (Booking, MonthlyAllocation) - Ergebnis der Verkettung und der
   periodengerechten Abgrenzung (siehe app/matching und app/domain). Diese Tabellen
   werden bei jedem Transformationslauf neu berechnet (siehe app/sync/service.py).
"""
from __future__ import annotations

import datetime as dt
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Date,
    DateTime,
    ForeignKey,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


class User(Base):
    """Anwender-Konto mit Rolle. role="admin" hat Vollzugriff (inkl. Benutzer-
    verwaltung, Bexio-Config, Log); role="user" sieht nur Dashboard/Auswertung.
    Passwoerter werden nur als Hash gespeichert (siehe app/auth.py)."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String, unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    role: Mapped[str] = mapped_column(String, default="user")  # "admin" | "user"
    is_active: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class AuditLog(Base):
    """Protokoll sicherheits-/nachvollziehbarkeitsrelevanter Aktionen (Login, Sync,
    Config-/Budget-Aenderung, Benutzerverwaltung). Siehe app/audit.py."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    timestamp: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc), index=True
    )
    username: Mapped[str] = mapped_column(String, default="")
    action: Mapped[str] = mapped_column(String, default="")
    detail: Mapped[str] = mapped_column(String, default="")


class OAuthToken(Base):
    """Singleton-Tabelle (id=1) fuer den aktuellen Bexio-Token-Satz."""

    __tablename__ = "oauth_token"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    access_token: Mapped[str] = mapped_column(String, nullable=False)
    refresh_token: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    scope: Mapped[str] = mapped_column(String, default="")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class AdminConfig(Base):
    """Singleton-Tabelle (id=1) fuer per Admin-Seite ueberschreibbare Einstellungen.

    Jedes Feld ist optional: NULL bedeutet "kein Override", die Env-Variable aus
    app/config.py gilt weiter (siehe app/admin_config.py, get_effective_config()).
    """

    __tablename__ = "admin_config"

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    bexio_client_id: Mapped[str | None] = mapped_column(String, nullable=True)
    bexio_client_secret: Mapped[str | None] = mapped_column(String, nullable=True)
    # Statischer Bexio-API-Token (Bearer), Alternative zum OAuth2-Flow oben - siehe
    # Konzept-Anhang/README "Admin-Seite". Ist er gesetzt, hat er Vorrang vor OAuth2.
    bexio_api_token: Mapped[str | None] = mapped_column(String, nullable=True)
    allocation_mode: Mapped[str | None] = mapped_column(String, nullable=True)
    monthly_budget_chf: Mapped[Decimal | None] = mapped_column(Numeric(12, 2), nullable=True)
    current_planning_year: Mapped[int | None] = mapped_column(nullable=True)
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[int] = mapped_column(primary_key=True)  # Bexio contact id
    contact_nr: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, default="")
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class Quote(Base):
    """Angebot (kb_offer)."""

    __tablename__ = "quotes"

    id: Mapped[int] = mapped_column(primary_key=True)  # Bexio id
    document_nr: Mapped[str] = mapped_column(String, index=True)  # AN-00046
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    quote_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    valid_until: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    kb_item_status_id: Mapped[int | None] = mapped_column(nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    line_items: Mapped[list["LineItem"]] = relationship(
        primaryjoin="and_(LineItem.document_type=='quote', "
        "foreign(LineItem.document_id)==Quote.id)",
        viewonly=True,
    )


class Order(Base):
    """Auftrag (kb_order) - Primaerschluessel einer Buchung, siehe Konzept Abschnitt 2."""

    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(primary_key=True)  # Bexio id
    document_nr: Mapped[str] = mapped_column(String, index=True)  # AU-00068
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    order_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Von Bexio geliefertes Referenzfeld auf das Ursprungsangebot, falls vorhanden.
    # Muss in Phase 1 gegen die reale API verifiziert werden (siehe Konzept Abschnitt 2).
    source_quote_bexio_id: Mapped[int | None] = mapped_column(nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    line_items: Mapped[list["LineItem"]] = relationship(
        primaryjoin="and_(LineItem.document_type=='order', "
        "foreign(LineItem.document_id)==Order.id)",
        viewonly=True,
    )


class Invoice(Base):
    """Rechnung (kb_invoice)."""

    __tablename__ = "invoices"

    id: Mapped[int] = mapped_column(primary_key=True)  # Bexio id
    document_nr: Mapped[str] = mapped_column(String, index=True)  # RE-00272
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    invoice_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    payment_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    # Von Bexio geliefertes Referenzfeld auf den Ursprungsauftrag, falls vorhanden.
    source_order_bexio_id: Mapped[int | None] = mapped_column(nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    line_items: Mapped[list["LineItem"]] = relationship(
        primaryjoin="and_(LineItem.document_type=='invoice', "
        "foreign(LineItem.document_id)==Invoice.id)",
        viewonly=True,
    )


class CreditNote(Base):
    """Gutschrift (kb_credit_voucher)."""

    __tablename__ = "credit_notes"

    id: Mapped[int] = mapped_column(primary_key=True)  # Bexio id
    document_nr: Mapped[str] = mapped_column(String, index=True)  # GS-00005
    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    title: Mapped[str] = mapped_column(String, default="")
    credit_note_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    reference_invoice_bexio_id: Mapped[int | None] = mapped_column(nullable=True)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)
    synced_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )


class LineItem(Base):
    """Positionszeile eines Dokuments (Quote/Order/Invoice/CreditNote).

    document_type + document_id bilden zusammen den logischen Fremdschluessel, da die
    vier Dokumenttypen bei Bexio getrennte ID-Raeume haben.
    """

    __tablename__ = "line_items"
    __table_args__ = (
        UniqueConstraint("document_type", "document_id", "position_bexio_id", name="uq_line_item"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    document_type: Mapped[str] = mapped_column(String)  # quote|order|invoice|credit_note
    document_id: Mapped[int] = mapped_column()  # Bexio id des Elterndokuments
    position_bexio_id: Mapped[int | None] = mapped_column(nullable=True)
    product_code: Mapped[str | None] = mapped_column(String, nullable=True)
    description: Mapped[str] = mapped_column(String, default="")
    quantity: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    unit: Mapped[str | None] = mapped_column(String, nullable=True)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    total: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    raw: Mapped[dict] = mapped_column(JSON, default=dict)


class Booking(Base):
    """Abgeleitete, verkettete Buchung - eine Zeile in der Detailplanung (Konzept Abschnitt 5).

    Wird bei jedem Transformationslauf komplett neu berechnet (siehe app/sync/service.py),
    keyed ueber booking_key (Auftragsnummer, ersatzweise Angebotsnummer bei reiner Pipeline).
    """

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    booking_key: Mapped[str] = mapped_column(String, unique=True, index=True)

    quote_id: Mapped[int | None] = mapped_column(ForeignKey("quotes.id"), nullable=True)
    order_id: Mapped[int | None] = mapped_column(ForeignKey("orders.id"), nullable=True)
    invoice_id: Mapped[int | None] = mapped_column(ForeignKey("invoices.id"), nullable=True)
    credit_note_id: Mapped[int | None] = mapped_column(ForeignKey("credit_notes.id"), nullable=True)

    contact_id: Mapped[int | None] = mapped_column(ForeignKey("contacts.id"), nullable=True)
    kunde: Mapped[str] = mapped_column(String, default="")
    anlass: Mapped[str] = mapped_column(String, default="")

    # linked_by: "reference" (harte Bexio-Referenz) | "heuristic" (Kundennr+Titel+Zeitraum) | "none"
    linked_by: Mapped[str] = mapped_column(String, default="none")

    # status: nur_angebot | beauftragt | verrechnet | storniert
    status: Mapped[str] = mapped_column(String, default="nur_angebot")

    service_start: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    service_end: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    invoice_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)
    payment_date: Mapped[dt.date | None] = mapped_column(Date, nullable=True)

    pax_soll: Mapped[int | None] = mapped_column(nullable=True)
    pax_ist: Mapped[int | None] = mapped_column(nullable=True)

    # Achtung: hier auf Buchungsebene sind das PAX-Naechte (Bexio-Positionsmenge,
    # bereits Personen x Naechte) - reiner Zwischenwert fuer die Monatsverteilung
    # (app/domain/allocation.py). Die physischen (Kalender-)Naechte je Monat landen
    # erst in MonthlyAllocation.nights_soll/ist weiter unten.
    nights_soll: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    nights_ist: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    umsatz_soll: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    umsatz_ist: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    comment: Mapped[str] = mapped_column(String, default="")

    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )

    monthly_allocations: Mapped[list["MonthlyAllocation"]] = relationship(
        back_populates="booking", cascade="all, delete-orphan"
    )

    @property
    def pax_diff(self) -> int | None:
        if self.pax_soll is None or self.pax_ist is None:
            return None
        return self.pax_ist - self.pax_soll

    @property
    def umsatz_diff(self) -> Decimal:
        return self.umsatz_ist - self.umsatz_soll


class MonthlyAllocation(Base):
    """Periodengerechte Verteilung einer Buchung auf einen Leistungsmonat (pro rata oder voll).

    Siehe Konzept Abschnitt 3. Pro Buchung i.d.R. 1 Zeile (einmonatig) oder 2+ Zeilen
    (monatsuebergreifend).
    """

    __tablename__ = "monthly_allocations"
    __table_args__ = (UniqueConstraint("booking_id", "year", "month", name="uq_monthly_allocation"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id"))
    year: Mapped[int] = mapped_column()
    month: Mapped[int] = mapped_column()  # 1-12

    # Physische (Kalender-)Naechte, die in diesen Monat fallen - unabhaengig von PAX.
    # Fuer PAX-Naechte (Personen x Naechte) siehe pax_nights_soll/ist weiter unten.
    nights_soll: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    nights_ist: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    days: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    pax_nights_soll: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    pax_nights_ist: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    umsatz_soll: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))
    umsatz_ist: Mapped[Decimal] = mapped_column(Numeric(12, 2), default=Decimal("0"))

    booking: Mapped[Booking] = relationship(back_populates="monthly_allocations")


class MonthlyBudget(Base):
    """Budget-Override pro Jahr+Monat (Konzept Abschnitt 9.4: Budget "fix oder
    konfigurierbar"). Ohne Eintrag hier gilt der Default aus Settings/AdminConfig
    (app/admin_config.py) einheitlich fuer alle Monate - direkt in der
    Jahresuebersicht editierbar (app/web/routes_dashboard.py)."""

    __tablename__ = "monthly_budgets"
    __table_args__ = (UniqueConstraint("year", "month", name="uq_monthly_budget"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    year: Mapped[int] = mapped_column()
    month: Mapped[int] = mapped_column()  # 1-12
    budget_chf: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: dt.datetime.now(dt.timezone.utc)
    )
