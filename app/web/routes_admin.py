"""Admin-Seite: Bexio-Zugangsdaten, Planungsparameter, Benutzerverwaltung und Log.

Geschuetzt durch Session-Login mit Rolle "admin" (siehe app/auth.py). Das Bexio-
Client-Secret wird nie im Klartext an den Browser zurueckgegeben - das Formularfeld
ist immer leer und ein Absenden ohne Eingabe laesst den gespeicherten Wert unveraendert.
"""
from __future__ import annotations

from decimal import Decimal, InvalidOperation
from pathlib import Path

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app import auth
from app.admin_config import get_effective_config, save_admin_config
from app.audit import log_action, recent_entries
from app.auth import ALL_ROLES, ROLE_ADMIN, ROLE_LABELS, ROLE_MITARBEITER, User, require_admin
from app.config import get_settings
from app.db import get_db
from app.domain.allocation import ALLOCATION_MODE_FULL_MONTH, ALLOCATION_MODE_PRORATA
from app.domain.periods import extract_service_period, nights_between
from app.domain.quotes import COMMENT_FIELDS, quote_status_overview
from app.domain.product_mapping import ErtragsArt, ertragsart_fuer, ist_uebernachtung
from app.models import (
    Booking,
    Contact,
    CreditNote,
    Invoice,
    LineItem,
    MonthlyAllocation,
    OAuthToken,
    Order,
    Quote,
)
from app.web.flash import pop_flash
from app.web.navigation import nav_context
from app.web.formatting import swisstime
from app.web.assets import register as register_assets

_DOCUMENT_MODELS = {
    "quote": Quote,
    "order": Order,
    "invoice": Invoice,
    "credit_note": CreditNote,
}

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])
templates = register_assets(Jinja2Templates(directory=str(Path(__file__).parent / "templates")))
templates.env.filters["swisstime"] = swisstime


def _context(
    request: Request,
    db: Session,
    user: User,
    message: str | None = None,
    message_kind: str | None = None,
) -> dict:
    settings = get_settings()
    effective = get_effective_config(db, settings)
    token = db.get(OAuthToken, 1)
    return {
        "request": request,
        **nav_context(db, user, effective.current_planning_year, "admin", mit_jahr=False),
        "message": message,
        "message_kind": message_kind,
        "effective": effective,
        "redirect_uri": settings.bexio_redirect_uri,
        "token": token,
        "allocation_modes": [ALLOCATION_MODE_PRORATA, ALLOCATION_MODE_FULL_MONTH],
        "current_user": user.username,
        "users": db.query(User).order_by(User.username).all(),
        "audit_entries": recent_entries(db, limit=80),
        "roles": ALL_ROLES,
        "role_labels": ROLE_LABELS,
    }


@router.get("")
def admin_page(request: Request, db: Session = Depends(get_db), user: User = Depends(require_admin)):
    # Meldung aus einem POST/Redirect/GET-Lauf (z.B. Sync) hat Vorrang vor den
    # einfachen Query-Flags der Formulare auf dieser Seite.
    flash = pop_flash(request)
    message = flash["text"] if flash else None
    message_kind = flash["kind"] if flash else None
    if message is None:
        if request.query_params.get("saved"):
            message = "Gespeichert."
        elif request.query_params.get("cleared"):
            message = "Bexio-Zugangsdaten-Override (Client-ID/Secret/API-Token) entfernt, Env-Variablen gelten wieder."
        elif request.query_params.get("user_created"):
            message = "Benutzer angelegt."
        elif request.query_params.get("user_updated"):
            message = "Benutzer aktualisiert."
        elif request.query_params.get("user_deleted"):
            message = "Benutzer gelöscht."
    return templates.TemplateResponse(
        "admin.html", _context(request, db, user, message, message_kind)
    )


