"""Public Peppol SML/SMP lookup — whether a Norwegian business can receive EHF.

Ademico's REST API has no participant-lookup endpoint, and Peppol's Service
Metadata Locator (SML) and Service Metadata Publishers (SMPs, including ELMA
in Norway) are public infrastructure by design. Querying them directly carries
no credentials and sends nothing outside Myntos except the SHA-256 hash of
``0192:<orgnr>`` in DNS and the public orgnr to the recipient's own SMP.

How OpenPeppol BDXL + SMP 1.0 discovery works:

1. Compute ``base32(sha256("0192:<orgnr>")).rstrip("=").lower()`` and query a
   DNS ``NAPTR`` record under ``.iso6523-actorid-upis.<sml_zone>`` (in
   production: ``participant.sml.prod.tech.peppol.org``).
2. ``NXDOMAIN`` or no ``Meta:SMP`` record means the business is not registered
   to receive on Peppol at all (``False``).
3. Otherwise the NAPTR regexp gives the participant's SMP base URL (e.g.
   ``https://smp.elma-smp.no/``). ``GET {smp}/iso6523-actorid-upis::0192%3A<orgnr>``
   returns the XML ServiceGroup listing every document type the receiver accepts.
4. Check whether the exact Peppol BIS Billing 3.0 Invoice or CreditNote
   document identifier appears in ``ServiceMetadataReference/@href``.

Transient DNS or HTTP errors raise :class:`SmpLookupError` rather than
returning ``False``, because the review step treats an unavailable lookup as
"unknown, still try sending" rather than quietly falling back to PDF
(docs/integrations/peppol.md).
"""

from __future__ import annotations

import base64
import hashlib
import logging
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote, urlparse
from xml.parsers import expat

import dns.asyncresolver
import dns.exception
import dns.resolver
import httpx

from myntos.core.config import get_settings

logger = logging.getLogger(__name__)

NORWEGIAN_SCHEME = "0192"
META_SCHEME = "iso6523-actorid-upis"

# Peppol BIS Billing 3.0 (EHF 3.0) document type identifiers in SMP hrefs.
INVOICE_DOCTYPE = (
    "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2::Invoice"
    "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
)
CREDIT_NOTE_DOCTYPE = (
    "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2::CreditNote"
    "##urn:cen.eu:en16931:2017#compliant#urn:fdc:peppol.eu:2017:poacc:billing:3.0::2.1"
)

_HTTP_TIMEOUT = httpx.Timeout(10.0, connect=4.0)
_DNS_TIMEOUT = 5.0


class SmpLookupError(Exception):
    """The public Peppol SML or SMP could not be reached or answered nonsense."""


class _DeclarationRefusedError(Exception):
    """Raised by expat handlers when an external SMP answer carries a DTD."""


def _refuse(*_: object) -> None:
    raise _DeclarationRefusedError


def _refuse_external(*_: object) -> int:
    raise _DeclarationRefusedError


def _parse_xml(content: bytes) -> ET.Element:
    """Parse SMP XML without ever reading a DTD or external entity."""
    builder = ET.TreeBuilder()
    parser = expat.ParserCreate(namespace_separator="}")
    parser.SetParamEntityParsing(expat.XML_PARAM_ENTITY_PARSING_NEVER)
    parser.StartDoctypeDeclHandler = _refuse
    parser.EntityDeclHandler = _refuse
    parser.UnparsedEntityDeclHandler = _refuse
    parser.NotationDeclHandler = _refuse
    parser.ExternalEntityRefHandler = _refuse_external
    parser.StartElementHandler = builder.start
    parser.EndElementHandler = builder.end
    parser.CharacterDataHandler = builder.data
    try:
        parser.Parse(content, True)
    except (_DeclarationRefusedError, expat.ExpatError) as error:
        raise SmpLookupError("ugyldig XML fra SMP") from error
    return builder.close()


def bdxl_hostname(orgnr: str, *, sml_zone: str | None = None) -> str:
    """The BDXL DNS name for ``0192:<orgnr>`` under ``sml_zone``."""
    zone = (sml_zone or get_settings().peppol_sml_zone).strip().rstrip(".")
    participant = f"{NORWEGIAN_SCHEME}:{orgnr}".lower().encode("ascii")
    digest = base64.b32encode(hashlib.sha256(participant).digest()).decode("ascii").rstrip("=")
    return f"{digest.lower()}.{META_SCHEME}.{zone}"


