"""Ademico — the Peppol access point that sends and receives EHF documents.

From 1 January 2027 a sale to another bokføringspliktig is documented by an
e-invoice, and an e-invoice is only delivered when it travels over Peppol.
Peppol is not something a program joins on its own: every sender and receiver
goes through an access point certified by OpenPeppol, and Ademico Software
(Belgium, EEA) is the one this deployment uses.

Myntos writes the outgoing EHF and freezes it at issue
(:mod:`myntos.domain.faktura.ehf`); this module hands that frozen XML to
Ademico unchanged as a multipart UBL submission, follows its delivery status,
manages each company's legal entity and SMP receiving flag, reads the events
Ademico reports for every company in Myntos at once, and tells Ademico where to
push them.

Four choices shape it (docs/integrations/peppol.md):

**The frozen XML, byte for byte.** ``POST /api/peppol/v1/invoices/ubl-submissions``
takes the stored ``ehf_xml`` verbatim. Nothing is rebuilt or e-mailed.

**Send-only by default; receiving is explicit opt-in.** A new company is
created with ``peppolRegistration: false`` and ``publishInPeppolDirectory: false``,
which registers the sender locally at Ademico without touching ELMA or any
existing supplier-invoice inbox. Enabling receiving sets ``peppolRegistration:
true`` on that registration, publishing the company in SML/SMP so supplier
invoices arrive in Myntos.

**Events for the whole workspace.** A delivery or a received document for
*every* legal entity under the deployment's credentials comes through one
channel: pushed to Myntos's webhook, or read from ``GET …/notifications``. Each
notification is consumed (``DELETE``) only after Myntos has committed the
result locally (myntos.integrations.peppol_sync).

**A push is checked against Ademico's own record.** Ademico cannot sign a
webhook body, so :func:`find_document` reads each pushed document back with the
deployment's credentials before anything is believed.

One set of Basic Auth credentials plus the environment ``accessToken`` for the
whole deployment (``ADEMICO_USER``, ``ADEMICO_PASSWORD``, ``ADEMICO_TOKEN``).
Logging never carries a document body: every request is an invoice and an error
body may quote it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
from pydantic import SecretStr

from myntos.core.config import get_settings

logger = logging.getLogger(__name__)

# Norway's Peppol participant scheme: the organisasjonsnummer. ELMA and
# OpenPeppol register Norwegian businesses under this scheme.
NORWEGIAN_SCHEME = "0192"

# Peppol BIS Billing 3.0 (EHF 3.0) invoice and credit note capabilities
# registered on Norwegian participants at Ademico.
SUPPORTED_DOCUMENTS: tuple[str, ...] = (
    "PEPPOL_BIS_BILLING_UBL_INVOICE_V3",
    "PEPPOL_BIS_BILLING_UBL_CREDIT_NOTE_V3",
)

# Ordinary calls are short: a lookup or status check that hangs costs the user
# a page that will not load. Submitting an invoice validates the UBL/Schematron
# synchronously before queueing it, and a timeout there leaves nobody knowing
# whether Ademico accepted it.
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_SEND_TIMEOUT = httpx.Timeout(90.0, connect=5.0)

# Ademico DocumentStatusEnumRO values that mean the document left Ademico over
# AS4 and reached the receiving access point (or further along the buyer flow).
_DELIVERED_STATUSES = frozenset(
    {
        "SENT",
        "TECHNICAL_ACCEPTANCE",
        "BUYER_ACKNOWLEDGE",
        "IN_PROCESS",
        "UNDER_QUERY",
        "CONDITIONALLY_ACCEPTED",
        "ACCEPTED",
        "PARTIALLY_PAID",
        "FULLY_PAID",
    }
)
_FAILED_STATUSES = frozenset({"SEND_FAILED", "REJECTED"})

# The user name Myntos gives Ademico for calling the webhook. Fixed, because the
# secret is the password: a second setting would only be a second thing to get
# out of step between .env and Ademico.
WEBHOOK_USER = "ademico"

# The most notifications one pushed body may carry before the rest are left to
# the sweep. Ademico is told to push one at a time (``bulk: false``); this only
# bounds what a body that says otherwise can make Myntos do in one request.
_MAX_PUSHED_NOTIFICATIONS = 100


class AdemicoError(Exception):
    """Base class for every reason the access point did not do what was asked."""


class AdemicoNotConfiguredError(AdemicoError):
    """Missing credentials. The deployment neither sends nor receives EHF."""


class AdemicoUnavailableError(AdemicoError):
    """Ademico could not be reached, refused our credentials, or answered nonsense."""


class AdemicoNotFoundError(AdemicoError):
    """Ademico holds no entity or document with that id."""


class AdemicoRefusedError(AdemicoError):
    """Ademico refused the request itself, and said why.

    ``problems`` carries Ademico's own error message or validation findings so
    the caller can report what failed on a frozen EHF or registration.
    """

    def __init__(self, message: str, problems: list[str], *, error_code: str | None = None) -> None:
        super().__init__(message)
        self.problems = problems
        self.error_code = error_code


class RecipientUnreachableError(AdemicoError):
    """The buyer is not reachable over Peppol. Nothing was sent or stored."""

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class ParticipantConflictError(AdemicoError):
    """The orgnr cannot be published to SMP/ELMA (typically registered elsewhere)."""

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class SendTimedOutError(AdemicoError):
    """The send was made and no answer came, so whether it went is unknown."""


@dataclass(frozen=True)
class RemoteEntity:
    """What Myntos needs from Ademico's record of one legal entity."""

    legal_entity_id: str
    peppol_registration_id: str | None
    orgnr: str
    receiving_enabled: bool
    publish_in_directory: bool = False
    registration_date: str | None = None