@router.post("/save")
def admin_save(
    request: Request,
    bexio_client_id: str = Form(""),
    bexio_client_secret: str = Form(""),
    bexio_api_token: str = Form(""),
    allocation_mode: str = Form(ALLOCATION_MODE_PRORATA),
    monthly_budget_chf: str = Form(""),
    current_planning_year: str = Form(""),
    bed_capacity: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    budget = None
    if monthly_budget_chf.strip():
        try:
            budget = Decimal(monthly_budget_chf.strip())
        except InvalidOperation:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, user, "Ungueltiges Budget-Format.")
            )

    year = None
    if current_planning_year.strip():
        try:
            year = int(current_planning_year.strip())
        except ValueError:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, user, "Ungueltiges Jahr-Format.")
            )

    beds = None
    if bed_capacity.strip():
        try:
            beds = int(bed_capacity.strip())
        except ValueError:
            return templates.TemplateResponse(
                "admin.html", _context(request, db, user, "Ungueltige Bettenzahl.")
            )

    save_admin_config(
        db,
        bexio_client_id=bexio_client_id.strip() or None,
        bexio_client_secret=bexio_client_secret.strip() or None,
        bexio_api_token=bexio_api_token.strip() or None,
        allocation_mode=allocation_mode,
        monthly_budget_chf=budget,
        current_planning_year=year,
        bed_capacity=beds,
    )
    log_action(db, user.username, "config_save", f"mode={allocation_mode} year={year} beds={beds}")
    return RedirectResponse(url="/admin?saved=1", status_code=303)


@router.post("/clear-bexio-credentials")
def admin_clear_bexio(db: Session = Depends(get_db), user: User = Depends(require_admin)):
    save_admin_config(db, clear_bexio_credentials=True)
    log_action(db, user.username, "config_clear_bexio")
    return RedirectResponse(url="/admin?cleared=1", status_code=303)


