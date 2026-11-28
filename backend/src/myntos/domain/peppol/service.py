"""Who may register a company with Peppol, send its invoices, and receive/book incoming EHFs.

Three acts, with different rules behind each:

- **Registering and toggling receiving** are an admin's (``ORGANIZATION_MANAGE``)
  and never a key's. The company must have been confirmed through Altinn first:
  Ademico answers to the deployment's credentials whatever orgnr is named, so
  Myntos is the wall proving the caller may act for that business. Receiving
  also enforces a single active inbox per orgnr across the whole database so an
  incoming supplier invoice can never be routed to the wrong set of books.
- **Sending** is anybody's who may write the books, once per issued document,
  and for an AI agent's key it always waits for a person's yes (``key_send``
  on the route). The one exception, the owner's decision of 2026-11-26
  (docs/agents-and-api.md rule 15): a server's integration key, made by an
  owner, bound to IP addresses and limited to invoicing, sends an invoice on
  its own.
- **Receiving and booking** store incoming UBL XML verbatim with its SHA-256
  digest (append-only for bokføringsloven § 13) and book an incoming EHF into
  the ledger as a voucher with a readable PDF archived in the bilag store.

Nothing here calls Ademico or Peppol SMP. The endpoints in
:mod:`myntos.api.v1.peppol` and the webhook and sweep in
:mod:`myntos.integrations.peppol_sync` do, between these checks and storing
what came back (docs/ARCHITECTURE.md, «Backend layout»).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.core.clock import utc_now
from myntos.domain.bilag import service as bilag_service
from myntos.domain.faktura.errors import InvoiceNotFoundError
from myntos.domain.faktura.models import SalesDocument
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.errors import InvalidOrgnrError
from myntos.domain.ledger.models import Company, Party, PartyType, Transaction
from myntos.domain.ledger.orgnr import validate_orgnr
from myntos.domain.ledger.service import PaymentMethod
from myntos.domain.peppol import incoming_pdf, ubl_reader
from myntos.domain.peppol.errors import (
    AlreadySentError,
    CompanyAddressMissingError,
    CompanyNotConfirmedError,
    IncomingInvoiceAlreadyBookedError,
    IncomingInvoiceDismissedError,
    IncomingInvoiceInvalidError,
    IncomingInvoiceNotFoundError,
    NotRegisteredError,
    NotSendableError,
    ReceivingAlreadyClaimedError,
    TransmissionNotFoundError,
)
from myntos.domain.peppol.models import (
    IncomingDocumentType,
    IncomingStatus,
    PeppolIncomingDocument,
    PeppolRegistration,
    PeppolTransmission,
    TransmissionStatus,
)
from myntos.storage.base import DocumentStorage

# --- Registration ------------------------------------------------------------------


@dataclass(frozen=True)
class Standing:
    """Whether the company can send and receive over Peppol, and what step is missing."""

    # The deployment has an access point configured at all.
    available: bool
    # Confirmed through Altinn, which registering requires.
    company_confirmed: bool
    registration: PeppolRegistration | None

    @property
    def registered(self) -> bool:
        return self.registration is not None

    @property
    def verified(self) -> bool:
        return self.registration is not None and self.registration.is_verified

    @property
    def can_send(self) -> bool:
        return self.available and self.verified

    @property
    def can_receive(self) -> bool:
        return (
            self.available and self.registration is not None and self.registration.receiving_enabled
        )


async def get_registration(db: AsyncSession, *, company: Company) -> PeppolRegistration | None:
    registration: PeppolRegistration | None = await db.scalar(
        select(PeppolRegistration).where(PeppolRegistration.company_id == company.id)
    )
    return registration


def mark_verified(registration: PeppolRegistration) -> None:
    """Stamp the registration as ready to send."""
    if registration.verified_at is None:
        registration.verified_at = utc_now()


def require_registrable(company: Company) -> None:
    """Refuse a company that has not been confirmed through Altinn.

    Without that, a new account that typed somebody else's orgnr would be one
    click from sending or receiving invoices in that business's name.
    """
    if not company.is_verified:
        raise CompanyNotConfirmedError(
            "selskapet må bekreftes med ID-porten før det kan registreres for e-faktura, "
            "så bare de som kan handle for selskapet kan sende og motta fakturaer i dets navn"
        )


def registered_address(company: Company) -> tuple[str, str, str]:
    """The business address the access point registers the company under."""
    if not (company.address_line and company.postal_code and company.city):
        raise CompanyAddressMissingError(
            "tilgangspunktet trenger selskapets forretningsadresse. "
            "Hent opplysningene fra Enhetsregisteret på nytt under Selskap"
        )
    return company.address_line, company.postal_code, company.city


async def require_receiving_claimable(db: AsyncSession, *, company: Company) -> None:
    """Refuse enabling receiving when another active Myntos company holds this orgnr's inbox.

    Incoming supplier invoices arrive from Peppol addressed only to
    ``0192:<orgnr>``. Allowing two active sets of books to claim the same
    orgnr's inbox would make routing ambiguous or leak supplier invoices across
    accounts.
    """
    other = await db.scalar(
        select(PeppolRegistration.id)
        .join(Company, Company.id == PeppolRegistration.company_id)
        .where(
            PeppolRegistration.orgnr == company.orgnr,
            PeppolRegistration.receiving_enabled.is_(True),
            PeppolRegistration.company_id != company.id,
            Company.closed_at.is_(None),
        )
    )
    if other is not None:
        raise ReceivingAlreadyClaimedError(
            "dette organisasjonsnummeret tar allerede imot e-faktura i et annet regnskap i "
            "Myntos. Slå av mottak der først før du slår det på her"
        )


async def record_registration(
    db: AsyncSession,
    *,
    company: Company,
    registration: PeppolRegistration | None,
    remote_company_id: str,
    remote_registration_id: str | None = None,
    verified: bool = True,
    receiving_enabled: bool | None = None,
) -> PeppolRegistration:
    """Keep the company's record at Ademico, found or created."""
    if registration is None:
        registration = PeppolRegistration(
            company_id=company.id,
            remote_company_id=remote_company_id,
            remote_registration_id=remote_registration_id,
            orgnr=company.orgnr,
            receiving_enabled=bool(receiving_enabled),
        )
        db.add(registration)
    else:
        registration.remote_company_id = remote_company_id
        if remote_registration_id is not None:
            registration.remote_registration_id = remote_registration_id
        registration.orgnr = company.orgnr
    if verified:
        mark_verified(registration)
    if receiving_enabled is not None:
        set_receiving_state(registration, enabled=receiving_enabled)
    await db.flush()
    return registration


