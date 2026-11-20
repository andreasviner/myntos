"""E-faktura over Peppol — registering the company and sending an issued invoice.

From 1 January 2027 a sale to another Norwegian business is documented by an
e-invoice (EHF), delivered over Peppol, the European network for business
documents. Myntos writes the EHF when an invoice is issued and keeps it
unchanged; these routes hand it to the access point that carries it to the
customer, and say afterwards whether it arrived.

Two steps, in this order. An admin registers the company once
(``POST …/peppol``), which ends on the access point's own page where somebody
who signs for the company proves who they are. After that anybody who may
write the books sends issued invoices one at a time (``POST …/send``). A key's
request to send always waits for a person's yes: an invoice that has gone
cannot be called back.

Nothing here e-mails anything. A customer who cannot receive over Peppol is
refused with a message saying so, and the seller sends the PDF themselves.

The access point is called from here, not from myntos.domain.peppol, which
makes the checks and keeps the answers: the domain imports no client
(docs/ARCHITECTURE.md, «Backend layout»). What the access point's errors mean
to the person sending is therefore decided here too.
"""

import contextlib
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Query, status
from pydantic import BaseModel

from myntos.api.deps import KEY_READ, CurrentCompany, DbSession, key_send
from myntos.domain.identity.access import Permission, require_permission
from myntos.domain.peppol import service as peppol_service
from myntos.domain.peppol.errors import (
    AccessPointRefusedError,
    AccessPointUnavailableError,
    PeppolNotAvailableError,
    RecipientNotOnPeppolError,
    SendUncertainError,
)
from myntos.domain.peppol.models import (
    PeppolRegistration,
    PeppolTransmission,
    TransmissionStatus,
)
from myntos.integrations import recommand

router = APIRouter(tags=["e-faktura"])

_UNAVAILABLE = "fikk ikke kontakt med tilgangspunktet for e-faktura. Prøv igjen om litt"


class PeppolStandingResponse(BaseModel):
    # The deployment has an access point at all. False means nothing can be
    # sent from this Myntos, whatever the company does.
    available: bool
    # The company has been confirmed through Altinn, which registering needs.
    company_confirmed: bool
    # Registered with the access point.
    registered: bool
    # The access point's identity check is done.
    verified: bool
    # All of the above: invoices can be sent.
    can_send: bool


class PeppolRegistrationResponse(PeppolStandingResponse):
    # The access point's page for the identity check, for the person who signs
    # for the company. Null once the check is done. Not stored by Myntos; ask
    # again for a fresh one.
    verification_url: str | None


class RecipientResponse(BaseModel):
    orgnr: str
    # Registered on Peppol to receive this kind of document (an invoice, or a
    # credit note when asked about one).
    can_receive: bool


class TransmissionResponse(BaseModel):
    status: TransmissionStatus
    # The Peppol address it went to: ``0192:`` and the customer's orgnr.
    recipient: str
    sent_at: datetime
    status_checked_at: datetime | None
    # Set only when ``status`` is ``failed``.
    failure_category: str | None
    failure_message: str | None


def transmission_response(transmission: PeppolTransmission) -> TransmissionResponse:
    return TransmissionResponse(
        status=transmission.status,
        recipient=transmission.recipient,
        sent_at=transmission.sent_at,
        status_checked_at=transmission.status_checked_at,
        failure_category=transmission.failure_category,
        failure_message=transmission.failure_message,
    )


def _require_access_point() -> None:
    if not recommand.is_configured():
        raise PeppolNotAvailableError(
            "e-faktura over Peppol er ikke satt opp i denne installasjonen av Myntos"
        )


def _refused(what: str, error: recommand.RecommandRefusedError) -> AccessPointRefusedError:
    # The findings go into the message itself: they are the only part of the
    # answer worth passing on, and the error body has room for one sentence.
    found = "; ".join(error.problems)
    detail = f"tilgangspunktet avviste {what}" + (f": {found}" if found else "")
    return AccessPointRefusedError(detail, error.problems)


async def _ask_verified(registration: PeppolRegistration) -> recommand.RemoteCompany | None:
    """Ask the access point whether the identity check is done, and stamp it if so.

    ``None`` when the company is not there any more, which only happens if
    somebody deleted it in Recommand's own dashboard.
    """
    try:
        remote = await recommand.get_company(registration.remote_company_id)
    except recommand.RecommandNotFoundError:
        return None
    if remote.is_verified:
        peppol_service.mark_verified(registration)
    return remote


def _standing_fields(standing: peppol_service.Standing) -> dict[str, bool]:
    return {
        "available": standing.available,
        "company_confirmed": standing.company_confirmed,
        "registered": standing.registered,
        "verified": standing.verified,
        "can_send": standing.can_send,
    }