# ------------------------------------------------------------------ Benutzerverwaltung
@router.post("/users/create")
def admin_create_user(
    request: Request,
    new_email: str = Form(...),
    new_password: str = Form(...),
    new_first_name: str = Form(""),
    new_last_name: str = Form(""),
    new_role: str = Form(ROLE_MITARBEITER),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    # Die E-Mail-Adresse ist zugleich der Login-Name (wie bei Microsoft &Co.).
    email = new_email.strip()
    role = new_role if new_role in ALL_ROLES else ROLE_MITARBEITER
    if not email or not new_password:
        return templates.TemplateResponse(
            "admin.html", _context(request, db, user, "E-Mail-Adresse und Initialpasswort sind erforderlich.")
        )
    if auth.get_user_by_login(db, email):
        return templates.TemplateResponse(
            "admin.html", _context(request, db, user, f"Benutzer «{email}» existiert bereits.")
        )
    # Neu angelegte Benutzer muessen ihr Initialpasswort bei der ersten Anmeldung aendern.
    auth.create_user(
        db, email, new_password, role=role,
        first_name=new_first_name, last_name=new_last_name, email=email,
        must_change_password=True,
    )
    log_action(db, user.username, "user_create", f"{email} ({role})")
    return RedirectResponse(url="/admin?user_created=1#benutzer", status_code=303)


@router.post("/users/{user_id}/update")
def admin_update_user(
    user_id: int,
    request: Request,
    action: str = Form(...),  # "activate" | "deactivate" | "password" | "role" | "profile"
    value: str = Form(""),
    first_name: str = Form(""),
    last_name: str = Form(""),
    email: str = Form(""),
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse(url="/admin#benutzer", status_code=303)

    if action == "deactivate":
        # Letzten aktiven Admin nicht deaktivieren - sonst sperrt man sich aus.
        active_admins = db.query(User).filter(User.role == ROLE_ADMIN, User.is_active).count()
        if target.role == ROLE_ADMIN and active_admins <= 1:
            return templates.TemplateResponse(
                "admin.html",
                _context(request, db, user, "Der letzte aktive Administrator kann nicht deaktiviert werden."),
            )
        target.is_active = False
    elif action == "activate":
        target.is_active = True
    elif action == "password" and value.strip():
        # Admin-Reset: neues Passwort ist ein Initialpasswort -> Zwangswechsel beim Login.
        target.password_hash = auth.hash_password(value.strip())
        target.must_change_password = True
    elif action == "role" and value in ALL_ROLES:
        # Letzten aktiven Admin nicht "wegdegradieren".
        active_admins = db.query(User).filter(User.role == ROLE_ADMIN, User.is_active).count()
        if target.role == ROLE_ADMIN and value != ROLE_ADMIN and active_admins <= 1:
            return templates.TemplateResponse(
                "admin.html",
                _context(request, db, user, "Der letzte aktive Administrator kann die Rolle nicht abgeben."),
            )
        target.role = value
    elif action == "profile":
        new_email = email.strip()
        # E-Mail ist zugleich der Anmeldename -> Eindeutigkeit gegen andere Benutzer pruefen.
        if new_email:
            existing = auth.get_user_by_login(db, new_email)
            if existing and existing.id != target.id:
                return templates.TemplateResponse(
                    "admin.html",
                    _context(request, db, user, f"Die E-Mail «{new_email}» wird bereits von einem anderen Benutzer verwendet."),
                )
            target.email = new_email
            target.username = new_email
        target.first_name = first_name.strip()
        target.last_name = last_name.strip()
    db.commit()
    log_action(db, user.username, "user_update", f"{target.username}: {action} {value if action=='role' else ''}".strip())
    return RedirectResponse(url="/admin?user_updated=1#benutzer", status_code=303)


@router.post("/users/{user_id}/delete")
def admin_delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    user: User = Depends(require_admin),
):
    target = db.get(User, user_id)
    if target is None:
        return RedirectResponse(url="/admin#benutzer", status_code=303)
    # Sich selbst nicht loeschen und den letzten aktiven Admin nicht loeschen.
    if target.id == user.id:
        return templates.TemplateResponse(
            "admin.html", _context(request, db, user, "Sie können sich nicht selbst löschen.")
        )
    if target.role == ROLE_ADMIN:
        active_admins = db.query(User).filter(User.role == ROLE_ADMIN, User.is_active).count()
        if active_admins <= 1:
            return templates.TemplateResponse(
                "admin.html",
                _context(request, db, user, "Der letzte aktive Administrator kann nicht gelöscht werden."),
            )
    username = target.username
    db.delete(target)
    db.commit()
    log_action(db, user.username, "user_delete", username)
    return RedirectResponse(url="/admin?user_deleted=1#benutzer", status_code=303)


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
            {
                "invoice_count": 0,
                "total_net": Decimal("0"),
                "total_gross": Decimal("0"),
                "total_net_effektiv": Decimal("0"),
                "status_ids": {},
            },
        )
        raw = inv.raw or {}
        net = _raw_decimal(raw.get("total_net"), inv.total or Decimal("0"))
        group["invoice_count"] += 1
        group["total_net"] += net
        group["total_gross"] += _raw_decimal(raw.get("total_gross"), inv.total or Decimal("0"))
        status_id = raw.get("kb_item_status_id")
        # Effektiv = ohne Entwuerfe (7) und Stornos (19) - vergleichbar mit dem, was
        # in Bexio-Auswertungen als verrechneter Umsatz erscheint.
        if status_id not in (7, 19):
            group["total_net_effektiv"] += net
        group["status_ids"][str(status_id)] = group["status_ids"].get(str(status_id), 0) + 1

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
                "total_net_effektiv": str(g["total_net_effektiv"]),
                "status_ids": g["status_ids"],
            }
            for year, g in sorted(by_invoice_year.items())
        },
        "allocations_by_service_year": {
            str(year): {"umsatz_soll": str(g["umsatz_soll"]), "umsatz_ist": str(g["umsatz_ist"])}
            for year, g in sorted(by_service_year.items())
        },
    }


# Welche Rohfelder eines Bexio-Belegs koennten ein Datum enthalten? Zeigt der
# Diagnose-Endpunkt mit an, damit erkennbar wird, ob das Anlassdatum doch
# strukturiert geliefert wird (statt nur im Titel zu stehen).
def _date_like_raw_fields(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if value not in (None, "", [], {})
        and any(token in key.lower() for token in ("date", "valid", "period", "von", "bis", "from", "to"))
    }