def set_receiving_state(registration: PeppolRegistration, *, enabled: bool) -> None:
    """Update the stored SMP/ELMA receiving state for ``registration``."""
    registration.receiving_enabled = enabled
    registration.receiving_enabled_at = utc_now() if enabled else None


async def find_receiving_company(db: AsyncSession, *, orgnr: str) -> Company | None:
    """The single active Myntos company configured to receive EHF for ``orgnr``, if any."""
    company: Company | None = await db.scalar(
        select(Company)
        .join(PeppolRegistration, PeppolRegistration.company_id == Company.id)
        .where(
            PeppolRegistration.orgnr == orgnr,
            PeppolRegistration.receiving_enabled.is_(True),
            Company.closed_at.is_(None),
        )
    )
    return company


# --- Sending -----------------------------------------------------------------------


@dataclass(frozen=True)
class Sendable:
    """An issued document that may go, and the registration it goes under."""

    document: SalesDocument
    registration: PeppolRegistration
    xml: str
    buyer_orgnr: str


async def prepare_send(db: AsyncSession, *, company: Company, document_pk: int) -> Sendable:
    """Check that the document may go, holding its row until the request ends.

    The document row is locked first, so two presses of the button in two tabs
    cannot both get past the check for an earlier transmission: the second waits
    for the first to commit, then finds it.
    """
    document = await db.scalar(
        select(SalesDocument)
        .where(SalesDocument.id == document_pk, SalesDocument.company_id == company.id)
        .with_for_update()
    )
    if document is None:
        raise InvoiceNotFoundError("finner ikke fakturaen")
    if not document.is_issued or document.ehf_xml is None:
        raise NotSendableError(
            "fakturaen er ikke utstedt ennå. Utsted den først, så sendes EHF-en som ble laget da"
        )
    if not document.buyer_orgnr:
        raise NotSendableError(
            "kunden har ikke organisasjonsnummer på fakturaen, og over Peppol er det adressen. "
            "Send PDF-en til kunden selv"
        )

    registration = await get_registration(db, company=company)
    if registration is None:
        raise NotRegisteredError(
            "selskapet er ikke registrert for e-faktura ennå. "
            "En administrator gjør det under Selskap, E-faktura"
        )

    earlier = await db.scalar(
        select(PeppolTransmission).where(
            PeppolTransmission.document_pk == document.id,
            PeppolTransmission.status != TransmissionStatus.FAILED,
        )
    )
    if earlier is not None:
        raise AlreadySentError(
            "fakturaen er allerede sendt som EHF. En faktura som sendes to ganger, "
            "kan bli betalt to ganger"
        )
    return Sendable(
        document=document,
        registration=registration,
        xml=document.ehf_xml,
        buyer_orgnr=document.buyer_orgnr,
    )