@router.get("/companies/{company_id}/peppol", openapi_extra=KEY_READ)
async def peppol_standing(context: CurrentCompany, db: DbSession) -> PeppolStandingResponse:
    """Whether this company can send invoices as EHF over Peppol, and if not, why.

    The steps, each a boolean, in the order they have to happen:
    ``available`` (this Myntos has an access point, the service that carries
    documents onto the Peppol network), ``company_confirmed`` (someone proved
    through Altinn, the Norwegian government's authorisation register, that
    they may act for the company), ``registered`` (an admin registered the
    company with the access point) and ``verified`` (someone who signs for the
    company finished the access point's identity check). ``can_send`` is all of
    them. The first false one is the step to tell the person about; none of
    them is something a key can do.

    While ``registered`` is true and ``verified`` false, each call asks the
    access point whether the check has been finished since.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    registration = await peppol_service.get_registration(db, company=context.company)
    available = recommand.is_configured()
    # Asked on every read until the check is done, because the person finishes
    # it on another site and comes back expecting the page to know. An access
    # point that cannot be reached leaves the stored answer.
    if available and registration is not None and not registration.is_verified:
        with contextlib.suppress(recommand.RecommandError):
            await _ask_verified(registration)
    standing = peppol_service.Standing(
        available=available,
        company_confirmed=context.company.is_verified,
        registration=registration,
    )
    return PeppolStandingResponse(**_standing_fields(standing))


@router.post("/companies/{company_id}/peppol")
async def register_peppol(context: CurrentCompany, db: DbSession) -> PeppolRegistrationResponse:
    """Register the company for sending EHF, and get the link to the identity check.

    Admins only, and only for a company confirmed through Altinn (409
    ``company_not_confirmed`` otherwise): the access point sends as whichever
    company Myntos names, so Myntos makes sure the account asking is one that
    may act for the business. The company is registered to send only. It is not
    registered to receive, because registering a Norwegian orgnr to receive
    takes over whatever inbox the company gets its supplier invoices in today.

    ``verification_url`` is the access point's page where someone who signs
    for the company (daglig leder, a board member with signing rights, or
    prokura) proves who they are. Calling this again before they have finished
    gives a fresh link; once ``verified`` is true it is null. 409
    ``company_address_missing`` when the company has no business address to
    register; 502 when the access point refused or could not be reached; 503
    ``peppol_not_available`` when this Myntos has no access point.
    """
    require_permission(context.membership.role, Permission.ORGANIZATION_MANAGE)
    _require_access_point()
    company = context.company
    peppol_service.require_registrable(company)
    registration = await peppol_service.get_registration(db, company=company)
    link: str | None = None
    try:
        remote = await _ask_verified(registration) if registration is not None else None
        # An existing record at the access point for the same orgnr is adopted
        # rather than duplicated: Peppol names the sender by orgnr, so two would
        # be two senders claiming to be one business (docs/integrations/peppol.md).
        if remote is None:
            remote = await recommand.find_company(company.orgnr)
        if remote is None:
            address, postal_code, city = peppol_service.registered_address(company)
            remote, link = await recommand.create_company(
                orgnr=company.orgnr,
                name=company.name,
                address=address,
                postal_code=postal_code,
                city=city,
                vat_registered=company.vat_registered,
            )
        registration = await peppol_service.record_registration(
            db,
            company=company,
            registration=registration,
            remote_company_id=remote.id,
            verified=remote.is_verified,
        )
        # Asking again before the check is done hands out a fresh link, which
        # is the answer to one that expired; after it there is no link.
        if remote.is_verified:
            link = None
        elif link is None:
            link = await recommand.verification_url(remote.id)
    except recommand.RecommandRefusedError as error:
        raise _refused("registreringen", error) from error
    except recommand.RecommandError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error
    standing = peppol_service.Standing(
        available=True, company_confirmed=True, registration=registration
    )
    return PeppolRegistrationResponse(**_standing_fields(standing), verification_url=link)


@router.get("/companies/{company_id}/peppol/mottaker", openapi_extra=KEY_READ)
async def peppol_recipient(
    context: CurrentCompany,
    orgnr: Annotated[str, Query(pattern=r"^\d{9}$")],
    kreditnota: bool = False,
) -> RecipientResponse:
    """Whether a customer can receive an invoice (or credit note) as EHF over Peppol.

    ``orgnr`` is the customer's organisasjonsnummer, nine digits, which is
    their address on Peppol. Ask before issuing an invoice to a business: when
    ``can_receive`` is true, sending it as EHF right after issuing is the
    normal way it goes, and from 1 January 2027 the required one for a sale to
    another business that keeps accounts. When false, the customer is not
    registered to receive this kind of document and gets the PDF. A customer
    that registers later answers true from then on, so the answer is worth
    asking again rather than remembering.

    Asking sends nothing and stores nothing; the orgnr goes to the access
    point, which looks it up in the Peppol network's registry. 503
    ``peppol_not_available`` when this Myntos has no access point, 502
    ``access_point_unavailable`` when it could not be asked.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    _require_access_point()
    try:
        receives = await recommand.can_receive(orgnr, credit_note=kreditnota)
    except recommand.RecommandError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error
    return RecipientResponse(orgnr=orgnr, can_receive=receives)