@router.get("/debug/zeitraum-herkunft")
def admin_debug_zeitraum_herkunft(
    status: str = "beauftragt", limit: int = 50, db: Session = Depends(get_db)
) -> dict:
    """Woher stammt der Leistungszeitraum je Buchung - aus dem Belegtitel oder nur
    aus dem Dokumentdatum?

    Steht im Titel kein Datum, faellt app/domain/periods.py auf das Dokumentdatum
    zurueck (bei "beauftragt" also das Auftragsdatum). Solche Buchungen landen im
    Planjahr des Auftrags statt in dem des Anlasses. Dieser Endpunkt zaehlt die
    Faelle und zeigt Titel sowie alle datumsaehnlichen Bexio-Rohfelder des Auftrags,
    damit beurteilt werden kann, ob sich das echte Anlassdatum anders beschaffen
    laesst. status="all" fuer alle Status.
    """
    query = db.query(Booking)
    if status != "all":
        query = query.filter_by(status=status)
    bookings = query.all()

    aus_titel, aus_dokumentdatum = [], []
    for booking in bookings:
        # Ohne Fallback parsen: liefert das den Zeitraum, stammt er aus dem Titel.
        start, _ = extract_service_period(booking.anlass, None)
        eintrag = {
            "booking_key": booking.booking_key,
            "status": booking.status,
            "kunde": booking.kunde,
            "anlass": booking.anlass,
            "service_start": booking.service_start.isoformat() if booking.service_start else None,
            "service_end": booking.service_end.isoformat() if booking.service_end else None,
        }
        if start is not None:
            aus_titel.append(eintrag)
            continue
        order = db.get(Order, booking.order_id) if booking.order_id else None
        eintrag["auftragsdatum"] = order.order_date.isoformat() if order and order.order_date else None
        eintrag["bexio_rohfelder_mit_datum"] = _date_like_raw_fields(order.raw if order else None)
        aus_dokumentdatum.append(eintrag)

    betroffen = len(aus_dokumentdatum)
    gesamt = len(bookings)
    return {
        "status_filter": status,
        "buchungen_gesamt": gesamt,
        "zeitraum_aus_titel": len(aus_titel),
        "zeitraum_nur_aus_dokumentdatum": betroffen,
        "anteil_betroffen_pct": round(betroffen / gesamt * 100, 1) if gesamt else 0.0,
        "betroffene_buchungen": aus_dokumentdatum[:limit],
    }


def _freitext_felder(raw: dict | None) -> dict:
    """Alle Textfelder eines Belegs, die nach Freitext aussehen - unabhaengig davon,
    ob der Feldname in COMMENT_FIELDS bereits bekannt ist. So faellt auf, unter
    welchem Namen Bexio den internen Kommentar tatsaechlich liefert."""
    if not isinstance(raw, dict):
        return {}
    return {
        key: value
        for key, value in raw.items()
        if isinstance(value, str) and len(value.strip()) > 3 and not key.endswith("_id")
    }


@router.get("/debug/angebot-status")
def admin_debug_angebot_status(beispiele: int = 2, db: Session = Depends(get_db)) -> dict:
    """Welche Angebots-Status kommen vor, und wo steht der interne Kommentar?

    Beantwortet zwei Fragen, die nur die echten Daten beantworten koennen:
    1. Welche kb_item_status_id bedeutet "abgelehnt"? -> QUOTE_DECLINED_STATUS_IDS
    2. In welchem Feld liefert Bexio den internen Kommentar? -> COMMENT_FIELDS
       in app/domain/quotes.py. Findet sich dort gar kein Text, legt Bexio den
       Kommentar nicht im Beleg selbst ab und muss separat geladen werden.
    """
    settings = get_settings()
    uebersicht = quote_status_overview(db)
    for eintrag in uebersicht:
        eintrag["summe"] = str(eintrag["summe"])

    quotes = db.query(Quote).order_by(Quote.id.desc()).limit(beispiele).all()
    orders = db.query(Order).order_by(Order.id.desc()).limit(beispiele).all()
    return {
        "aktuell_als_abgelehnt_konfiguriert": sorted(settings.quote_declined_status_id_set),
        "bekannte_kommentarfelder": list(COMMENT_FIELDS),
        "status_uebersicht": uebersicht,
        "freitext_in_angeboten": [
            {"document_nr": q.document_nr, "felder": _freitext_felder(q.raw)} for q in quotes
        ],
        "freitext_in_auftraegen": [
            {"document_nr": o.document_nr, "felder": _freitext_felder(o.raw)} for o in orders
        ],
    }