@dataclass(frozen=True)
class Delivery:
    """Where a sent document stands, mapped to Myntos's three states."""

    status: str
    failure_category: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class Sent:
    """The answer to a UBL submission: Ademico's transmissionId and initial status."""

    id: str
    delivery: Delivery


@dataclass(frozen=True)
class Notification:
    """One event across the workspace, from the pull queue or a webhook body.

    ``notification_id`` is always there on the queue. A pushed body is not
    documented, so there it may be missing, and then nothing can be consumed.
    """

    notification_id: int | None
    event_type: str
    transmission_id: str | None
    sbdh_transmission_id: str | None
    document_id: str | None
    document_type: str | None
    document_status: str | None
    sender: str | None
    receiver: str | None
    received_at: datetime | None
    notification_at: datetime | None
    details: tuple[str, ...] = ()


@dataclass(frozen=True)
class DocumentRecord:
    """Ademico's own record of one exchanged document, from ``GET …/documents``.

    What a webhook is checked against: receiver, sender, type and status here
    come from Ademico over the deployment's credentials, never from a push.
    """

    transmission_id: str
    direction: str
    document_type: str | None
    document_status: str | None
    sender: str | None
    receiver: str | None
    sbdh_transmission_id: str | None
    created_at: datetime | None


@dataclass(frozen=True)
class WebhookSettings:
    """Where Ademico pushes this deployment's events. The password never comes back."""

    active: bool
    bulk: bool
    url: str | None
    authentication: str
    user: str | None


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value().strip()
    return text or None


def _credentials() -> tuple[str, str, str] | None:
    """``(user, password, token)``, treating any blank part as unconfigured.

    A blank variable in ``.env`` is how a deployment or test suite turns
    Peppol off, so half-filled credentials must never reach the live gateway.
    """
    settings = get_settings()
    user = (settings.ademico_user or "").strip()
    password = _secret(settings.ademico_password)
    token = _secret(settings.ademico_token)
    if not user or password is None or token is None:
        return None
    return user, password, token


def is_configured() -> bool:
    """Whether this deployment has Ademico credentials configured."""
    return _credentials() is not None


def peppol_address(orgnr: str) -> str:
    """``0192:<orgnr>``, how Peppol names a Norwegian business."""
    return f"{NORWEGIAN_SCHEME}:{orgnr}"


def parse_norwegian_orgnr(peppol_id: str | None) -> str | None:
    """Extract a 9-digit Norwegian orgnr from ``0192:<orgnr>``, or ``None``."""
    if not peppol_id:
        return None
    scheme, sep, ident = peppol_id.strip().partition(":")
    if sep and scheme == NORWEGIAN_SCHEME and len(ident) == 9 and ident.isdigit():
        return ident
    return None


def _client(timeout: httpx.Timeout = _HTTP_TIMEOUT) -> httpx.AsyncClient:
    """The HTTP client. A function so tests can hand in a mock transport."""
    return httpx.AsyncClient(timeout=timeout)