@router.post(
    "/companies/{company_id}/faktura/{document_id}/send",
    status_code=status.HTTP_201_CREATED,
    openapi_extra=key_send("sende fakturaen som EHF til kunden"),
)
async def send_faktura(
    document_id: int, context: CurrentCompany, db: DbSession
) -> TransmissionResponse:
    """Send an issued invoice or credit note to the customer as EHF over Peppol.

    What is sent is the EHF (the Norwegian e-invoice, an XML file) that was
    made when the document was issued, unchanged. It goes to the customer's
    organisasjonsnummer (orgnr, the Norwegian business register number), which
    is their address on Peppol. Nothing is e-mailed.

    A key's request always waits for its person's approval, whatever the key
    may do on its own, because an invoice that has gone cannot be called back.

    ``status`` in the answer is ``pending`` (handed to the network, no receipt
    from the customer's side yet), ``delivered`` or ``failed``. Follow it with
    ``GET …/forsendelse``. Refusals, each with a message to pass on:
    409 ``not_registered`` (the company is not registered or the identity check
    is unfinished, see ``GET …/peppol``), 409 ``not_sendable`` (a draft, or a
    customer without an orgnr), 409 ``already_sent`` (it went once and did not
    fail; sending twice can get it paid twice), 409 ``recipient_not_on_peppol``
    (the customer cannot receive EHF; nothing was sent, send the PDF instead),
    502 ``send_uncertain`` (no answer in time; ask the customer before trying
    again), 502 ``access_point_refused`` or ``access_point_unavailable``.
    """
    require_permission(context.membership.role, Permission.LEDGER_WRITE)
    _require_access_point()
    sendable = await peppol_service.prepare_send(
        db, company=context.company, document_pk=document_id
    )
    if not sendable.registration.is_verified:
        try:
            await _ask_verified(sendable.registration)
        except recommand.RecommandError as error:
            raise AccessPointUnavailableError(_UNAVAILABLE) from error
        peppol_service.require_verified(sendable.registration)

    try:
        sent = await recommand.send(
            sendable.registration.remote_company_id,
            recipient_orgnr=sendable.buyer_orgnr,
            xml=sendable.xml,
            credit_note=sendable.document.is_credit_note,
        )
    except recommand.RecipientUnreachableError as error:
        raise RecipientNotOnPeppolError(
            "kunden kan ikke motta EHF over Peppol, så ingenting ble sendt. "
            "Send PDF-en til kunden selv"
        ) from error
    except recommand.RecommandRefusedError as error:
        raise _refused("fakturaen", error) from error
    except recommand.SendTimedOutError as error:
        # Nothing is stored: storing it as sent would block a resend that may
        # be needed, so the person is told to ask the customer first
        # (docs/integrations/peppol.md, «Sending»).
        raise SendUncertainError(
            "tilgangspunktet svarte ikke i tide, så vi vet ikke om fakturaen gikk. "
            "Spør kunden om de har fått den før du sender den igjen"
        ) from error
    except recommand.RecommandError as error:
        raise AccessPointUnavailableError(_UNAVAILABLE) from error

    transmission = await peppol_service.record_transmission(
        db,
        company=context.company,
        document=sendable.document,
        remote_document_id=sent.id,
        recipient=recommand.peppol_address(sendable.buyer_orgnr),
        status=TransmissionStatus(sent.delivery.status),
        failure_category=sent.delivery.failure_category,
        failure_message=sent.delivery.failure_message,
    )
    return transmission_response(transmission)


@router.get("/companies/{company_id}/faktura/{document_id}/forsendelse", openapi_extra=KEY_READ)
async def faktura_transmission(
    document_id: int, context: CurrentCompany, db: DbSession
) -> TransmissionResponse:
    """Whether the invoice sent as EHF has reached the customer.

    The latest send of the document. While it is ``pending`` each call asks
    the access point again and stores the answer; ``status_checked_at`` says
    when that last succeeded, and an access point that cannot be reached leaves
    the stored answer. ``delivered`` means the customer's access point
    acknowledged it. ``failed`` comes with ``failure_category``
    (``recipient_not_found``, ``document_not_supported``, ``validation``,
    ``transport``, ``recipient_rejected``, ``duplicate`` or ``other``) and the
    access point's own ``failure_message``, and a failed send may be tried
    again with ``POST …/send``.

    404 ``transmission_not_found`` when the document has never been sent;
    404 ``invoice_not_found`` when the id is not one of this company's
    documents.
    """
    require_permission(context.membership.role, Permission.LEDGER_READ)
    latest = await peppol_service.latest_transmission(
        db, company=context.company, document_pk=document_id
    )
    if latest.status == TransmissionStatus.PENDING and recommand.is_configured():
        try:
            delivery = await recommand.delivery(latest.remote_document_id)
        except recommand.RecommandError:
            # A status that cannot be refreshed is still the last true thing
            # known, and status_checked_at says how old it is.
            return transmission_response(latest)
        await peppol_service.record_delivery(
            db,
            latest,
            status=TransmissionStatus(delivery.status),
            failure_category=delivery.failure_category,
            failure_message=delivery.failure_message,
        )
    return transmission_response(latest)