def _pax_positionen(db: Session, document_type: str, document_id: int | None) -> dict | None:
    """Alle Positionen eines Belegs, getrennt nach "zaehlt fuer PAX" und "zaehlt nicht"."""
    if document_id is None:
        return None
    items = (
        db.query(LineItem)
        .filter_by(document_type=document_type, document_id=document_id)
        .order_by(LineItem.id)
        .all()
    )
    zaehlend = [i for i in items if ist_uebernachtung(i.product_code)]
    return {
        "document_id": document_id,
        "mengensumme_uebernachtungen": str(
            sum((i.quantity or Decimal("0") for i in zaehlend), Decimal("0"))
        ),
        "zaehlt_fuer_pax": [
            {
                "position_id": i.position_bexio_id,
                "produktcode": i.product_code,
                "text": (i.description or "")[:120],
                "menge": str(i.quantity),
            }
            for i in zaehlend
        ],
        "zaehlt_nicht": [
            {
                "produktcode": i.product_code,
                "text": (i.description or "")[:120],
                "menge": str(i.quantity),
            }
            for i in items
            if not ist_uebernachtung(i.product_code)
        ],
    }


@router.get("/debug/pax-herleitung")
def admin_debug_pax_herleitung(suche: str = "", limit: int = 5, db: Session = Depends(get_db)) -> dict:
    """Warum hat diese Buchung diese PAX-Zahl?

    Bexio liefert kein Teilnehmerfeld. PAX wird deshalb hergeleitet: Menge aller
    Uebernachtungspositionen geteilt durch die physischen Naechte des Zeitraums
    (siehe _document_metrics in app/sync/service.py). Stimmt die PAX-Zahl nicht,
    liegt es an einem der beiden Werte - und hier steht, aus welchen Positionen
    sie entstanden sind.

    suche: Teil von Kundenname, Anlass oder Buchungsnummer.
    """
    query = db.query(Booking)
    if suche:
        muster = f"%{suche.strip()}%"
        query = query.filter(
            Booking.kunde.ilike(muster)
            | Booking.anlass.ilike(muster)
            | Booking.booking_key.ilike(muster)
        )
    buchungen = query.order_by(Booking.service_start.desc().nullslast()).limit(limit).all()

    ergebnis = []
    for b in buchungen:
        physische_naechte = (
            nights_between(b.service_start, b.service_end)
            if b.service_start and b.service_end
            else None
        )
        ergebnis.append(
            {
                "buchung": b.booking_key,
                "kunde": b.kunde,
                "anlass": b.anlass,
                "zeitraum": {
                    "von": b.service_start.isoformat() if b.service_start else None,
                    "bis": b.service_end.isoformat() if b.service_end else None,
                    "physische_naechte": physische_naechte,
                    "herkunft": b.service_period_source,
                },
                "gespeichert": {
                    "pax_soll": b.pax_soll,
                    "pax_ist": b.pax_ist,
                    "pax_naechte_soll": str(b.nights_soll),
                    "pax_naechte_ist": str(b.nights_ist),
                },
                "rechnung": (
                    f"PAX = Mengensumme {b.nights_soll} / {physische_naechte} Naechte"
                    if physische_naechte
                    else "kein Zeitraum -> keine Herleitung moeglich"
                ),
                "positionen": {
                    "angebot": _pax_positionen(db, "quote", b.quote_id),
                    "auftrag": _pax_positionen(db, "order", b.order_id),
                    "rechnung": _pax_positionen(db, "invoice", b.invoice_id),
                },
            }
        )
    return {
        "hinweis": (
            "zaehlt_fuer_pax listet die Positionen, deren Menge als PAX-Naechte gilt "
            "(NIGHT_RELEVANT_CODES in app/domain/product_mapping.py). Ist die "
            "Mengensumme doppelt so hoch wie erwartet, steht dort meist eine Position "
            "zu viel oder eine Menge als Personen x Tage statt Personen x Naechte."
        ),
        "treffer": len(ergebnis),
        "buchungen": ergebnis,
    }