def require_verified(registration: PeppolRegistration) -> None:
    if not registration.is_verified:
        raise NotRegisteredError(
            "registreringen for e-faktura er ikke fullført. "
            "En administrator fullfører den under Selskap, E-faktura"
        )


async def record_transmission(
    db: AsyncSession,
    *,
    company: Company,
    document: SalesDocument,
    remote_document_id: str,
    recipient: str,
    status: TransmissionStatus,
    failure_category: str | None,
    failure_message: str | None,
) -> PeppolTransmission:
    """Keep what the access point answered to a send."""
    transmission = PeppolTransmission(
        company_id=company.id,
        document_pk=document.id,
        remote_document_id=remote_document_id,
        recipient=recipient,
        status=status,
        failure_category=failure_category,
        failure_message=failure_message,
        sent_at=utc_now(),
    )
    db.add(transmission)
    await db.flush()
    return transmission


async def latest_transmissions(
    db: AsyncSession, *, company: Company, document_pks: Sequence[int]
) -> dict[int, PeppolTransmission]:
    """The most recent transmission of each document, as stored."""
    if not document_pks:
        return {}
    rows = await db.scalars(
        select(PeppolTransmission)
        .where(
            PeppolTransmission.company_id == company.id,
            PeppolTransmission.document_pk.in_(document_pks),
        )
        .order_by(PeppolTransmission.id)
    )
    # Ascending, so the last one written for each document wins.
    return {row.document_pk: row for row in rows}


async def latest_transmission(
    db: AsyncSession, *, company: Company, document_pk: int
) -> PeppolTransmission:
    """The document's latest transmission, as stored."""
    exists = await db.scalar(
        select(SalesDocument.id).where(
            SalesDocument.id == document_pk, SalesDocument.company_id == company.id
        )
    )
    if exists is None:
        raise InvoiceNotFoundError("finner ikke fakturaen")
    latest = (await latest_transmissions(db, company=company, document_pks=[document_pk])).get(
        document_pk
    )
    if latest is None:
        raise TransmissionNotFoundError("fakturaen er ikke sendt som EHF")
    return latest


async def find_transmission_by_remote_id(
    db: AsyncSession, *, remote_document_id: str
) -> PeppolTransmission | None:
    """Find an outgoing transmission by Ademico's ``transmissionId`` (pushed or swept events)."""
    transmission: PeppolTransmission | None = await db.scalar(
        select(PeppolTransmission).where(
            PeppolTransmission.remote_document_id == remote_document_id
        )
    )
    return transmission