def _parse_utc(raw: object) -> datetime | None:
    """Ademico timestamps are UTC without timezone suffix (``yyyy-MM-dd'T'HH:mm:ss.SSS``)."""
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _error_details(body: object) -> tuple[str | None, list[str]]:
    """Extract Ademico's error code and human-readable problem list from a response body."""
    if not isinstance(body, dict):
        return None, []
    code = body.get("code")
    code_str = str(code) if isinstance(code, str) and code else None
    problems: list[str] = []
    message = body.get("message")
    if isinstance(message, str) and message.strip():
        problems.append(message.strip())
    # Validation reports or details arrays may list individual Schematron/XSD findings.
    for key in ("details", "errors"):
        items = body.get(key)
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    msg = item.get("message") or item.get("description")
                    item_code = item.get("code")
                    if isinstance(msg, str) and msg.strip():
                        text = f"{item_code}: {msg.strip()}" if item_code else msg.strip()
                        if text not in problems:
                            problems.append(text)
                elif isinstance(item, str) and item.strip() and item.strip() not in problems:
                    problems.append(item.strip())
    report = body.get("validationReport")
    if isinstance(report, dict):
        for item in report.get("validationErrors") or []:
            if isinstance(item, dict):
                msg = item.get("message")
                rule = item.get("ruleId") or item.get("code")
                if isinstance(msg, str) and msg.strip():
                    text = f"[{rule}] {msg.strip()}" if rule else msg.strip()
                    if text not in problems:
                        problems.append(text)
    return code_str, problems[:20]


async def _raw_request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str | int] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    accept: str = "application/json",
    timeout: httpx.Timeout = _HTTP_TIMEOUT,
) -> httpx.Response:
    credentials = _credentials()
    if credentials is None:
        raise AdemicoNotConfiguredError("e-faktura er ikke satt opp i denne installasjonen")
    user, password, token = credentials
    url = f"{get_settings().ademico_api_base_url.rstrip('/')}{path}"
    query: dict[str, str | int] = dict(params or {})
    query["accessToken"] = token
    headers = {"Accept": accept}
    try:
        async with _client(timeout) as client:
            response = await client.request(
                method,
                url,
                json=json,
                params=query,
                files=files,
                headers=headers,
                auth=httpx.BasicAuth(user, password),
            )
    except httpx.TimeoutException as error:
        if (
            method == "POST"
            and path.endswith("/ubl-submissions")
            and not isinstance(error, httpx.ConnectTimeout)
        ):
            # The XML was on its way and no answer came: it may well have gone.
            # At ERROR so the operator's page and mail catch it (docs/operator.md).
            logger.error("Ademico did not answer a send in time")
            raise SendTimedOutError("tilgangspunktet svarte ikke i tide") from error
        logger.error("Ademico could not be reached: %s", type(error).__name__, exc_info=error)
        raise AdemicoUnavailableError("fikk ikke kontakt med tilgangspunktet") from error
    except httpx.HTTPError as error:
        logger.error("Ademico could not be reached: %s", type(error).__name__, exc_info=error)
        raise AdemicoUnavailableError("fikk ikke kontakt med tilgangspunktet") from error

    if response.status_code == httpx.codes.UNAUTHORIZED:
        # Ours to fix, not the user's: wrong or revoked deployment credentials.
        logger.error("Ademico refused the deployment's credentials on %s %s", method, path)
        raise AdemicoUnavailableError("tilgangspunktet godtok ikke nøkkelen til Myntos")
    if response.status_code == httpx.codes.NOT_FOUND:
        raise AdemicoNotFoundError("tilgangspunktet kjenner ikke denne")

    if response.is_error:
        try:
            body: object = response.json()
        except ValueError:
            body = None
        code, problems = _error_details(body)
        if code == "PARTICIPANT_LOOKUP_ERROR":
            raise RecipientUnreachableError("kunden kan ikke nås over Peppol", problems)
        if code == "PARTICIPANT_ERROR":
            raise ParticipantConflictError("tilgangspunktet avviste registreringen", problems)
        if response.status_code == httpx.codes.BAD_REQUEST:
            logger.error(
                "Ademico refused %s %s with code=%s (%d problems)",
                method,
                path,
                code or "unknown",
                len(problems),
            )
            raise AdemicoRefusedError(
                "tilgangspunktet avviste forespørselen", problems, error_code=code
            )
        logger.error("Ademico answered %s %s with %d", method, path, response.status_code)
        raise AdemicoUnavailableError("uventet svar fra tilgangspunktet")
    return response