# Felder eines Bexio-Belegs, die auf einen Kontakt zeigen. contact_id ist der
# Beleg-Empfaenger (danach filtert Bexio die Register "Angebote"/"Auftraege" am
# Kontakt), contact_sub_id die Kontaktperson/Verknuepfung darunter. Die Anwendung
# liest nur contact_id - stimmt der angezeigte Kunde nicht, muss sich das hier zeigen.
_KONTAKT_FELDER = ("contact_id", "contact_sub_id", "user_id", "owner_id")


def _beleg_kontaktfelder(raw: dict | None) -> dict:
    if not isinstance(raw, dict):
        return {}
    return {feld: raw.get(feld) for feld in _KONTAKT_FELDER if feld in raw}


@router.get("/debug/kunde")
def admin_debug_kunde(suche: str = "", limit: int = 20, db: Session = Depends(get_db)) -> dict:
    """Warum steht bei dieser Buchung dieser Kunde?

    Der Kundenname einer Buchung stammt aus dem Kontakt, auf den der Beleg per
    contact_id zeigt - dieselbe Zuordnung, nach der Bexio die Register "Angebote"
    und "Auftraege" am Kontakt fuellt. Weichen die beiden voneinander ab, zeigt
    diese Ausgabe, welchen Kontakt Bexio am Beleg tatsaechlich liefert und welchen
    Namen dieser Kontakt bei uns traegt.

    suche: Teil eines Kontaktnamens, einer Kontaktnummer oder eines Anlasses.
    """
    muster = f"%{suche.strip()}%" if suche.strip() else "%"

    kontakte = (
        db.query(Contact)
        .filter(Contact.name.ilike(muster) | Contact.contact_nr.ilike(muster))
        .order_by(Contact.name)
        .limit(limit)
        .all()
    )
    kontakt_ids = [k.id for k in kontakte]

    def belege(modell, typ: str) -> list[dict]:
        if not kontakt_ids:
            return []
        return [
            {
                "typ": typ,
                "nummer": row.document_nr,
                "titel": row.title,
                "unser_contact_id": row.contact_id,
                "bexio_kontaktfelder": _beleg_kontaktfelder(row.raw),
            }
            for row in db.query(modell).filter(modell.contact_id.in_(kontakt_ids)).all()
        ]

    gefunden = belege(Quote, "angebot") + belege(Order, "auftrag") + belege(Invoice, "rechnung")

    buchungen = (
        db.query(Booking)
        .filter(Booking.kunde.ilike(muster) | Booking.anlass.ilike(muster))
        .order_by(Booking.service_start.desc().nullslast())
        .limit(limit)
        .all()
    )

    return {
        "hinweis": (
            "Steht ein Beleg hier unter einem Kontakt, bei dem Bexio ihn nicht "
            "anzeigt, weicht contact_id am Beleg von dem ab, was die Bexio-Oberflaeche "
            "zeigt - dann steht die Antwort in bexio_kontaktfelder (contact_sub_id = "
            "verknuepfte Kontaktperson). Ist die Liste der Belege leer, hat die "
            "Anwendung diesem Kontakt gar nichts zugeordnet."
        ),
        "kontakte": [
            {
                "id": k.id,
                "nummer": k.contact_nr,
                "name": k.name,
                "bexio_name_1": (k.raw or {}).get("name_1"),
                "bexio_name_2": (k.raw or {}).get("name_2"),
                "bexio_contact_type_id": (k.raw or {}).get("contact_type_id"),
            }
            for k in kontakte
        ],
        "belege_dieser_kontakte": gefunden,
        "buchungen": [
            {
                "buchung": b.booking_key,
                "kunde": b.kunde,
                "anlass": b.anlass,
                "contact_id": b.contact_id,
                "status": b.status,
                "verkettung": b.linked_by,
            }
            for b in buchungen
        ],
    }