def _extract_smp_url(service: str, regexp: str) -> str | None:
    """Read the SMP URL from a ``Meta:SMP`` NAPTR record's regexp field.

    RFC 3403 delimits the regexp with its first character (typically ``!``):
    ``!.*!https://smp.elma-smp.no/!``.
    """
    if service.lower() != "meta:smp" or len(regexp) < 4:
        return None
    delimiter = regexp[0]
    parts = regexp.split(delimiter)
    if len(parts) < 3:
        return None
    candidate = parts[2].strip()
    parsed = urlparse(candidate)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        return None
    return candidate.rstrip("/")


async def _resolve_smp_url(hostname: str) -> str | None:
    """Query DNS NAPTR for ``hostname`` and return the SMP base URL, or ``None`` if absent.

    Kept as a small helper so tests can replace DNS lookup without mocking
    dnspython internals.
    """
    resolver = dns.asyncresolver.Resolver()
    resolver.lifetime = _DNS_TIMEOUT
    try:
        answer = await resolver.resolve(hostname, "NAPTR")
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return None
    except dns.exception.DNSException as error:
        logger.error("Peppol SML DNS lookup failed: %s", type(error).__name__, exc_info=error)
        raise SmpLookupError("fikk ikke svar fra Peppol SML") from error

    for record in answer:
        raw_service = getattr(record, "service", b"")
        raw_regexp = getattr(record, "regexp", b"")
        service = (
            raw_service.decode("ascii", errors="ignore")
            if isinstance(raw_service, bytes)
            else str(raw_service)
        )
        regexp = (
            raw_regexp.decode("utf-8", errors="ignore")
            if isinstance(raw_regexp, bytes)
            else str(raw_regexp)
        )
        url = _extract_smp_url(service, regexp)
        if url is not None:
            return url
    return None


def _client() -> httpx.AsyncClient:
    """The HTTP client for SMP fetches. A function so tests can hand in a mock transport."""
    return httpx.AsyncClient(timeout=_HTTP_TIMEOUT, follow_redirects=False)


def _supports_doctype(xml_bytes: bytes, doctype: str) -> bool:
    """Whether ``doctype`` appears in any ``ServiceMetadataReference/@href`` of a ServiceGroup."""
    root = _parse_xml(xml_bytes)
    for elem in root.iter():
        local = elem.tag.rsplit("}", 1)[-1]
        if local != "ServiceMetadataReference":
            continue
        href = elem.attrib.get("href") or ""
        # SMP servers vary between single- and double-encoding colons/hashes in hrefs.
        decoded = unquote(unquote(href))
        if doctype in decoded:
            return True
    return False


async def registered_smp_url(orgnr: str) -> str | None:
    """The SMP base URL for ``0192:<orgnr>``, or ``None`` when not registered in SML."""
    return await _resolve_smp_url(bdxl_hostname(orgnr))


async def can_receive(orgnr: str, *, credit_note: bool) -> bool:
    """Whether ``0192:<orgnr>`` is registered on Peppol to receive BIS Billing 3.0."""
    smp_base = await registered_smp_url(orgnr)
    if smp_base is None:
        return False
    participant = quote(f"{NORWEGIAN_SCHEME}:{orgnr}", safe="")
    url = f"{smp_base}/{META_SCHEME}::{participant}"
    try:
        async with _client() as client:
            response = await client.get(url, headers={"Accept": "application/xml, text/xml"})
    except httpx.HTTPError as error:
        logger.error("Peppol SMP lookup failed: %s", type(error).__name__, exc_info=error)
        raise SmpLookupError("fikk ikke svar fra mottakerens SMP") from error

    if response.status_code == httpx.codes.NOT_FOUND:
        return False
    if response.is_error:
        logger.error("Peppol SMP answered with %d", response.status_code)
        raise SmpLookupError("uventet svar fra mottakerens SMP")

    doctype = CREDIT_NOTE_DOCTYPE if credit_note else INVOICE_DOCTYPE
    return _supports_doctype(response.content, doctype)