async def _json_request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str | int] | None = None,
    files: dict[str, tuple[str, bytes, str]] | None = None,
    timeout: httpx.Timeout = _HTTP_TIMEOUT,
) -> dict[str, Any]:
    response = await _raw_request(
        method, path, json=json, params=params, files=files, timeout=timeout
    )
    if not response.content:
        return {}
    try:
        body: object = response.json()
    except ValueError as error:
        logger.error("Ademico returned unreadable JSON for %s %s", method, path, exc_info=error)
        raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet") from error
    if not isinstance(body, dict):
        logger.error("Ademico returned unexpected JSON shape for %s %s", method, path)
        raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet")
    return body


def _ademico_timestamp(when: datetime | None = None) -> str:
    """Format a UTC timestamp as Ademico's ``yyyy-MM-dd'T'HH:mm:ss.SSS`` (no ``Z``)."""
    dt = (when or datetime.now(UTC)).astimezone(UTC)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000")


def _entity_from_registration(
    legal_entity_id: str,
    raw_regs: object,
    orgnr: str,
    *,
    legal_entity_details: object = None,
) -> RemoteEntity | None:
    if not isinstance(raw_regs, list):
        return None
    publish_in_directory = False
    registration_date: str | None = None
    if isinstance(legal_entity_details, dict):
        publish_in_directory = bool(legal_entity_details.get("publishInPeppolDirectory"))
        raw_reg_date = legal_entity_details.get("registrationDate")
        if isinstance(raw_reg_date, str) and raw_reg_date.strip():
            registration_date = raw_reg_date.strip()
    for reg in raw_regs:
        if not isinstance(reg, dict):
            continue
        details = reg.get("peppolRegistrationDetails")
        if not isinstance(details, dict):
            continue
        ident = details.get("peppolIdentifier")
        if not isinstance(ident, dict):
            continue
        if ident.get("scheme") == NORWEGIAN_SCHEME and str(ident.get("identifier") or "") == orgnr:
            reg_id = reg.get("peppolRegistrationId")
            return RemoteEntity(
                legal_entity_id=legal_entity_id,
                peppol_registration_id=str(reg_id) if reg_id is not None else None,
                orgnr=orgnr,
                receiving_enabled=bool(details.get("peppolRegistration")),
                publish_in_directory=publish_in_directory,
                registration_date=registration_date,
            )
    return None


async def find_legal_entity(orgnr: str) -> RemoteEntity | None:
    """Find the existing Ademico legal entity for ``0192:<orgnr>``, if any."""
    page = 0
    page_size = 100
    while True:
        body = await _json_request(
            "GET", "/api/peppol/v1/legal-entities", params={"page": page, "pageSize": page_size}
        )
        entities = body.get("legalEntities")
        if not isinstance(entities, list):
            raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet")
        for raw in entities:
            if not isinstance(raw, dict) or raw.get("legalEntityId") is None:
                continue
            found = _entity_from_registration(
                str(raw["legalEntityId"]),
                raw.get("peppolRegistrations"),
                orgnr,
                legal_entity_details=raw.get("legalEntityDetails"),
            )
            if found is not None:
                return found
        pagination = body.get("pagination")
        count = pagination.get("count", 0) if isinstance(pagination, dict) else 0
        if not entities or (page + 1) * page_size >= count:
            return None
        page += 1


def _legal_entity_details(
    *,
    name: str,
    address: str,
    postal_code: str,
    city: str,
    publish: bool,
    registration_date: str | None = None,
) -> dict[str, Any]:
    """Legal entity metadata for Ademico. No email or phone is ever sent.

    Ademico requires ``registrationDate`` on legal-entity updates (especially
    when ``publishInPeppolDirectory`` is enabled). Preserve the existing value
    from Ademico whenever present, falling back to the current UTC timestamp.
    """
    return {
        "publishInPeppolDirectory": publish,
        "name": name,
        "countryCode": "NO",
        "geographicalInformation": f"{address}, {postal_code} {city}",
        "registrationDate": registration_date or _ademico_timestamp(),
    }


def _registration_details(*, orgnr: str, receiving: bool) -> dict[str, Any]:
    return {
        "peppolIdentifier": {"scheme": NORWEGIAN_SCHEME, "identifier": orgnr},
        "supportedDocuments": list(SUPPORTED_DOCUMENTS),
        "peppolRegistration": receiving,
    }