async def pending_transmission_ids(
    db: AsyncSession, *, sent_after: datetime, sent_before: datetime, limit: int
) -> list[str]:
    """Ademico's ids for outgoing sends still pending, oldest first, across all companies.

    For the sweep behind the webhook (myntos.integrations.peppol_sync), which
    asks Ademico about each. Sends younger than ``sent_before`` are left to the
    push that is probably on its way; older than ``sent_after`` are left alone,
    since a send nobody has heard of in a month will not be settled by asking
    again every fifteen minutes.
    """
    rows = await db.scalars(
        select(PeppolTransmission.remote_document_id)
        .where(
            PeppolTransmission.status == TransmissionStatus.PENDING,
            PeppolTransmission.sent_at >= sent_after,
            PeppolTransmission.sent_at < sent_before,
        )
        .order_by(PeppolTransmission.sent_at)
        .limit(limit)
    )
    return list(rows)


async def record_delivery(
    db: AsyncSession,
    transmission: PeppolTransmission,
    *,
    status: TransmissionStatus,
    failure_category: str | None,
    failure_message: str | None,
) -> None:
    """Keep a fresh answer about a pending transmission, and when it came."""
    transmission.status = status
    transmission.failure_category = failure_category
    transmission.failure_message = failure_message
    transmission.status_checked_at = utc_now()
    await db.flush()


# --- Receiving incoming EHF --------------------------------------------------------


async def incoming_exists(db: AsyncSession, *, transmission_id: str) -> bool:
    """Whether any company already holds the received document with this transmissionId.

    Across companies on purpose: a Peppol transmission has one receiver, so a
    document stored once is never stored again, not even for another company
    that claimed the same orgnr after the first gave it up.
    """
    found = await db.scalar(
        select(PeppolIncomingDocument.id)
        .where(PeppolIncomingDocument.transmission_id == transmission_id)
        .limit(1)
    )
    return found is not None


async def store_incoming(
    db: AsyncSession,
    *,
    company: Company,
    transmission_id: str,
    sbdh_transmission_id: str | None,
    raw_xml: bytes,
    received_at: datetime,
    fallback_sender: str = "",
) -> tuple[PeppolIncomingDocument, bool]:
    """Store a received EHF idempotently, returning ``(document, created)``.

    Keyed by ``(company.id, transmission_id)`` so the same document seen again,
    pushed twice, swept after a push, or read off the queue after a crash before
    ``DELETE``, never creates a duplicate invoice.
    """
    existing = await db.scalar(
        select(PeppolIncomingDocument).where(
            PeppolIncomingDocument.company_id == company.id,
            PeppolIncomingDocument.transmission_id == transmission_id,
        )
    )
    if existing is not None:
        return existing, False

    parsed = ubl_reader.parse_ehf(raw_xml, fallback_sender=fallback_sender)
    document = PeppolIncomingDocument(
        company_id=company.id,
        transmission_id=transmission_id,
        sbdh_transmission_id=sbdh_transmission_id,
        document_type=parsed.document_type,
        invoice_number=parsed.invoice_number,
        issue_date=parsed.issue_date,
        due_date=parsed.due_date,
        currency=parsed.currency,
        net_amount=parsed.net_amount,
        vat_amount=parsed.vat_amount,
        gross_amount=parsed.gross_amount,
        seller_name=parsed.seller_name,
        seller_orgnr=parsed.seller_orgnr,
        seller_peppol_id=parsed.seller_peppol_id,
        buyer_reference=parsed.buyer_reference,
        order_reference=parsed.order_reference,
        payment_reference=parsed.payment_reference,
        seller_bank_account=parsed.seller_bank_account,
        note=parsed.note,
        lines_json=parsed.lines_json(),
        tax_subtotals_json=parsed.tax_subtotals_json(),
        ehf_xml=raw_xml.decode("utf-8", errors="replace"),
        ehf_sha256=hashlib.sha256(raw_xml).hexdigest(),
        has_embedded_pdf=parsed.has_embedded_pdf,
        status=IncomingStatus.NY,
        received_at=received_at,
    )
    db.add(document)
    await db.flush()
    return document, True


