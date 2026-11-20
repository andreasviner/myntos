"""Recommand — the Peppol access point that carries an EHF to the customer.

From 1 January 2027 a sale to another bokføringspliktig is documented by an
e-invoice, and an e-invoice is only delivered when it travels over Peppol. Peppol
is not something a program joins on its own: every sender and receiver goes
through an access point certified by the network, and Recommand (BRBX BV,
Belgium) is the one this deployment uses. Myntos writes the EHF and freezes it at
issue (:mod:`myntos.domain.faktura.ehf`); this module only hands that frozen XML
over and asks afterwards whether it arrived.

Three decisions shape it, all explained at length in
docs/integrations/peppol.md:

**Peppol only, never e-mail.** Recommand will e-mail a document instead of, or
beside, a Peppol transmission, and its mail goes through Postmark in the USA. An
invoice is accounting data, which never leaves the EEA (CLAUDE.md rule 5), so no
request here carries ``email`` and a customer who cannot receive over Peppol is
answered with a refusal the person acts on, not with a fallback.

**The frozen XML, as it is.** Recommand can build a document from its own JSON
model, and would then be writing the sales document instead of Myntos. The XML
the seller issued is what is sent, with the document type and process named
explicitly rather than detected, so a change in how Recommand guesses cannot
change what goes out.

**Send only.** Each company is created with ``isSmpRecipient`` off. Registering a
Norwegian organisasjonsnummer as a receiver in ELMA is exclusive: it would take
over whatever inbox the company receives its supplier invoices in today, from an
access point Myntos does not run, and Myntos has nowhere to put an incoming
invoice yet.

One key for the whole deployment (``RECOMMAND_API_KEY`` and
``RECOMMAND_API_SECRET``); each Myntos company is a company under it. Logging
never carries a body: the request is an invoice and the answer may quote it.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import SecretStr

from myntos.core.config import get_settings

logger = logging.getLogger(__name__)

# Norway's Peppol participant scheme: the organisasjonsnummer. ELMA registers
# Norwegian businesses under this one only.
NORWEGIAN_SCHEME = "0192"

# Peppol BIS Billing 3.0, which EHF 3 is. Named on every send instead of
# detected; see the module docstring.
INVOICE_DOCTYPE = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
    "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
)
CREDIT_NOTE_DOCTYPE = (
    "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2::CreditNote"
    "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
)
BILLING_PROCESS = "urn:fdc:peppol.eu:2017:poacc:billing:01:1.0"

# Ordinary calls are short: a lookup that hangs costs the person a page that
# will not load. A send is handed to the receiving access point over AS4 before
# Recommand answers, which takes seconds, and a send cut off by our own timeout
# is the one failure that leaves nobody knowing whether the invoice went.
_HTTP_TIMEOUT = httpx.Timeout(15.0, connect=5.0)
_SEND_TIMEOUT = httpx.Timeout(90.0, connect=5.0)


class RecommandError(Exception):
    """Base class for every reason the access point did not do what was asked."""


class RecommandNotConfiguredError(RecommandError):
    """No key. The deployment sends no EHF."""


class RecommandUnavailableError(RecommandError):
    """Recommand could not be reached, refused our key, or answered nonsense."""


class RecommandNotFoundError(RecommandError):
    """Recommand holds no company or document with that id."""


class RecommandRefusedError(RecommandError):
    """Recommand refused the request itself, and said why.

    ``problems`` is its own list, flattened to sentences. For a send that is a
    validation finding on an XML Myntos wrote, which is ours to fix, so it is
    kept for the person to quote rather than paraphrased away.
    """

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class RecipientUnreachableError(RecommandError):
    """The customer cannot be reached over Peppol. Nothing was sent or stored."""

    def __init__(self, message: str, problems: list[str]) -> None:
        super().__init__(message)
        self.problems = problems


class SendTimedOutError(RecommandError):
    """The send was made and no answer came, so whether it went is unknown."""


@dataclass(frozen=True)
class RemoteCompany:
    """What Myntos needs from Recommand's record of one company."""

    id: str
    enterprise_number: str
    # An authorised representative has completed Recommand's identity check.
    # Nothing can be sent before it.
    is_verified: bool


@dataclass(frozen=True)
class Delivery:
    """Where a sent document stands, summarised over its Peppol deliveries.

    ``status`` is Recommand's: ``pending`` (handed to the network, no receipt
    yet), ``delivered`` (the customer's access point acknowledged it) or
    ``failed``. ``failure_category`` is its fixed vocabulary
    (``recipient_not_found``, ``document_not_supported``, ``validation``,
    ``transport``, ``recipient_rejected``, ``duplicate``, ``other``) and
    ``failure_message`` the access point's own words.
    """

    status: str
    failure_category: str | None = None
    failure_message: str | None = None


@dataclass(frozen=True)
class Sent:
    """The answer to a send: Recommand's id for the document and where it stands."""

    id: str
    delivery: Delivery