async def _find_registration_id(legal_entity_id: str, orgnr: str) -> str | None:
    body = await _json_request(
        "GET", f"/api/peppol/v1/legal-entities/{legal_entity_id}/peppol-registrations"
    )
    found = _entity_from_registration(legal_entity_id, body.get("peppolRegistrations"), orgnr)
    return found.peppol_registration_id if found is not None else None


async def create_legal_entity(
    *,
    orgnr: str,
    name: str,
    address: str,
    postal_code: str,
    city: str,
    receiving: bool = False,
) -> RemoteEntity:
    """Create a legal entity in Ademico (send-only by default; receiving on opt-in)."""
    reg_date = _ademico_timestamp()
    body = await _json_request(
        "POST",
        "/api/peppol/v1/legal-entities",
        json={
            "legalEntityDetails": _legal_entity_details(
                name=name,
                address=address,
                postal_code=postal_code,
                city=city,
                publish=receiving,
                registration_date=reg_date,
            ),
            "peppolRegistrations": [_registration_details(orgnr=orgnr, receiving=receiving)],
        },
    )
    raw_id = body.get("legalEntityId")
    if raw_id is None:
        raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet")
    legal_entity_id = str(raw_id)
    registration_id = await _find_registration_id(legal_entity_id, orgnr)
    return RemoteEntity(
        legal_entity_id=legal_entity_id,
        peppol_registration_id=registration_id,
        orgnr=orgnr,
        receiving_enabled=receiving,
        publish_in_directory=receiving,
        registration_date=reg_date,
    )


async def set_receiving(
    legal_entity_id: str,
    registration_id: str,
    *,
    orgnr: str,
    name: str,
    address: str,
    postal_code: str,
    city: str,
    enabled: bool,
    registration_date: str | None = None,
    update_registration: bool = True,
) -> None:
    """Publish (``enabled=True``) or unpublish (``enabled=False``) receiving in SML/SMP.

    Updates the Peppol registration first so an ELMA conflict fails before
    touching the directory flag, then keeps ``publishInPeppolDirectory`` in
    step with ``enabled``.
    """
    if update_registration:
        await _json_request(
            "PUT",
            f"/api/peppol/v1/legal-entities/{legal_entity_id}/peppol-registrations/{registration_id}",
            json={
                "peppolRegistrationDetails": _registration_details(orgnr=orgnr, receiving=enabled)
            },
        )
    await _json_request(
        "PUT",
        f"/api/peppol/v1/legal-entities/{legal_entity_id}",
        json={
            "legalEntityDetails": _legal_entity_details(
                name=name,
                address=address,
                postal_code=postal_code,
                city=city,
                publish=enabled,
                registration_date=registration_date,
            )
        },
    )


async def send(*, xml: str, filename: str) -> Sent:
    """Submit the frozen EHF XML verbatim to Ademico's UBL submission endpoint."""
    body = await _json_request(
        "POST",
        "/api/peppol/v1/invoices/ubl-submissions",
        files={"file": (filename, xml.encode("utf-8"), "application/xml")},
        timeout=_SEND_TIMEOUT,
    )
    transmission_id = body.get("transmissionId")
    if not isinstance(transmission_id, str) or not transmission_id:
        raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet")
    # Submissions are queued asynchronously; delivery arrives as a pushed or
    # queued notification, or by asking GET /api/peppol/v1/documents.
    return Sent(id=transmission_id, delivery=Delivery(status="pending"))


def delivery_from_status(document_status: str | None, *, details: tuple[str, ...] = ()) -> Delivery:
    """Map Ademico's ``DocumentStatusEnumRO`` to Myntos's ``pending``/``delivered``/``failed``."""
    if document_status in _DELIVERED_STATUSES:
        return Delivery(status="delivered")
    if document_status in _FAILED_STATUSES:
        category = "recipient_rejected" if document_status == "REJECTED" else "transport"
        message = "; ".join(details)[:1000] if details else None
        return Delivery(status="failed", failure_category=category, failure_message=message)
    return Delivery(status="pending")


def _text(raw: dict[str, Any], key: str) -> str | None:
    value = raw.get(key)
    return value if isinstance(value, str) and value else None