async def list_incoming(
    db: AsyncSession,
    *,
    company: Company,
    status: IncomingStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[PeppolIncomingDocument]:
    """List received EHF documents for ``company``, newest first."""
    query = select(PeppolIncomingDocument).where(PeppolIncomingDocument.company_id == company.id)
    if status is not None:
        query = query.where(PeppolIncomingDocument.status == status)
    rows = await db.scalars(
        query.order_by(PeppolIncomingDocument.received_at.desc(), PeppolIncomingDocument.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return list(rows)


async def get_incoming(
    db: AsyncSession, *, company: Company, incoming_id: int
) -> PeppolIncomingDocument:
    """One received EHF belonging to ``company``, or :class:`IncomingInvoiceNotFoundError`."""
    document: PeppolIncomingDocument | None = await db.scalar(
        select(PeppolIncomingDocument).where(
            PeppolIncomingDocument.id == incoming_id,
            PeppolIncomingDocument.company_id == company.id,
        )
    )
    if document is None:
        raise IncomingInvoiceNotFoundError("finner ikke den innkommende e-fakturaen")
    return document


async def match_supplier_parties(
    db: AsyncSession, *, company: Company, orgnrs: Sequence[str]
) -> dict[str, int]:
    """Map supplier ``orgnr`` -> ``Party.id`` for ``company``'s existing suppliers."""
    wanted = {orgnr for orgnr in orgnrs if orgnr}
    if not wanted:
        return {}
    rows = await db.scalars(
        select(Party).where(
            Party.company_id == company.id,
            Party.party_type == PartyType.LEVERANDOR,
            Party.orgnr.in_(wanted),
        )
    )
    return {party.orgnr: party.id for party in rows if party.orgnr}


async def dismiss_incoming(
    db: AsyncSession, *, company: Company, incoming_id: int
) -> PeppolIncomingDocument:
    """Dismiss an unbooked incoming EHF (kept in the database, never deleted)."""
    document = await db.scalar(
        select(PeppolIncomingDocument)
        .where(
            PeppolIncomingDocument.id == incoming_id,
            PeppolIncomingDocument.company_id == company.id,
        )
        .with_for_update()
    )
    if document is None:
        raise IncomingInvoiceNotFoundError("finner ikke den innkommende e-fakturaen")
    if document.status is IncomingStatus.BOKFORT:
        raise IncomingInvoiceAlreadyBookedError(
            "e-fakturaen er allerede bokført og kan ikke avvises"
        )
    if document.status is not IncomingStatus.AVVIST:
        document.status = IncomingStatus.AVVIST
        document.dismissed_at = utc_now()
        await db.flush()
    return document


async def reopen_incoming(
    db: AsyncSession, *, company: Company, incoming_id: int
) -> PeppolIncomingDocument:
    """Return a dismissed incoming EHF to ``ny`` so it can be booked."""
    document = await db.scalar(
        select(PeppolIncomingDocument)
        .where(
            PeppolIncomingDocument.id == incoming_id,
            PeppolIncomingDocument.company_id == company.id,
        )
        .with_for_update()
    )
    if document is None:
        raise IncomingInvoiceNotFoundError("finner ikke den innkommende e-fakturaen")
    if document.status is IncomingStatus.BOKFORT:
        raise IncomingInvoiceAlreadyBookedError("e-fakturaen er allerede bokført")
    document.status = IncomingStatus.NY
    document.dismissed_at = None
    await db.flush()
    return document


async def _resolve_or_create_supplier(
    db: AsyncSession, *, company: Company, document: PeppolIncomingDocument
) -> int | None:
    """Find an existing supplier matching ``document.seller_orgnr`` or create one."""
    if document.seller_orgnr:
        existing = await db.scalar(
            select(Party.id).where(
                Party.company_id == company.id,
                Party.party_type == PartyType.LEVERANDOR,
                Party.orgnr == document.seller_orgnr,
            )
        )
        if existing is not None:
            return int(existing)

    # Validate orgnr before passing to create_party; a foreign or test orgnr
    # that fails mod-11 is omitted rather than failing the booking.
    valid_orgnr: str | None = None
    if document.seller_orgnr:
        try:
            valid_orgnr = validate_orgnr(document.seller_orgnr)
        except InvalidOrgnrError:
            valid_orgnr = None

    parsed = ubl_reader.parse_ehf(
        document.ehf_xml.encode("utf-8"), fallback_sender=document.seller_peppol_id
    )
    party = await party_service.create_party(
        db,
        company=company,
        party_type=PartyType.LEVERANDOR,
        name=document.seller_name,
        orgnr=valid_orgnr,
        address_line=parsed.seller_address_line,
        postal_code=parsed.seller_postal_code,
        city=parsed.seller_city,
        country=parsed.seller_country,
    )
    return party.id


async def book_incoming(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    incoming_id: int,
    type_code: str,
    occurred_on: date | None = None,
    gross_amount: Decimal | None = None,
    description: str | None = None,
    tax_code: str | None = None,
    payment_method: PaymentMethod = PaymentMethod.BANK,
    party_id: int | None = None,
    create_supplier: bool = False,
    account_number: str | None = None,
    dimension_value_ids: Sequence[int] = (),
    created_by_user_id: int | None = None,
    created_via_agent_key_id: int | None = None,
) -> tuple[PeppolIncomingDocument, Transaction]:
    """Book a received EHF into the ledger and archive its readable PDF bilag.

    Locks the incoming row ``FOR UPDATE`` so two concurrent presses cannot book
    the same supplier invoice twice. The PDF attached to the voucher is the
    supplier's embedded PDF when the EHF carried one, or Myntos's rendered A4
    summary otherwise; the original UBL XML remains frozen on this row.
    """
    document = await db.scalar(
        select(PeppolIncomingDocument)
        .where(
            PeppolIncomingDocument.id == incoming_id,
            PeppolIncomingDocument.company_id == company.id,
        )
        .with_for_update()
    )
    if document is None:
        raise IncomingInvoiceNotFoundError("finner ikke den innkommende e-fakturaen")
    if document.status is IncomingStatus.BOKFORT:
        raise IncomingInvoiceAlreadyBookedError("e-fakturaen er allerede bokført")
    if document.status is IncomingStatus.AVVIST:
        raise IncomingInvoiceDismissedError(
            "e-fakturaen er avvist. Åpne den på nytt før du bokfører den"
        )

    booking_date = occurred_on or document.issue_date
    if booking_date is None:
        raise IncomingInvoiceInvalidError("oppgi dato for bokføringen")

    if gross_amount is None:
        if document.currency != "NOK":
            raise IncomingInvoiceInvalidError(
                f"fakturaen er i {document.currency}. Oppgi beløpet i norske kroner (NOK) slik det "
                "ble betalt"
            )
        amount = abs(document.gross_amount)
    else:
        amount = gross_amount

    kind = "kreditnota" if document.document_type is IncomingDocumentType.KREDITNOTA else "faktura"
    text = (description or f"{document.seller_name}, {kind} {document.invoice_number}").strip()[
        :255
    ]

    resolved_party_id = party_id
    if resolved_party_id is None and create_supplier:
        resolved_party_id = await _resolve_or_create_supplier(
            db, company=company, document=document
        )

    voucher = await ledger_service.record_cash_flow(
        db,
        company=company,
        type_code=type_code,
        occurred_on=booking_date,
        gross_amount=amount,
        description=text,
        tax_code=tax_code,
        payment_method=payment_method,
        party_pk=resolved_party_id,
        created_by_user_id=created_by_user_id,
        account_number=account_number,
        dimension_value_ids=dimension_value_ids,
    )
    if created_via_agent_key_id is not None:
        voucher.created_via_agent_key_id = created_via_agent_key_id

    pdf_bytes, pdf_name = incoming_pdf.pdf_for(document)
    await bilag_service.attach_document(
        db,
        storage,
        company=company,
        transaction=voucher,
        content=pdf_bytes,
        filename=pdf_name,
        uploaded_by_user_id=created_by_user_id,
    )

    document.status = IncomingStatus.BOKFORT
    document.voucher_id = voucher.id
    document.booked_at = utc_now()
    await db.flush()
    return document, voucher
