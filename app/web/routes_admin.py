"""Admin-Seite: Bexio-Zugangsdaten und Planungsparameter zur Laufzeit verwalten.

Geschuetzt durch HTTP Basic Auth (siehe app/web/auth.py). Das Bexio-Client-Secret
wird nie im Klartext an den Browser zurueckgegeben - das Formularfeld ist immer
leer und ein Absenden ohne Eingabe laesst den gespeicherten Wert unveraendert.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.admin_config import get_effective_config, save_admin_config
from app.config import get_settings
from app.db import get_db
from app.domain.allocation import ALLOCATION_MODE_FULL_MONTH, ALLOCATION_MODE_PRORATA
from app.domain.product_mapping import ErtragsArt, ertragsart_fuer
from app.models import CreditNote, Invoice, LineItem, MonthlyAllocation, OAuthToken, Order, Quote
from app.web.auth import require_admin_auth

_DOCUMENT_MODELS = {
    "quote": Quote,
    "order": Order,
    "invoice": Invoice,
    "credit_note": CreditNote,
}

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin_auth)])
templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def _context(request: Request, db: Session, message: str | None = None) -> dict:
    settings = get_settings()
    effective = get_effective_config(db, settings)
    token = db.get(OAuthToken, 1)
    return {
        "request": request,
        "message": message,
        "effective": effective,
        "redirect_uri": settings.bexio_redirect_uri,
        "token": token,
        "allocation_modes": [ALLOCATION_MODE_PRORATA, ALLOCATION_MODE_FULL_MONTH],
    }


@router.get("")
def admin_page(request: Request, db: Session = Depends(get_db)):
    message = None
    if request.query_params.get("saved"):
        message = "Gespeichert."
    elif request.query_params.get("cleared"):
        message = "Bexio-Zugangsdaten-Override (Client-ID/Secret/API-Token) entfernt, Env-Variablen gelten wieder."
    return templates.TemplateResponse("admin.html", _context(request, db, message))


@router.post("/save")
def admin_save(
    request: Request,
    bexio_client_id: str = Form(""),
    bexio_client_secret: str = Form(""),
    bexio_api_token: str = Form(""),
    allocation_mode: str = Form(ALLOCATION_MODE_PRORATA),
    monthly_budget_chf: str = Form(""),
    current_planning_year: str = Form(""),
    db: Session = Depends(get_db),
):
    budget = None
    if monthly_budget_chf.strip():
        try:
            budget = Decimal(monthly_budget_chf.strip())
        except InvalidOperation:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, "Ungueltiges Budget-Format.")
            )

    year = None
    if current_planning_year.strip():
        try:
            year = int(current_planning_year.strip())
        except ValueError:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, "Ungueltiges Jahr-Format.")
            )

    save_admin_config(
        db,
        bexio_client_id=bexio_client_id.strip() or None,
        bexio_client_secret=bexio_client_secret.strip() or None,
        bexio_api_token=bexio_api_token.strip() or None,
        allocation_mode=allocation_mode,
        monthly_budget_chf=budget,
        current_planning_year=year,
    )
    return RedirectResponse(url="/admin?saved=1", status_code=303)


@router.post("/clear-bexio-credentials")
def admin_clear_bexio(db: Session = Depends(get_db)):
    save_admin_config(db, clear_bexio_credentials=True)
    return RedirectResponse(url="/admin?cleared=1", status_code=303)


@router.get("/debug/line-items")
def admin_debug_line_items(
    document_type: str = "invoice", limit: int = 3, db: Session = Depends(get_db)
) -> dict:
    """Zeigt rohe Bexio-Positionsdaten aus dem Cache, um die Feldnamen-Kandidaten in
    app/sync/service.py (_FIELD_CANDIDATES) gegen die echte API zu verifizieren
    (Konzept Abschnitt 9.1/9.2) - z.B. warum Produktcodes nicht erkannt werden."""
    items = (
        db.query(LineItem)
        .filter_by(document_type=document_type)
        .order_by(LineItem.id.desc())
        .limit(limit)
        .all()
    )
    return {"document_type": document_type, "count": len(items), "samples": [item.raw for item in items]}


@router.get("/debug/documents")
def admin_debug_documents(kind: str = "invoice", limit: int = 2, db: Session = Depends(get_db)) -> dict:
    """Zeigt rohe Bexio-Dokumentdaten (Angebot/Auftrag/Rechnung/Gutschrift) aus dem
    Cache - z.B. um ein PAX-/Teilnehmerzahl-Feld auf Dokumentebene zu finden."""
    model = _DOCUMENT_MODELS.get(kind)
    if model is None:
        return {"error": f"Unbekannte kind={kind!r}, erlaubt: {list(_DOCUMENT_MODELS)}"}
    rows = db.query(model).order_by(model.id.desc()).limit(limit).all()
    return {"kind": kind, "count": len(rows), "samples": [row.raw for row in rows]}


@router.get("/debug/product-summary")
def admin_debug_product_summary(document_type: str = "invoice", db: Session = Depends(get_db)) -> dict:
    """Gruppiert alle synchronisierten Positionen nach Produktcode und zeigt, wie viel
    Umsatz auf nicht zugeordnete Codes (Ertragsart 'sonstiges') entfaellt - beantwortet
    "gibt es Umsaetze ohne Produktzuordnung?" anhand der echten synchronisierten
    Positionen statt nur des Produktkatalogs. document_type="all" fuer alle Dokument-
    typen zusammen (Standard: nur Rechnungen, also effektiv verrechneter Umsatz)."""
    query = db.query(LineItem)
    if document_type != "all":
        query = query.filter_by(document_type=document_type)
    items = query.all()

    groups: dict[str, dict] = {}
    for item in items:
        code = item.product_code or "(kein Produktcode erkannt)"
        ertragsart = ertragsart_fuer(item.product_code)
        group = groups.setdefault(
            code, {"product_code": code, "ertragsart": ertragsart.value, "count": 0, "total": Decimal("0")}
        )
        group["count"] += 1
        group["total"] += item.total or Decimal("0")

    total_revenue = sum((g["total"] for g in groups.values()), Decimal("0"))
    unmapped_revenue = sum(
        (g["total"] for g in groups.values() if g["ertragsart"] == ErtragsArt.SONSTIGES.value), Decimal("0")
    )
    unmapped_share_pct = (
        float((unmapped_revenue / total_revenue * 100).quantize(Decimal("0.1"))) if total_revenue else 0.0
    )

    sorted_groups = sorted(groups.values(), key=lambda g: g["total"], reverse=True)
    return {
        "document_type": document_type,
        "total_positions": len(items),
        "total_revenue": str(total_revenue),
        "unmapped_revenue": str(unmapped_revenue),
        "unmapped_share_pct": unmapped_share_pct,
        "groups": [{**g, "total": str(g["total"])} for g in sorted_groups],
    }


def _raw_decimal(value, fallback: Decimal) -> Decimal:
    if value in (None, ""):
        return fallback
    try:
        return Decimal(str(value))
    except InvalidOperation:
        return fallback


@router.get("/debug/revenue-reconciliation")
def admin_debug_revenue_reconciliation(db: Session = Depends(get_db)) -> dict:
    """Abstimmung gegen Bexio: Anzahl und Brutto-/Netto-Summe aller synchronisierten
    Rechnungen je Rechnungsjahr (nach Rechnungsdatum), plus Umsatz Soll/Ist je
    Leistungsjahr aus den Monats-Allokationen (ungefiltert, alle Status).

    Zum Vergleich die Rechnungsliste in Bexio heranziehen (Summe der Rechnungen des
    Jahres) - NICHT das Dashboard "Fluessige Mittel": das zeigt Zahlungseingaenge
    (Cash, brutto, nach Zahlungsdatum, inkl. Einnahmen ohne Rechnung) und kann daher
    nie mit dem Rechnungsumsatz uebereinstimmen (drei Zeitachsen, Konzept Abschnitt 3).

    status_ids zeigt die Bexio-Statusverteilung (kb_item_status_id) je Jahr - damit
    laesst sich erkennen, ob z.B. Entwuerfe oder stornierte Rechnungen mitgezaehlt
    werden, die in Bexio-Auswertungen fehlen."""
    invoices = db.query(Invoice).all()
    by_invoice_year: dict[int, dict] = {}
    for inv in invoices:
        year = inv.invoice_date.year if inv.invoice_date else 0
        group = by_invoice_year.setdefault(
            year,
            {"invoice_count": 0, "total_net": Decimal("0"), "total_gross": Decimal("0"), "status_ids": {}},
        )
        raw = inv.raw or {}
        group["invoice_count"] += 1
        group["total_net"] += _raw_decimal(raw.get("total_net"), inv.total or Decimal("0"))
        group["total_gross"] += _raw_decimal(raw.get("total_gross"), inv.total or Decimal("0"))
        status_id = str(raw.get("kb_item_status_id"))
        group["status_ids"][status_id] = group["status_ids"].get(status_id, 0) + 1

    allocations = db.query(MonthlyAllocation).all()
    by_service_year: dict[int, dict] = {}
    for alloc in allocations:
        group = by_service_year.setdefault(
            alloc.year, {"umsatz_soll": Decimal("0"), "umsatz_ist": Decimal("0")}
        )
        group["umsatz_soll"] += alloc.umsatz_soll
        group["umsatz_ist"] += alloc.umsatz_ist

    return {
        "hinweis": (
            "Vergleichsbasis in Bexio: Rechnungsliste des Jahres (Summe brutto/netto). "
            "Das Dashboard 'Fluessige Mittel' zeigt Zahlungseingaenge und ist nicht vergleichbar."
        ),
        "invoices_by_invoice_year": {
            str(year): {
                "invoice_count": g["invoice_count"],
                "total_net": str(g["total_net"]),
                "total_gross": str(g["total_gross"]),
                "status_ids": g["status_ids"],
            }
            for year, g in sorted(by_invoice_year.items())
        },
        "allocations_by_service_year": {
            str(year): {"umsatz_soll": str(g["umsatz_soll"]), "umsatz_ist": str(g["umsatz_ist"])}
            for year, g in sorted(by_service_year.items())
        },
    }