def _document_record(raw: object) -> DocumentRecord | None:
    if not isinstance(raw, dict):
        return None
    transmission_id = _text(raw, "transmissionId")
    direction = _text(raw, "direction")
    if transmission_id is None or direction is None:
        return None
    return DocumentRecord(
        transmission_id=transmission_id,
        direction=direction,
        document_type=_text(raw, "peppolDocumentType"),
        document_status=_text(raw, "documentStatus"),
        sender=_text(raw, "sender"),
        receiver=_text(raw, "receiver"),
        sbdh_transmission_id=_text(raw, "sbdhTransmissionId"),
        created_at=_parse_utc(raw.get("creationTime")),
    )


def _documents(body: dict[str, Any]) -> list[DocumentRecord]:
    raw_items = body.get("documents")
    if not isinstance(raw_items, list):
        raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet")
    return [record for raw in raw_items if (record := _document_record(raw)) is not None]


async def find_document(transmission_id: str, *, direction: str) -> DocumentRecord | None:
    """Ademico's record of one transmission in one direction, or ``None``.

    The direction is checked on the answer as well as asked for: a document
    sent between two companies under this deployment is two records, and the
    receiving one must never be taken for the sending one.
    """
    body = await _json_request(
        "GET",
        "/api/peppol/v1/documents",
        params={"transmissionId": transmission_id, "direction": direction, "pageSize": 5},
    )
    for record in _documents(body):
        if record.transmission_id == transmission_id and record.direction == direction:
            return record
    return None


async def list_documents(
    *, direction: str, since: datetime, page: int = 0, page_size: int = 100
) -> tuple[list[DocumentRecord], int]:
    """One page of the documents exchanged in ``direction`` since ``since``, all companies."""
    body = await _json_request(
        "GET",
        "/api/peppol/v1/documents",
        params={
            "direction": direction,
            # The filter takes ISO 8601 with a zone, unlike the timestamps
            # Ademico writes, which have none.
            "startDateTime": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.000Z"),
            "page": page,
            "pageSize": page_size,
        },
    )
    records = _documents(body)
    pagination = body.get("pagination")
    count = pagination.get("count", len(records)) if isinstance(pagination, dict) else len(records)
    return records, int(count)


async def delivery(transmission_id: str) -> Delivery:
    """Ask Ademico where an outgoing transmission stands now."""
    record = await find_document(transmission_id, direction="SENDING")
    if record is None:
        return Delivery(status="pending")
    return delivery_from_status(record.document_status)


def _notification(raw: object, *, require_id: bool = True) -> Notification | None:
    if not isinstance(raw, dict):
        return None
    nid = raw.get("notificationId")
    event_type = raw.get("eventType")
    if not isinstance(event_type, str):
        return None
    if not isinstance(nid, int):
        if require_id:
            return None
        nid = None
    details: list[str] = []
    for item in raw.get("details") or []:
        if isinstance(item, dict):
            msg = item.get("message")
            code = item.get("code")
            if isinstance(msg, str) and msg.strip():
                details.append(f"{code}: {msg.strip()}" if code else msg.strip())
    invoice_response = raw.get("invoiceResponse")
    if isinstance(invoice_response, dict):
        note = invoice_response.get("note")
        if isinstance(note, str) and note.strip():
            details.append(note.strip())
    transmission_id = raw.get("transmissionId")
    if not transmission_id and isinstance(invoice_response, dict):
        transmission_id = invoice_response.get("invoiceTransmissionId")
    return Notification(
        notification_id=nid,
        event_type=event_type,
        transmission_id=str(transmission_id) if isinstance(transmission_id, str) else None,
        sbdh_transmission_id=(
            str(raw["sbdhTransmissionId"])
            if isinstance(raw.get("sbdhTransmissionId"), str)
            else None
        ),
        document_id=str(raw["documentId"]) if isinstance(raw.get("documentId"), str) else None,
        document_type=(
            str(raw["peppolDocumentType"])
            if isinstance(raw.get("peppolDocumentType"), str)
            else None
        ),
        document_status=(
            str(raw["documentStatus"]) if isinstance(raw.get("documentStatus"), str) else None
        ),
        sender=str(raw["sender"]) if isinstance(raw.get("sender"), str) else None,
        receiver=str(raw["receiver"]) if isinstance(raw.get("receiver"), str) else None,
        received_at=_parse_utc(raw.get("receivedDate")),
        notification_at=_parse_utc(raw.get("notificationDate")),
        details=tuple(details),
    )