def _secret(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    text = value.get_secret_value().strip()
    return text or None


def _credentials() -> tuple[str, str] | None:
    """Key and secret, treating a blank half as no key at all.

    ``RECOMMAND_API_KEY=`` in a ``.env`` is how a deployment says "not this
    one", and the test suite relies on it: an empty key must never reach a live
    access point with an empty password.
    """
    settings = get_settings()
    key = _secret(settings.recommand_api_key)
    secret = _secret(settings.recommand_api_secret)
    if key is None or secret is None:
        return None
    return key, secret


def is_configured() -> bool:
    """Whether this deployment has an access point to send through."""
    return _credentials() is not None


def peppol_address(orgnr: str) -> str:
    """``0192:<orgnr>``, how Peppol names a Norwegian business."""
    return f"{NORWEGIAN_SCHEME}:{orgnr}"


def _client(timeout: httpx.Timeout = _HTTP_TIMEOUT) -> httpx.AsyncClient:
    """The HTTP client. A function so the tests can hand in a mock transport."""
    return httpx.AsyncClient(timeout=timeout)


def _problems(body: object) -> list[str]:
    """Recommand's ``errors`` object, ``{field: [message, …]}``, as sentences."""
    if not isinstance(body, dict):
        return []
    errors = body.get("errors")
    if not isinstance(errors, dict):
        return []
    out: list[str] = []
    for field, messages in errors.items():
        if not isinstance(messages, list):
            continue
        for message in messages:
            if not isinstance(message, str):
                continue
            # The root field carries general refusals; a named one says which
            # part of the request it is about.
            out.append(message if field in ("", "root", "_") else f"{field}: {message}")
    return out[:20]


async def _request(
    method: str,
    path: str,
    *,
    json: dict[str, Any] | None = None,
    params: dict[str, str] | None = None,
    timeout: httpx.Timeout = _HTTP_TIMEOUT,
) -> dict[str, Any]:
    credentials = _credentials()
    if credentials is None:
        raise RecommandNotConfiguredError("e-faktura er ikke satt opp i denne installasjonen")
    url = f"{get_settings().recommand_api_base_url.rstrip('/')}{path}"
    try:
        async with _client(timeout) as client:
            response = await client.request(method, url, json=json, params=params, auth=credentials)
    except httpx.TimeoutException as error:
        if (
            method == "POST"
            and path.endswith("/send")
            and not isinstance(error, httpx.ConnectTimeout)
        ):
            # The XML was on its way and no answer came: it may well have gone.
            # At ERROR, so the operator's page and mail have it (docs/operator.md).
            logger.error("Recommand did not answer a send in time")
            raise SendTimedOutError("tilgangspunktet svarte ikke i tide") from error
        logger.info("Recommand could not be reached: %s", type(error).__name__)
        raise RecommandUnavailableError("fikk ikke kontakt med tilgangspunktet") from error
    except httpx.HTTPError as error:
        logger.info("Recommand could not be reached: %s", type(error).__name__)
        raise RecommandUnavailableError("fikk ikke kontakt med tilgangspunktet") from error

    try:
        body: object = response.json()
    except ValueError:
        body = None

    if response.status_code == httpx.codes.UNAUTHORIZED:
        # Ours to fix, not the person's: the deployment's key is wrong or was
        # revoked. At ERROR, so it is on the operator's page and in their mail
        # before customers say so (docs/operator.md).
        logger.error("Recommand refused the deployment's key on %s %s", method, path)
        raise RecommandUnavailableError("tilgangspunktet godtok ikke nøkkelen til Myntos")
    if response.status_code == httpx.codes.NOT_FOUND:
        raise RecommandNotFoundError("tilgangspunktet kjenner ikke denne")
    if response.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
        raise RecipientUnreachableError("kunden kan ikke nås over Peppol", _problems(body))
    if response.status_code == httpx.codes.BAD_REQUEST:
        problems = _problems(body)
        logger.info("Recommand refused %s %s with %d problems", method, path, len(problems))
        raise RecommandRefusedError("tilgangspunktet avviste forespørselen", problems)
    if response.is_error or not isinstance(body, dict):
        logger.info("Recommand answered %s %s with %d", method, path, response.status_code)
        raise RecommandUnavailableError("uventet svar fra tilgangspunktet")
    return body


def _company(raw: object) -> RemoteCompany:
    if not isinstance(raw, dict):
        raise RecommandUnavailableError("uventet svarformat fra tilgangspunktet")
    try:
        return RemoteCompany(
            id=str(raw["id"]),
            enterprise_number=str(raw.get("enterpriseNumber") or ""),
            is_verified=bool(raw.get("isVerified")),
        )
    except KeyError as error:
        raise RecommandUnavailableError("uventet svarformat fra tilgangspunktet") from error


def _delivery(raw: dict[str, Any]) -> Delivery:
    """Where a document stands, from its Peppol deliveries.

    The summary ``deliveryStatus`` is Recommand's own; the failure is taken from
    the Peppol delivery, which is the only kind Myntos asks for.
    """
    status = raw.get("deliveryStatus")
    peppol = [
        each
        for each in raw.get("deliveries") or []
        if isinstance(each, dict) and each.get("channel") == "peppol"
    ]
    if status not in ("pending", "delivered", "failed"):
        status = peppol[0].get("status") if peppol else None
    if status not in ("pending", "delivered", "failed"):
        # Handed over with no delivery to report on is the pending case: the
        # answer to "did it arrive" is not in yet.
        status = "pending"
    failure = next(
        (each["failure"] for each in peppol if isinstance(each.get("failure"), dict)), None
    )
    if status != "failed" or failure is None:
        return Delivery(status=status)
    message = failure.get("message")
    return Delivery(
        status=status,
        failure_category=str(failure.get("category") or "other"),
        failure_message=str(message)[:1000] if message else None,
    )


async def find_company(orgnr: str) -> RemoteCompany | None:
    """The company under this deployment's key with this orgnr, if one exists.

    Two Myntos companies for one business (two accounts that have each
    confirmed it through Altinn) are one sender to the network, since Peppol
    names the sender by orgnr, so the second adopts the first one's record.
    Asking first is also what makes a registration that failed halfway safe to
    try again.
    """
    body = await _request("GET", "/api/v1/companies", params={"enterpriseNumber": orgnr})
    companies = body.get("companies")
    if not isinstance(companies, list):
        raise RecommandUnavailableError("uventet svarformat fra tilgangspunktet")
    for raw in companies:
        company = _company(raw)
        if company.enterprise_number == orgnr:
            return company
    return None


async def create_company(
    *,
    orgnr: str,
    name: str,
    address: str,
    postal_code: str,
    city: str,
    vat_registered: bool,
) -> tuple[RemoteCompany, str | None]:
    """Create the company for sending, and the link for its identity check.

    The fields are the registered ones, the same as on the invoice's seller
    party. No e-mail address and no phone number: Recommand would only use them
    to contact the company, and contacting it is Myntos's business.
    """
    body = await _request(
        "POST",
        "/api/v1/companies",
        json={
            "name": name,
            "address": address,
            "postalCode": postal_code,
            "city": city,
            "country": "NO",
            "enterpriseNumberScheme": NORWEGIAN_SCHEME,
            "enterpriseNumber": orgnr,
            "vatNumber": f"NO{orgnr}MVA" if vat_registered else None,
            # Send only: see the module docstring.
            "isSmpRecipient": False,
        },
    )
    url = body.get("verificationUrl")
    return _company(body.get("company")), url if isinstance(url, str) and url else None


async def get_company(company_id: str) -> RemoteCompany:
    body = await _request("GET", f"/api/v1/companies/{company_id}")
    return _company(body.get("company"))


async def verification_url(company_id: str) -> str:
    """A fresh link to Recommand's identity check for the company."""
    body = await _request("POST", f"/api/v1/companies/{company_id}/verify")
    url = body.get("verificationUrl")
    if not isinstance(url, str) or not url:
        raise RecommandUnavailableError("uventet svarformat fra tilgangspunktet")
    return url


async def can_receive(orgnr: str, *, credit_note: bool) -> bool:
    """Whether the customer is registered on Peppol to receive this kind of document.

    Asked before an invoice is issued, so the page can make sending it as EHF
    the default for a customer who can take it, and say so plainly for one who
    cannot, instead of finding out from a refused send after the invoice is
    issued. Recommand answers ``isValid: false`` for any lookup that fails at
    its end, an orgnr that is not registered at all included; only our side
    failing (no key, no answer) raises.
    """
    body = await _request(
        "POST",
        "/api/v1/verify-document-support",
        json={
            "peppolAddress": peppol_address(orgnr),
            "documentType": CREDIT_NOTE_DOCTYPE if credit_note else INVOICE_DOCTYPE,
            "processId": BILLING_PROCESS,
        },
    )
    return body.get("isValid") is True


async def send(company_id: str, *, recipient_orgnr: str, xml: str, credit_note: bool) -> Sent:
    """Hand the frozen EHF to the network, addressed to the customer's orgnr."""
    body = await _request(
        "POST",
        f"/api/v1/{company_id}/send",
        json={
            "recipient": peppol_address(recipient_orgnr),
            "documentType": "xml",
            "document": xml,
            "doctypeId": CREDIT_NOTE_DOCTYPE if credit_note else INVOICE_DOCTYPE,
            "processId": BILLING_PROCESS,
        },
        timeout=_SEND_TIMEOUT,
    )
    document_id = body.get("id")
    if not isinstance(document_id, str) or not document_id:
        raise RecommandUnavailableError("uventet svarformat fra tilgangspunktet")
    return Sent(id=document_id, delivery=_delivery(body))


async def delivery(document_id: str) -> Delivery:
    """Where an earlier send stands now."""
    body = await _request("GET", f"/api/v1/documents/{document_id}")
    document = body.get("document")
    if not isinstance(document, dict):
        raise RecommandUnavailableError("uventet svarformat fra tilgangspunktet")
    return _delivery(document)