def delivery_from_notification(notification: Notification) -> Delivery | None:
    """Turn a sending-side notification into a :class:`Delivery`, or ``None``."""
    event = notification.event_type
    if event == "DOCUMENT_SENT":
        return Delivery(status="delivered")
    if event == "DOCUMENT_SEND_FAILED":
        message = "; ".join(notification.details)[:1000] if notification.details else None
        return Delivery(status="failed", failure_category="transport", failure_message=message)
    if event in ("INVOICE_RESPONSE_RECEIVED", "MLR_RECEIVED"):
        return delivery_from_status(notification.document_status, details=notification.details)
    return None


async def list_notifications(
    *, page: int = 0, page_size: int = 100
) -> tuple[list[Notification], int]:
    """Fetch one page of unconsumed notifications for all companies in the workspace."""
    body = await _json_request(
        "GET",
        "/api/peppol/v1/notifications",
        params={"page": page, "pageSize": page_size},
    )
    raw_items = body.get("notifications")
    if not isinstance(raw_items, list):
        raise AdemicoUnavailableError("uventet svarformat fra tilgangspunktet")
    items = [parsed for raw in raw_items if (parsed := _notification(raw)) is not None]
    pagination = body.get("pagination")
    count = pagination.get("count", len(items)) if isinstance(pagination, dict) else len(items)
    return items, int(count)


def notifications_from_push(body: object) -> list[Notification]:
    """The notifications in a webhook body, whatever envelope it came in.

    Ademico documents its webhook settings but not the body it pushes. With
    ``bulk: false`` it is presumably one notification as the queue lists them,
    with ``bulk: true`` several. Accepted: one notification, a JSON array of
    them, or ``{"notifications": [...]}`` as ``GET …/notifications`` answers.
    Nothing read here is believed (myntos.integrations.peppol_sync).
    """
    if isinstance(body, dict) and isinstance(body.get("notifications"), list):
        items: list[object] = body["notifications"]
    elif isinstance(body, list):
        items = body
    else:
        items = [body]
    parsed = [
        notification
        for raw in items[:_MAX_PUSHED_NOTIFICATIONS]
        if (notification := _notification(raw, require_id=False)) is not None
    ]
    return parsed


async def consume_notification(notification_id: int) -> None:
    """Remove a processed notification from Ademico's queue."""
    await _raw_request("DELETE", f"/api/peppol/v1/notifications/{notification_id}")


def _webhook_settings(body: dict[str, Any]) -> WebhookSettings:
    auth = body.get("authentication")
    auth = auth if isinstance(auth, dict) else {}
    return WebhookSettings(
        active=bool(body.get("active")),
        bulk=bool(body.get("bulk")),
        url=_text(body, "webhookUrl"),
        authentication=_text(auth, "type") or "NONE",
        user=_text(auth, "user"),
    )


async def webhook_settings() -> WebhookSettings:
    """Where Ademico pushes this deployment's events now."""
    return _webhook_settings(await _json_request("GET", "/api/peppol/v1/settings/webhooks"))


async def set_webhook(*, url: str, secret: str) -> None:
    """Tell Ademico to push every event to ``url``, one per request, with Basic Auth.

    ``PUT`` rather than ``PATCH``, so nothing set by hand in Ademico's portal
    (an OAuth2 block, extra headers) survives beside it. One event per request
    (``bulk: false``) so a 503 asks Ademico to push again only the event that
    could not be confirmed, and so each request stays well inside Ademico's
    timeout: a received invoice costs Myntos two calls back to Ademico.
    """
    await _json_request(
        "PUT",
        "/api/peppol/v1/settings/webhooks",
        json={
            "active": True,
            "bulk": False,
            "webhookUrl": url,
            "authentication": {"type": "BASIC_AUTH", "user": WEBHOOK_USER, "password": secret},
            "additionalHeaders": {},
        },
    )


async def disable_webhook() -> None:
    """Stop Ademico pushing. Everything is then left to the sweep."""
    await _json_request("PATCH", "/api/peppol/v1/settings/webhooks", json={"active": False})


async def get_invoice_ubl(transmission_id: str) -> bytes:
    """Download the raw UBL XML of a received (or sent) invoice or credit note."""
    response = await _raw_request(
        "GET",
        f"/api/peppol/v1/invoices/{transmission_id}/ubl",
        accept="application/xml",
    )
    if not response.content:
        raise AdemicoUnavailableError("tomt svar fra tilgangspunktet")
    return response.content
