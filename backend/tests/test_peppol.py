"""Sending and receiving EHF over Peppol via Ademico, plus public SML/SMP lookup.

Structured in five parts:
1. The Ademico client (wire format, Basic Auth + accessToken, multipart frozen
   XML, send-only legal-entity creation, receiving toggles, error and status mapping).
2. Public OpenPeppol BDXL SML/SMP lookup (hostname hash, NAPTR regexp, ServiceGroup
   parsing for Invoice vs CreditNote, DTD refusal).
3. Incoming EHF UBL parser and readable PDF generation (Invoice, CreditNote,
   Decimals, DTD refusal, embedded PDF vs rendered fallback, VAT code suggestion).
4. Registration, receiving opt-in, ELMA conflicts, single-inbox-per-orgnr guard,
   and outgoing sending API routes.
5. Ademico's webhook (only its credentials open it; every push read back from
   Ademico and filed where Ademico says), the sweep behind it across multiple
   companies (queue, document list, pending sends), crash-safe deduplication,
   inbox/detail/XML/PDF routes, booking into ledger + bilag archive, dismiss/reopen,
   agent key scopes & approval, and company closure/retention hooks.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from urllib.parse import quote, unquote

import httpx
import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from myntos.api.v1.saft_innlesing import get_import_sessions
from myntos.core.clock import utc_now
from myntos.core.config import get_settings
from myntos.domain.peppol import incoming_pdf, ubl_reader
from myntos.domain.peppol.models import IncomingDocumentType, IncomingStatus, PeppolIncomingDocument
from myntos.integrations import ademico, peppol_smp, peppol_sync
from tests.conftest import PASSWORD as USER_PASSWORD
from tests.conftest import agent_header, join_company, register, write
from tests.test_faktura_api import CUSTOMER_ORGNR, ORGNR, create_draft, issue, make_company

USER = "test_user"
PASSWORD = "test_password"
TOKEN = "test-access-token"
SECOND_ORGNR = "910000012"
SAMPLE_PDF = b"%PDF-1.7\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"
WEBHOOK_SECRET = "3f9c2a7e5b1d48c6a0e7f2b9d4c1a8e6"
WEBHOOK_URL = "/api/v1/webhooks/ademico"


def build_incoming_ubl(
    *,
    kind: str = "Invoice",
    invoice_number: str = "LEV-2026-100",
    issue_date: str = "2026-03-14",
    due_date: str = "2026-03-28",
    currency: str = "NOK",
    net: str = "1000.00",
    vat: str = "250.00",
    gross: str = "1250.00",
    vat_percent: str = "25",
    vat_category: str = "S",
    seller_name: str = "Fjord Leverandør AS",
    seller_orgnr: str = CUSTOMER_ORGNR,
    payment_id: str = "0001234567",
    bank_account: str = "15062033158",
    embedded_pdf: bytes | None = None,
    embedded_mime: str = "application/pdf",
) -> bytes:
    """Construct a valid Peppol BIS Billing 3.0 UBL Invoice or CreditNote."""
    ns = (
        "urn:oasis:names:specification:ubl:schema:xsd:CreditNote-2"
        if kind == "CreditNote"
        else "urn:oasis:names:specification:ubl:schema:xsd:Invoice-2"
    )
    line_tag = "CreditNoteLine" if kind == "CreditNote" else "InvoiceLine"
    qty_tag = "CreditedQuantity" if kind == "CreditNote" else "InvoicedQuantity"
    attachment_xml = ""
    if embedded_pdf is not None:
        encoded = base64.b64encode(embedded_pdf).decode("ascii")
        attachment_xml = (
            "<cac:AdditionalDocumentReference>"
            "<cbc:ID>VEDLEGG-1</cbc:ID>"
            "<cac:Attachment>"
            f'<cbc:EmbeddedDocumentBinaryObject mimeCode="{embedded_mime}" '
            f'filename="leverandor-faktura.pdf">{encoded}</cbc:EmbeddedDocumentBinaryObject>'
            "</cac:Attachment>"
            "</cac:AdditionalDocumentReference>"
        )
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<{kind} xmlns="{ns}" '
        'xmlns:cac="urn:oasis:names:specification:ubl:schema:xsd:CommonAggregateComponents-2" '
        'xmlns:cbc="urn:oasis:names:specification:ubl:schema:xsd:CommonBasicComponents-2">'
        f"<cbc:ID>{invoice_number}</cbc:ID>"
        f"<cbc:IssueDate>{issue_date}</cbc:IssueDate>"
        f"<cbc:DueDate>{due_date}</cbc:DueDate>"
        f"<cbc:DocumentCurrencyCode>{currency}</cbc:DocumentCurrencyCode>"
        "<cbc:BuyerReference>AVD-IT</cbc:BuyerReference>"
        "<cac:OrderReference><cbc:ID>PO-42</cbc:ID></cac:OrderReference>"
        f"{attachment_xml}"
        "<cac:AccountingSupplierParty><cac:Party>"
        f'<cbc:EndpointID schemeID="0192">{seller_orgnr}</cbc:EndpointID>'
        "<cac:PostalAddress>"
        "<cbc:StreetName>Storgata 5</cbc:StreetName>"
        "<cbc:CityName>Bergen</cbc:CityName>"
        "<cbc:PostalZone>5003</cbc:PostalZone>"
        "<cac:Country><cbc:IdentificationCode>NO</cbc:IdentificationCode></cac:Country>"
        "</cac:PostalAddress>"
        "<cac:PartyLegalEntity>"
        f"<cbc:RegistrationName>{seller_name}</cbc:RegistrationName>"
        f'<cbc:CompanyID schemeID="0192">{seller_orgnr}</cbc:CompanyID>'
        "</cac:PartyLegalEntity>"
        "</cac:Party></cac:AccountingSupplierParty>"
        "<cac:PaymentMeans><cbc:PaymentMeansCode>30</cbc:PaymentMeansCode>"
        f"<cbc:PaymentID>{payment_id}</cbc:PaymentID>"
        f"<cac:PayeeFinancialAccount><cbc:ID>{bank_account}</cbc:ID></cac:PayeeFinancialAccount>"
        "</cac:PaymentMeans>"
        f'<cac:TaxTotal><cbc:TaxAmount currencyID="{currency}">{vat}</cbc:TaxAmount>'
        "<cac:TaxSubtotal>"
        f'<cbc:TaxableAmount currencyID="{currency}">{net}</cbc:TaxableAmount>'
        f'<cbc:TaxAmount currencyID="{currency}">{vat}</cbc:TaxAmount>'
        f"<cac:TaxCategory><cbc:ID>{vat_category}</cbc:ID><cbc:Percent>{vat_percent}</cbc:Percent>"
        "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme></cac:TaxCategory>"
        "</cac:TaxSubtotal></cac:TaxTotal>"
        "<cac:LegalMonetaryTotal>"
        f'<cbc:LineExtensionAmount currencyID="{currency}">{net}</cbc:LineExtensionAmount>'
        f'<cbc:TaxExclusiveAmount currencyID="{currency}">{net}</cbc:TaxExclusiveAmount>'
        f'<cbc:TaxInclusiveAmount currencyID="{currency}">{gross}</cbc:TaxInclusiveAmount>'
        f'<cbc:PayableAmount currencyID="{currency}">{gross}</cbc:PayableAmount>'
        "</cac:LegalMonetaryTotal>"
        f"<cac:{line_tag}><cbc:ID>1</cbc:ID>"
        f'<cbc:{qty_tag} unitCode="EA">2</cbc:{qty_tag}>'
        f'<cbc:LineExtensionAmount currencyID="{currency}">{net}</cbc:LineExtensionAmount>'
        "<cac:Item><cbc:Name>Programvarelisens</cbc:Name>"
        "<cac:ClassifiedTaxCategory>"
        f"<cbc:ID>{vat_category}</cbc:ID><cbc:Percent>{vat_percent}</cbc:Percent>"
        "<cac:TaxScheme><cbc:ID>VAT</cbc:ID></cac:TaxScheme>"
        "</cac:ClassifiedTaxCategory></cac:Item>"
        f'<cac:Price><cbc:PriceAmount currencyID="{currency}">500.00</cbc:PriceAmount></cac:Price>'
        f"</cac:{line_tag}></{kind}>"
    )
    return xml.encode("utf-8")


def _extract_multipart_file(request: httpx.Request) -> tuple[str, bytes]:
    """Pull filename and verbatim file bytes out of a multipart/form-data upload."""
    content_type = request.headers.get("content-type", "")
    match = re.search(r"boundary=([^\s;]+)", content_type)
    assert match, f"missing multipart boundary in {content_type!r}"
    boundary = b"--" + match.group(1).strip('"').encode("ascii")
    for part in request.content.split(boundary):
        if b"Content-Disposition" not in part:
            continue
        header_block, _, body = part.partition(b"\r\n\r\n")
        if body.endswith(b"\r\n"):
            body = body[:-2]
        name_match = re.search(rb'filename="([^"]+)"', header_block)
        filename = name_match.group(1).decode("utf-8") if name_match else ""
        return filename, body
    raise AssertionError("no file part found in multipart request")


def ademico_time(when: datetime) -> str:
    """A timestamp as Ademico writes one: UTC, milliseconds, and no zone."""
    return when.astimezone(UTC).replace(tzinfo=None).isoformat(timespec="milliseconds")


class AdemicoStandIn:
    """In-memory stand-in matching Ademico's REST API OpenAPI & live TEST responses.

    Ademico's record of a document (``GET …/documents``) and the event that
    says it happened (the pull queue, or a push) are kept apart, as they are
    at Ademico: a pushed event need not be in the queue, and a document may be
    listed before or without any event about it.
    """

    def __init__(self) -> None:
        self.entities: dict[int, dict[str, Any]] = {}
        # Sent and received documents apart: one sent between two companies
        # under the same deployment is two records with one transmissionId.
        self.documents: dict[str, dict[str, Any]] = {}
        self.received_documents: dict[str, dict[str, Any]] = {}
        self.ubl_by_tx: dict[str, bytes] = {}
        self.notifications: list[dict[str, Any]] = []
        self.consumed_notifications: list[int] = []
        self.requests: list[tuple[str, str, Any]] = []
        self.queries: list[tuple[str, str, dict[str, str]]] = []
        self.auth_seen: set[str] = set()
        self.tokens_seen: set[str] = set()
        self.webhook: dict[str, Any] = {
            "active": False,
            "bulk": False,
            "webhookUrl": None,
            "authentication": {"type": "NONE"},
            "additionalHeaders": {},
        }
        self.next_entity_id = 100
        self.next_reg_id = 500
        self.next_notification_id = 1
        self.next_sent_id = 1
        # Overrides for simulating errors.
        self.down = False
        self.send_error: tuple[int, dict[str, Any]] | None = None
        self.elma_conflicts: set[str] = set()
        self.fail_consume_once: set[int] = set()

    def add_entity(self, orgnr: str, *, receiving: bool = False) -> tuple[int, int]:
        entity_id = self.next_entity_id
        self.next_entity_id += 1
        reg_id = self.next_reg_id
        self.next_reg_id += 1
        self.entities[entity_id] = {
            "legalEntityId": entity_id,
            "legalEntityDetails": {
                "publishInPeppolDirectory": receiving,
                "name": "Existing AS",
                "countryCode": "NO",
                "geographicalInformation": "Kirkegata 2, 0153 Oslo",
            },
            "peppolRegistrations": [
                {
                    "peppolRegistrationId": reg_id,
                    "peppolRegistrationDetails": {
                        "peppolIdentifier": {"scheme": "0192", "identifier": orgnr},
                        "supportedDocuments": list(ademico.SUPPORTED_DOCUMENTS),
                        "peppolRegistration": receiving,
                    },
                }
            ],
        }
        return entity_id, reg_id

    def receive(
        self,
        *,
        receiver_orgnr: str,
        raw_xml: bytes,
        sender_orgnr: str = CUSTOMER_ORGNR,
        document_type: str = "INVOICE",
        transmission_id: str | None = None,
    ) -> dict[str, Any]:
        """A document arrives at Ademico. Its record and XML; the event, unsent."""
        nid = self.next_notification_id
        self.next_notification_id += 1
        tx_id = transmission_id or f"in-tx-{nid}"
        self.ubl_by_tx[tx_id] = raw_xml
        self.received_documents[tx_id] = {
            "transmissionId": tx_id,
            "sbdhTransmissionId": f"sbdh-{tx_id}",
            "direction": "RECEIVING",
            "peppolDocumentType": document_type,
            "documentStatus": "RECEIVED",
            "sender": f"0192:{sender_orgnr}",
            "receiver": f"0192:{receiver_orgnr}",
            "creationTime": ademico_time(utc_now()),
        }
        return {
            "notificationId": nid,
            "eventType": "DOCUMENT_RECEIVED",
            "transmissionId": tx_id,
            "sbdhTransmissionId": f"sbdh-{tx_id}",
            "documentId": f"INV-{nid}",
            "peppolDocumentType": document_type,
            "documentStatus": "RECEIVED",
            "sender": f"0192:{sender_orgnr}",
            "receiver": f"0192:{receiver_orgnr}",
            "receivedDate": "2026-03-14T10:00:00.000",
            "notificationDate": "2026-03-14T10:00:01.000",
            "details": [],
        }

    def queue_received(self, **kwargs: Any) -> tuple[int, str]:
        event = self.receive(**kwargs)
        self.notifications.append(event)
        return event["notificationId"], event["transmissionId"]

    def sending_event(
        self,
        *,
        transmission_id: str,
        event_type: str = "DOCUMENT_SENT",
        document_status: str = "SENT",
        details: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Something happens to a send. Its record moves on; the event, unsent."""
        nid = self.next_notification_id
        self.next_notification_id += 1
        if transmission_id in self.documents:
            self.documents[transmission_id]["documentStatus"] = document_status
        return {
            "notificationId": nid,
            "eventType": event_type,
            "transmissionId": transmission_id,
            "documentStatus": document_status,
            "notificationDate": "2026-03-14T10:05:00.000",
            "details": details or [],
        }

    def queue_sending_event(self, **kwargs: Any) -> int:
        event = self.sending_event(**kwargs)
        self.notifications.append(event)
        nid: int = event["notificationId"]
        return nid

    def set_document_status(self, transmission_id: str, status: str) -> None:
        self.documents[transmission_id]["documentStatus"] = status

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.auth_seen.add(request.headers.get("authorization", ""))
        self.tokens_seen.add(request.url.params.get("accessToken", ""))
        path = request.url.path
        if "/api/peppol/v1/" in path:
            path = path[path.index("/api/peppol/v1/") :]
        params = {k: v for k, v in request.url.params.items() if k != "accessToken"}
        self.queries.append((request.method, path, params))
        if self.down:
            return httpx.Response(503, json={"message": "maintenance"})

        if request.method == "POST" and path == "/api/peppol/v1/invoices/ubl-submissions":
            filename, raw = _extract_multipart_file(request)
            self.requests.append((request.method, path, {"filename": filename, "xml": raw}))
            if self.send_error is not None:
                status_code, payload = self.send_error
                return httpx.Response(status_code, json=payload)
            tx_id = f"tx-{self.next_sent_id}"
            self.next_sent_id += 1
            self.documents[tx_id] = {
                "transmissionId": tx_id,
                "direction": "SENDING",
                "documentStatus": "CREATED",
                "creationTime": ademico_time(utc_now()),
            }
            self.ubl_by_tx[tx_id] = raw
            return httpx.Response(200, json={"transmissionId": tx_id})

        body: Any = None
        if request.content:
            body = json.loads(request.content)
        self.requests.append((request.method, path, body))

        if request.method == "GET" and path == "/api/peppol/v1/legal-entities":
            items = list(self.entities.values())
            return httpx.Response(
                200,
                json={
                    "legalEntities": items,
                    "pagination": {"count": len(items), "page": 0, "pageSize": 100},
                },
            )

        if request.method == "POST" and path == "/api/peppol/v1/legal-entities":
            entity_id = self.next_entity_id
            self.next_entity_id += 1
            regs = []
            for reg in body["peppolRegistrations"]:
                reg_id = self.next_reg_id
                self.next_reg_id += 1
                regs.append({"peppolRegistrationId": reg_id, "peppolRegistrationDetails": reg})
            self.entities[entity_id] = {
                "legalEntityId": entity_id,
                "legalEntityDetails": body["legalEntityDetails"],
                "peppolRegistrations": regs,
            }
            return httpx.Response(200, json={"legalEntityId": entity_id})

        m = re.fullmatch(r"/api/peppol/v1/legal-entities/(\d+)/peppol-registrations", path)
        if request.method == "GET" and m:
            entity = self.entities.get(int(m.group(1)))
            if entity is None:
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            return httpx.Response(200, json={"peppolRegistrations": entity["peppolRegistrations"]})

        m = re.fullmatch(r"/api/peppol/v1/legal-entities/(\d+)/peppol-registrations/(\d+)", path)
        if request.method == "PUT" and m:
            entity = self.entities.get(int(m.group(1)))
            reg_id = int(m.group(2))
            if entity is None:
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            details = body["peppolRegistrationDetails"]
            ident = details["peppolIdentifier"]["identifier"]
            if details["peppolRegistration"] and ident in self.elma_conflicts:
                return httpx.Response(
                    400,
                    json={
                        "code": "PARTICIPANT_ERROR",
                        "message": "Participant already registered in SML/SMP",
                    },
                )
            for reg in entity["peppolRegistrations"]:
                if reg["peppolRegistrationId"] == reg_id:
                    reg["peppolRegistrationDetails"] = details
                    return httpx.Response(200, json=reg)
            return httpx.Response(404, json={"code": "NOT_FOUND"})

        m = re.fullmatch(r"/api/peppol/v1/legal-entities/(\d+)", path)
        if request.method == "PUT" and m:
            entity = self.entities.get(int(m.group(1)))
            if entity is None:
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            details = body.get("legalEntityDetails") or {}
            if not details.get("registrationDate"):
                return httpx.Response(
                    400,
                    json={
                        "code": "INVALID_REQUEST",
                        "message": "Legal entity registration date should be provided",
                    },
                )
            entity["legalEntityDetails"] = details
            return httpx.Response(200, json=entity)

        if request.method == "GET" and path == "/api/peppol/v1/documents":
            docs = [*self.documents.values(), *self.received_documents.values()]
            if wanted := params.get("transmissionId"):
                docs = [d for d in docs if d["transmissionId"] == wanted]
            if direction := params.get("direction"):
                docs = [d for d in docs if d["direction"] == direction]
            if start := params.get("startDateTime"):
                # Ademico wants the filter with a zone and writes its own without.
                since = datetime.fromisoformat(start.replace("Z", "+00:00"))
                docs = [
                    d
                    for d in docs
                    if datetime.fromisoformat(d["creationTime"]).replace(tzinfo=UTC) >= since
                ]
            page = int(params.get("page", "0"))
            page_size = int(params.get("pageSize", "100"))
            chunk = docs[page * page_size : (page + 1) * page_size]
            return httpx.Response(
                200,
                json={"documents": chunk, "pagination": {"count": len(docs), "page": page}},
            )

        if path == "/api/peppol/v1/settings/webhooks":
            if request.method == "GET":
                return httpx.Response(200, json=self.webhook)
            if request.method == "PUT":
                self.webhook = body
                return httpx.Response(200, json=self.webhook)
            if request.method == "PATCH":
                self.webhook = {**self.webhook, **body}
                return httpx.Response(200, json=self.webhook)

        if request.method == "GET" and path == "/api/peppol/v1/notifications":
            page = int(request.url.params.get("page", "0"))
            page_size = int(request.url.params.get("pageSize", "100"))
            chunk = self.notifications[page * page_size : (page + 1) * page_size]
            return httpx.Response(
                200,
                json={
                    "notifications": chunk,
                    "pagination": {
                        "count": len(self.notifications),
                        "page": page,
                        "pageSize": page_size,
                    },
                },
            )

        m = re.fullmatch(r"/api/peppol/v1/notifications/(\d+)", path)
        if request.method == "DELETE" and m:
            nid = int(m.group(1))
            if nid in self.fail_consume_once:
                self.fail_consume_once.remove(nid)
                return httpx.Response(503, json={"message": "temporary"})
            if not any(n["notificationId"] == nid for n in self.notifications):
                return httpx.Response(404, json={"code": "NOT_FOUND"})
            self.notifications = [n for n in self.notifications if n["notificationId"] != nid]
            self.consumed_notifications.append(nid)
            return httpx.Response(204)

        m = re.fullmatch(r"/api/peppol/v1/invoices/([^/]+)/ubl", path)
        if request.method == "GET" and m:
            stored_xml = self.ubl_by_tx.get(m.group(1))
            if stored_xml is None:
                return httpx.Response(404)
            return httpx.Response(
                200, content=stored_xml, headers={"content-type": "application/xml"}
            )

        return httpx.Response(500, json={"message": f"unhandled {request.method} {path}"})


class SmpStub:
    """In-memory stand-in for public OpenPeppol BDXL SML DNS + SMP XML."""

    def __init__(self) -> None:
        # orgnr -> set of supported doctype URNs
        self.supported: dict[str, set[str]] = {}
        self.raw_xml_override: bytes | None = None
        self.fail_dns = False

    def register(self, orgnr: str, *, invoice: bool = True, credit_note: bool = True) -> None:
        docs: set[str] = set()
        if invoice:
            docs.add(peppol_smp.INVOICE_DOCTYPE)
        if credit_note:
            docs.add(peppol_smp.CREDIT_NOTE_DOCTYPE)
        self.supported[orgnr] = docs

    async def resolve(self, hostname: str) -> str | None:
        if self.fail_dns:
            raise peppol_smp.SmpLookupError("DNS down")
        for orgnr in self.supported:
            if peppol_smp.bdxl_hostname(orgnr) == hostname:
                return "https://smp.example.no"
        return None

    def handle_http(self, request: httpx.Request) -> httpx.Response:
        if self.raw_xml_override is not None:
            return httpx.Response(200, content=self.raw_xml_override)
        # Path is /iso6523-actorid-upis::0192%3A<orgnr> (httpx decodes %3A on .path).
        tail = unquote(request.url.path).rsplit("0192:", 1)[-1]
        docs = self.supported.get(tail)
        if docs is None:
            return httpx.Response(404)
        refs = "".join(
            f'<ServiceMetadataReference href="https://smp.example.no/iso6523-actorid-upis::'
            f'0192%3A{tail}/services/busdox-docid-qns::{quote(doc, safe="")}"/>'
            for doc in sorted(docs)
        )
        xml = (
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<ServiceGroup xmlns="http://busdox.org/serviceMetadata/publishing/1.0/">'
            f"<ServiceMetadataReferenceCollection>{refs}</ServiceMetadataReferenceCollection>"
            "</ServiceGroup>"
        )
        return httpx.Response(200, content=xml.encode("utf-8"))


@pytest.fixture
def stand_in() -> Iterator[AdemicoStandIn]:
    stub = AdemicoStandIn()
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ADEMICO_USER", USER)
        patch.setenv("ADEMICO_PASSWORD", PASSWORD)
        patch.setenv("ADEMICO_TOKEN", TOKEN)
        patch.setattr(
            ademico,
            "_client",
            lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(stub.handle)),
        )
        get_settings.cache_clear()
        yield stub
    get_settings.cache_clear()


@pytest.fixture
def webhook(stand_in: AdemicoStandIn) -> Iterator[AdemicoStandIn]:
    """The stand-in, with this deployment's webhook secret set."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("ADEMICO_WEBHOOK_SECRET", WEBHOOK_SECRET)
        get_settings.cache_clear()
        yield stand_in
    get_settings.cache_clear()


@pytest.fixture
def smp(monkeypatch: pytest.MonkeyPatch) -> SmpStub:
    stub = SmpStub()
    monkeypatch.setattr(peppol_smp, "_resolve_smp_url", stub.resolve)
    monkeypatch.setattr(
        peppol_smp,
        "_client",
        lambda: httpx.AsyncClient(transport=httpx.MockTransport(stub.handle_http)),
    )
    return stub


@pytest.fixture
def database(database_url: str) -> Iterator[sa.Engine]:
    engine = sa.create_engine(database_url.replace("+aiosqlite", ""))
    yield engine
    engine.dispose()


def confirm(database: sa.Engine, company_id: int) -> None:
    """Stamp Altinn confirmation directly in the database."""
    with database.begin() as connection:
        connection.execute(
            sa.text("UPDATE company SET verified_at = :t WHERE id = :c"),
            {"t": utc_now(), "c": company_id},
        )


def books(
    client: TestClient,
    database: sa.Engine,
    *,
    confirmed: bool = True,
    email: str = "kari@example.no",
    orgnr: str = ORGNR,
    name: str = "Nordmann Consulting AS",
) -> dict[str, Any]:
    session = register(client, email=email)
    company = make_company(
        client, session["memberships"][0]["organization_id"], orgnr=orgnr, name=name
    )
    if confirmed:
        confirm(database, company["id"])
    return company


def issued_invoice(client: TestClient, company_id: int, **overrides: Any) -> dict[str, Any]:
    draft = create_draft(client, company_id, **overrides)
    response = issue(client, company_id, draft["id"])
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def register_peppol(client: TestClient, company_id: int) -> Any:
    return write(client, "POST", f"/api/v1/companies/{company_id}/peppol")


def set_receiving(client: TestClient, company_id: int, *, enabled: bool) -> Any:
    return write(
        client, "PUT", f"/api/v1/companies/{company_id}/peppol/mottak", json={"enabled": enabled}
    )


def send(client: TestClient, company_id: int, document_id: int, **kwargs: Any) -> Any:
    return write(
        client, "POST", f"/api/v1/companies/{company_id}/faktura/{document_id}/send", **kwargs
    )


def ready_to_send(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> dict[str, Any]:
    company = books(client, database)
    assert register_peppol(client, company["id"]).status_code == 200
    return company


def push(
    client: TestClient,
    body: Any,
    *,
    auth: tuple[str, str] | None = (ademico.WEBHOOK_USER, WEBHOOK_SECRET),
) -> httpx.Response:
    """Ademico calling the webhook: no session and no origin, only Basic Auth."""
    caller = TestClient(client.app, base_url=str(client.base_url))
    response: httpx.Response = caller.post(WEBHOOK_URL, json=body, auth=auth)
    return response


def inbox(client: TestClient, company_id: int) -> list[str]:
    received = client.get(f"/api/v1/companies/{company_id}/peppol/innkommende").json()
    return sorted(item["invoice_number"] for item in received)


def sending_status(client: TestClient, company_id: int, invoice_id: int) -> dict[str, Any]:
    """Where Myntos has the send now, without asking Ademico."""
    shown = client.get(f"/api/v1/companies/{company_id}/faktura/{invoice_id}").json()
    sending: dict[str, Any] = shown["ehf_sending"]
    return sending


def asked_for_documents(stand_in: AdemicoStandIn) -> list[dict[str, str]]:
    return [q for method, path, q in stand_in.queries if path == "/api/peppol/v1/documents"]


def downloads(stand_in: AdemicoStandIn) -> list[str]:
    return [path for _, path, _ in stand_in.queries if re.search(r"/invoices/[^/]+/ubl$", path)]


# --- 1. Ademico client wire format -----------------------------------------------------


async def test_the_frozen_xml_goes_as_multipart_with_basic_auth_and_token(
    stand_in: AdemicoStandIn,
) -> None:
    xml = "<Invoice>æøå &amp; nothing rewritten</Invoice>"

    sent = await ademico.send(xml=xml, filename="faktura-1001.xml")

    method, path, payload = stand_in.requests[-1]
    assert (method, path) == ("POST", "/api/peppol/v1/invoices/ubl-submissions")
    assert payload["filename"] == "faktura-1001.xml"
    assert payload["xml"] == xml.encode("utf-8")
    expected_auth = base64.b64encode(f"{USER}:{PASSWORD}".encode()).decode()
    assert stand_in.auth_seen == {f"Basic {expected_auth}"}
    assert stand_in.tokens_seen == {TOKEN}
    assert sent.id == "tx-1"
    assert sent.delivery.status == "pending"


async def test_a_company_is_registered_send_only_and_without_contact_details(
    stand_in: AdemicoStandIn,
) -> None:
    remote = await ademico.create_legal_entity(
        orgnr=ORGNR,
        name="Nordmann Consulting AS",
        address="Kirkegata 2",
        postal_code="0153",
        city="Oslo",
        receiving=False,
    )
    assert remote.orgnr == ORGNR
    assert remote.receiving_enabled is False
    assert remote.peppol_registration_id is not None

    post_req = next(
        r for r in stand_in.requests if r[:2] == ("POST", "/api/peppol/v1/legal-entities")
    )
    body = post_req[2]
    assert body["legalEntityDetails"]["publishInPeppolDirectory"] is False
    assert body["legalEntityDetails"]["countryCode"] == "NO"
    assert "email" not in body["legalEntityDetails"]
    assert "phone" not in body["legalEntityDetails"]
    assert "contacts" not in body["legalEntityDetails"]
    reg = body["peppolRegistrations"][0]
    assert reg["peppolIdentifier"] == {"scheme": "0192", "identifier": ORGNR}
    assert reg["peppolRegistration"] is False
    assert set(reg["supportedDocuments"]) == set(ademico.SUPPORTED_DOCUMENTS)


async def test_the_access_points_refusals_are_told_apart(stand_in: AdemicoStandIn) -> None:
    stand_in.send_error = (
        400,
        {"code": "PARTICIPANT_LOOKUP_ERROR", "message": "Recipient not found in SML"},
    )
    with pytest.raises(ademico.RecipientUnreachableError):
        await ademico.send(xml="<Invoice/>", filename="f.xml")

    stand_in.send_error = (
        400,
        {
            "code": "INVALID_DOCUMENT",
            "message": "Schematron error",
            "validationReport": {
                "validationErrors": [{"ruleId": "BR-CO-15", "message": "totals differ"}]
            },
        },
    )
    with pytest.raises(ademico.AdemicoRefusedError) as refused:
        await ademico.send(xml="<Invoice/>", filename="f.xml")
    assert any("BR-CO-15" in item for item in refused.value.problems)

    stand_in.send_error = (401, {"message": "Unauthorized"})
    with pytest.raises(ademico.AdemicoUnavailableError):
        await ademico.send(xml="<Invoice/>", filename="f.xml")


async def test_a_send_with_no_answer_is_uncertain_not_unavailable(
    monkeypatch: pytest.MonkeyPatch, stand_in: AdemicoStandIn
) -> None:
    def silent(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("no answer", request=request)

    monkeypatch.setattr(
        ademico,
        "_client",
        lambda timeout=None: httpx.AsyncClient(transport=httpx.MockTransport(silent)),
    )
    with pytest.raises(ademico.SendTimedOutError):
        await ademico.send(xml="<Invoice/>", filename="f.xml")
    with pytest.raises(ademico.AdemicoUnavailableError):
        await ademico.find_legal_entity(ORGNR)


async def test_a_blank_credential_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ADEMICO_USER", USER)
    monkeypatch.setenv("ADEMICO_PASSWORD", PASSWORD)
    monkeypatch.setenv("ADEMICO_TOKEN", " ")
    get_settings.cache_clear()
    try:
        assert not ademico.is_configured()
        with pytest.raises(ademico.AdemicoNotConfiguredError):
            await ademico.find_legal_entity(ORGNR)
    finally:
        get_settings.cache_clear()


async def test_delivery_status_mapping(stand_in: AdemicoStandIn) -> None:
    sent = await ademico.send(xml="<Invoice/>", filename="f.xml")
    assert (await ademico.delivery(sent.id)).status == "pending"

    stand_in.set_document_status(sent.id, "SENT")
    assert (await ademico.delivery(sent.id)).status == "delivered"

    stand_in.set_document_status(sent.id, "SEND_FAILED")
    failed = await ademico.delivery(sent.id)
    assert failed == ademico.Delivery(status="failed", failure_category="transport")

    stand_in.set_document_status(sent.id, "REJECTED")
    rejected = await ademico.delivery(sent.id)
    assert rejected == ademico.Delivery(status="failed", failure_category="recipient_rejected")


# --- 2. Public OpenPeppol SML/SMP lookup -----------------------------------------------


def test_bdxl_hostname_matches_openpeppol_spec() -> None:
    host = peppol_smp.bdxl_hostname("974760673")
    expected_hash = (
        base64.b32encode(hashlib.sha256(b"0192:974760673").digest())
        .decode("ascii")
        .rstrip("=")
        .lower()
    )
    assert host == f"{expected_hash}.iso6523-actorid-upis.participant.sml.prod.tech.peppol.org"


def test_naptr_regexp_extraction() -> None:
    assert (
        peppol_smp._extract_smp_url("Meta:SMP", "!.*!https://smp.elma-smp.no/!")
        == "https://smp.elma-smp.no"
    )
    assert peppol_smp._extract_smp_url("Meta:Other", "!.*!https://smp.elma-smp.no/!") is None
    assert peppol_smp._extract_smp_url("Meta:SMP", "!.*!ftp://bad.example/!") is None


async def test_smp_lookup_checks_invoice_and_credit_note_independently(smp: SmpStub) -> None:
    smp.register(CUSTOMER_ORGNR, invoice=True, credit_note=False)

    assert await peppol_smp.can_receive(CUSTOMER_ORGNR, credit_note=False) is True
    assert await peppol_smp.can_receive(CUSTOMER_ORGNR, credit_note=True) is False
    assert await peppol_smp.can_receive("999999999", credit_note=False) is False


async def test_smp_xml_with_dtd_is_refused(smp: SmpStub) -> None:
    smp.register(CUSTOMER_ORGNR)
    smp.raw_xml_override = b'<!DOCTYPE foo [<!ENTITY xxe "bad">]><ServiceGroup/>'
    with pytest.raises(peppol_smp.SmpLookupError):
        await peppol_smp.can_receive(CUSTOMER_ORGNR, credit_note=False)


# --- 3. Incoming UBL parser & PDF ------------------------------------------------------


def test_parse_ehf_invoice_and_credit_note_with_decimals() -> None:
    inv = ubl_reader.parse_ehf(build_incoming_ubl(kind="Invoice"))
    assert inv.document_type is IncomingDocumentType.FAKTURA
    assert inv.invoice_number == "LEV-2026-100"
    assert inv.net_amount == Decimal("1000.00")
    assert inv.vat_amount == Decimal("250.00")
    assert inv.gross_amount == Decimal("1250.00")
    assert inv.seller_orgnr == CUSTOMER_ORGNR
    assert inv.seller_name == "Fjord Leverandør AS"
    assert inv.payment_reference == "0001234567"
    assert inv.seller_bank_account == "15062033158"
    assert inv.has_embedded_pdf is False
    assert len(inv.lines) == 1
    assert inv.lines[0].quantity == Decimal("2")
    assert ubl_reader.suggest_input_tax_code(inv.tax_subtotals_json(), vat_registered=True) == "1"
    assert ubl_reader.suggest_input_tax_code(inv.tax_subtotals_json(), vat_registered=False) == "0"

    cn = ubl_reader.parse_ehf(
        build_incoming_ubl(
            kind="CreditNote",
            invoice_number="KN-9",
            vat_percent="15",
            vat="150.00",
            gross="1150.00",
        )
    )
    assert cn.document_type is IncomingDocumentType.KREDITNOTA
    assert ubl_reader.suggest_input_tax_code(cn.tax_subtotals_json(), vat_registered=True) == "11"


def test_parse_ehf_refuses_dtd_and_unknown_root() -> None:
    with pytest.raises(ubl_reader.InvalidEhfXmlError):
        ubl_reader.parse_ehf(b"<!DOCTYPE x><Invoice/>")
    with pytest.raises(ubl_reader.InvalidEhfXmlError):
        ubl_reader.parse_ehf(b"<Order/>")


def test_incoming_pdf_prefers_valid_embedded_pdf_and_renders_fallback() -> None:
    with_pdf_xml = build_incoming_ubl(embedded_pdf=SAMPLE_PDF)
    parsed = ubl_reader.parse_ehf(with_pdf_xml)
    assert parsed.has_embedded_pdf is True
    doc_with_pdf = PeppolIncomingDocument(
        id=1,
        company_id=1,
        transmission_id="t1",
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
        ehf_xml=with_pdf_xml.decode("utf-8"),
        ehf_sha256="0" * 64,
        has_embedded_pdf=True,
        status=IncomingStatus.NY,
        received_at=datetime.now(UTC),
    )
    pdf_bytes, filename = incoming_pdf.pdf_for(doc_with_pdf)
    assert pdf_bytes == SAMPLE_PDF
    assert filename == "leverandor-faktura.pdf"

    # Non-PDF attachment bytes are ignored and Myntos renders a clean fallback PDF.
    without_pdf_xml = build_incoming_ubl(embedded_pdf=b"not actually a pdf")
    parsed_no = ubl_reader.parse_ehf(without_pdf_xml)
    assert parsed_no.has_embedded_pdf is False
    doc_with_pdf.ehf_xml = without_pdf_xml.decode("utf-8")
    doc_with_pdf.has_embedded_pdf = False
    rendered_bytes, rendered_name = incoming_pdf.pdf_for(doc_with_pdf)
    assert rendered_bytes.startswith(b"%PDF-")
    assert rendered_name.endswith(".pdf")


# --- 4. Registration, receiving opt-in, and sending API routes -------------------------


def test_without_an_access_point_nothing_can_be_sent_or_synced(
    client: TestClient, database: sa.Engine
) -> None:
    company = books(client, database)

    standing = client.get(f"/api/v1/companies/{company['id']}/peppol").json()
    assert standing == {
        "available": False,
        "company_confirmed": True,
        "registered": False,
        "verified": False,
        "can_send": False,
        "can_receive": False,
    }
    for method, url, kwargs in (
        ("POST", f"/api/v1/companies/{company['id']}/peppol", {}),
        ("PUT", f"/api/v1/companies/{company['id']}/peppol/mottak", {"json": {"enabled": True}}),
        ("POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser", {}),
    ):
        refused = write(client, method, url, **kwargs)
        assert refused.status_code == 503, url
        assert refused.json()["code"] == "peppol_not_available"


def test_a_company_not_confirmed_through_altinn_cannot_register_or_receive(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database, confirmed=False)

    for response in (
        register_peppol(client, company["id"]),
        set_receiving(client, company["id"], enabled=True),
    ):
        assert response.status_code == 409
        assert response.json()["code"] == "company_not_confirmed"
    assert stand_in.requests == []


def test_registering_creates_send_only_entity_and_is_immediately_ready_to_send(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)

    registered = register_peppol(client, company["id"])

    assert registered.status_code == 200, registered.text
    assert registered.json() == {
        "available": True,
        "company_confirmed": True,
        "registered": True,
        "verified": True,
        "can_send": True,
        "can_receive": False,
        "verification_url": None,
    }
    created = [r for r in stand_in.requests if r[:2] == ("POST", "/api/peppol/v1/legal-entities")]
    assert len(created) == 1
    assert created[0][2]["peppolRegistrations"][0]["peppolRegistration"] is False


def test_an_existing_company_at_the_access_point_is_adopted(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    stand_in.add_entity(ORGNR, receiving=False)

    body = register_peppol(client, company["id"]).json()

    assert body["registered"] is True and body["can_send"] is True and body["can_receive"] is False
    assert not [r for r in stand_in.requests if r[:2] == ("POST", "/api/peppol/v1/legal-entities")]


def test_only_an_admin_registers_or_toggles_receiving(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    header = agent_header(client, company["id"])
    assert (
        write(
            client, "POST", f"/api/v1/companies/{company['id']}/peppol", headers=header
        ).status_code
        == 403
    )
    assert (
        write(
            client,
            "PUT",
            f"/api/v1/companies/{company['id']}/peppol/mottak",
            json={"enabled": True},
            headers=header,
        ).status_code
        == 403
    )

    join_company(client, company["id"], email="medarbeider@example.no", role="member")
    assert register_peppol(client, company["id"]).status_code == 403
    assert set_receiving(client, company["id"], enabled=True).status_code == 403
    assert client.get(f"/api/v1/companies/{company['id']}/peppol").status_code == 200


def test_enabling_and_disabling_receiving_publishes_and_unpublishes_in_smp(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)

    enabled = set_receiving(client, company["id"], enabled=True)
    assert enabled.status_code == 200, enabled.text
    assert enabled.json()["can_send"] is True
    assert enabled.json()["can_receive"] is True

    entity = next(iter(stand_in.entities.values()))
    assert (
        entity["peppolRegistrations"][0]["peppolRegistrationDetails"]["peppolRegistration"] is True
    )
    assert entity["legalEntityDetails"]["publishInPeppolDirectory"] is True
    preserved_reg_date = entity["legalEntityDetails"]["registrationDate"]
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}", preserved_reg_date)

    # Simulate partial remote state where ELMA registration already succeeded but
    # Peppol Directory publishing had failed earlier: retrying still syncs both
    # and preserves the existing registrationDate verbatim.
    entity["legalEntityDetails"]["publishInPeppolDirectory"] = False
    entity["legalEntityDetails"]["registrationDate"] = "2024-05-01T00:00:00.000"
    retried = set_receiving(client, company["id"], enabled=True)
    assert retried.status_code == 200, retried.text
    assert entity["legalEntityDetails"]["publishInPeppolDirectory"] is True
    assert entity["legalEntityDetails"]["registrationDate"] == "2024-05-01T00:00:00.000"

    disabled = set_receiving(client, company["id"], enabled=False)
    assert disabled.status_code == 200, disabled.text
    assert disabled.json()["can_send"] is True
    assert disabled.json()["can_receive"] is False
    assert (
        entity["peppolRegistrations"][0]["peppolRegistrationDetails"]["peppolRegistration"] is False
    )
    assert entity["legalEntityDetails"]["publishInPeppolDirectory"] is False
    assert entity["legalEntityDetails"]["registrationDate"] == "2024-05-01T00:00:00.000"


def test_receiving_conflict_in_elma_returns_409_and_keeps_receiving_off(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    stand_in.elma_conflicts.add(ORGNR)

    refused = set_receiving(client, company["id"], enabled=True)
    assert refused.status_code == 409
    assert refused.json()["code"] == "receiving_conflict"

    standing = client.get(f"/api/v1/companies/{company['id']}/peppol").json()
    assert standing["can_receive"] is False


def test_two_companies_with_same_orgnr_cannot_both_claim_receiving(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    first = books(client, database, email="eier1@example.no", orgnr=ORGNR)
    assert set_receiving(client, first["id"], enabled=True).status_code == 200

    second_client = TestClient(client.app, base_url=str(client.base_url))
    second = books(second_client, database, email="eier2@example.no", orgnr=ORGNR)

    # Sending is still allowed on the second confirmed company, but receiving is refused.
    assert register_peppol(second_client, second["id"]).status_code == 200
    refused = set_receiving(second_client, second["id"], enabled=True)
    assert refused.status_code == 409
    assert refused.json()["code"] == "receiving_already_claimed"

    # Once the first company switches receiving off, the second can claim it.
    assert set_receiving(client, first["id"], enabled=False).status_code == 200
    claimed = set_receiving(second_client, second["id"], enabled=True)
    assert claimed.status_code == 200
    assert claimed.json()["can_receive"] is True


def test_whether_the_customer_can_receive_uses_public_smp(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn, smp: SmpStub
) -> None:
    company = books(client, database)
    smp.register(CUSTOMER_ORGNR, invoice=True, credit_note=False)
    url = f"/api/v1/companies/{company['id']}/peppol/mottaker"

    on = client.get(url, params={"orgnr": CUSTOMER_ORGNR})
    assert on.status_code == 200, on.text
    assert on.json() == {"orgnr": CUSTOMER_ORGNR, "can_receive": True}

    cn = client.get(url, params={"orgnr": CUSTOMER_ORGNR, "kreditnota": "true"})
    assert cn.status_code == 200
    assert cn.json() == {"orgnr": CUSTOMER_ORGNR, "can_receive": False}

    assert client.get(url, params={"orgnr": "12345"}).status_code == 422

    smp.fail_dns = True
    assert client.get(url, params={"orgnr": CUSTOMER_ORGNR}).status_code == 502


def test_without_an_access_point_nobody_is_looked_up(
    client: TestClient, database: sa.Engine
) -> None:
    company = books(client, database)
    refused = client.get(
        f"/api/v1/companies/{company['id']}/peppol/mottaker", params={"orgnr": CUSTOMER_ORGNR}
    )
    assert refused.status_code == 503
    assert refused.json()["code"] == "peppol_not_available"


def test_an_issued_invoice_goes_once_and_its_delivery_is_followed(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    frozen = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/ehf").content

    sent = send(client, company["id"], invoice["id"])
    assert sent.status_code == 201, sent.text
    assert sent.json()["status"] == "pending"
    assert sent.json()["recipient"] == f"0192:{CUSTOMER_ORGNR}"
    assert stand_in.requests[-1][2]["xml"] == frozen

    again = send(client, company["id"], invoice["id"])
    assert again.status_code == 409
    assert again.json()["code"] == "already_sent"

    stand_in.set_document_status("tx-1", "SENT")
    followed = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/forsendelse")
    assert followed.json()["status"] == "delivered"
    assert followed.json()["status_checked_at"] is not None
    shown = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}").json()
    assert shown["ehf_sending"]["status"] == "delivered"


def test_a_failed_send_may_be_tried_again(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    assert send(client, company["id"], invoice["id"]).status_code == 201

    stand_in.set_document_status("tx-1", "SEND_FAILED")
    followed = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/forsendelse")
    assert followed.json()["status"] == "failed"
    assert followed.json()["failure_category"] == "transport"

    assert send(client, company["id"], invoice["id"]).status_code == 201


def test_a_customer_not_on_peppol_is_told_and_nothing_is_kept(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    stand_in.send_error = (400, {"code": "PARTICIPANT_LOOKUP_ERROR", "message": "not found"})

    refused = send(client, company["id"], invoice["id"])
    assert refused.status_code == 409
    assert refused.json()["code"] == "recipient_not_on_peppol"
    assert "PDF" in refused.json()["detail"]

    never = client.get(f"/api/v1/companies/{company['id']}/faktura/{invoice['id']}/forsendelse")
    assert never.status_code == 404
    assert never.json()["code"] == "transmission_not_found"


def test_a_refused_ehf_says_what_was_found(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    stand_in.send_error = (
        400,
        {
            "code": "INVALID_DOCUMENT",
            "validationReport": {
                "validationErrors": [{"ruleId": "BR-CO-15", "message": "totals differ"}]
            },
        },
    )

    refused = send(client, company["id"], invoice["id"])
    assert refused.status_code == 502
    assert refused.json()["code"] == "access_point_refused"
    assert "BR-CO-15" in refused.json()["detail"]


def test_business_invoice_requires_ehf_setup_before_issuing_while_consumer_is_allowed(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    """With Peppol configured, the first B2B invoice waits for EHF setup; B2C does not."""
    company = books(client, database)
    business_draft = create_draft(client, company["id"])
    assert any(
        v["field"] == "peppol_registration" and v["severity"] == "blokkerende"
        for v in business_draft["violations"]
    )
    checked = client.get(
        f"/api/v1/companies/{company['id']}/faktura/{business_draft['id']}/kontroll"
    ).json()
    assert any(v["field"] == "peppol_registration" for v in checked)

    refused = issue(client, company["id"], business_draft["id"])
    assert refused.status_code == 422
    assert refused.json()["code"] == "invoice_not_compliant"

    # Private consumer invoices remain allowed without EHF setup.
    consumer = issued_invoice(client, company["id"], buyer_orgnr=None, buyer_is_business=False)
    assert consumer["status"] == "utstedt"

    # Once EHF sending is activated, the business draft has no blocking findings and issues cleanly.
    assert register_peppol(client, company["id"]).status_code == 200
    after = client.get(
        f"/api/v1/companies/{company['id']}/faktura/{business_draft['id']}/kontroll"
    ).json()
    assert [v for v in after if v["severity"] == "blokkerende"] == []
    issued = issue(client, company["id"], business_draft["id"])
    assert issued.status_code == 201, issued.text


def test_unregistered_draft_and_consumer_invoice_cannot_go(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])

    # Simulate an invoice issued before a registration existed (defense in depth on /send).
    with database.begin() as connection:
        connection.execute(
            sa.text("DELETE FROM peppol_registration WHERE company_id = :c"),
            {"c": company["id"]},
        )

    unregistered = send(client, company["id"], invoice["id"])
    assert unregistered.status_code == 409
    assert unregistered.json()["code"] == "not_registered"

    assert register_peppol(client, company["id"]).status_code == 200
    draft = create_draft(client, company["id"])
    consumer = issued_invoice(client, company["id"], buyer_orgnr=None, buyer_is_business=False)
    for document_id in (draft["id"], consumer["id"]):
        refused = send(client, company["id"], document_id)
        assert refused.status_code == 409
        assert refused.json()["code"] == "not_sendable"


def test_a_viewer_cannot_send(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    join_company(client, company["id"], email="revisor@example.no", role="viewer")
    assert send(client, company["id"], invoice["id"]).status_code == 403


def test_a_keys_send_waits_for_its_person(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, stand_in)
    invoice = issued_invoice(client, company["id"])
    header = agent_header(client, company["id"])

    held = send(client, company["id"], invoice["id"], headers=header)
    assert held.status_code == 202, held.text
    assert held.json()["code"] == "approval_pending"
    assert not [r for r in stand_in.requests if r[1].endswith("/ubl-submissions")]
    seen = client.get(held.headers["Location"], headers=header).json()
    assert seen["label"] == "sende fakturaen som EHF til kunden"


# --- 5. The webhook, the sweep behind it, and incoming EHF routes ----------------------


def test_one_sweep_routes_two_companies_and_updates_delivery(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    """One sweep serves every company and never leaks counts across tenants."""
    company_a = books(client, database, email="a@example.no", orgnr=ORGNR, name="Selskap A AS")
    assert set_receiving(client, company_a["id"], enabled=True).status_code == 200
    outgoing = issued_invoice(client, company_a["id"])
    assert send(client, company_a["id"], outgoing["id"]).status_code == 201

    client_b = TestClient(client.app, base_url=str(client.base_url))
    company_b = books(
        client_b, database, email="b@example.no", orgnr=SECOND_ORGNR, name="Selskap B AS"
    )
    assert set_receiving(client_b, company_b["id"], enabled=True).status_code == 200

    stand_in.queue_received(
        receiver_orgnr=ORGNR,
        raw_xml=build_incoming_ubl(invoice_number="A-INV-1", seller_name="Leverandør A AS"),
    )
    stand_in.queue_received(
        receiver_orgnr=SECOND_ORGNR,
        document_type="CREDIT_NOTE",
        raw_xml=build_incoming_ubl(
            kind="CreditNote", invoice_number="B-CN-1", seller_name="Leverandør B AS"
        ),
    )
    stand_in.queue_sending_event(transmission_id="tx-1", event_type="DOCUMENT_SENT")

    # Company A triggers a sync: only Company A's own counts come back, though
    # the sweep stored Company B's credit note and consumed all three events.
    synced = write(client, "POST", f"/api/v1/companies/{company_a['id']}/peppol/synkroniser")
    assert synced.status_code == 200, synced.text
    assert synced.json() == {"received": 1, "deliveries_updated": 1}
    assert len(stand_in.consumed_notifications) == 3
    assert stand_in.notifications == []

    # Outgoing transmission for A was marked delivered by the sweep.
    out_status = client.get(f"/api/v1/companies/{company_a['id']}/faktura/{outgoing['id']}").json()
    assert out_status["ehf_sending"]["status"] == "delivered"

    # Each company sees only its own received document.
    inbox_a = client.get(f"/api/v1/companies/{company_a['id']}/peppol/innkommende").json()
    assert [(i["invoice_number"], i["document_type"]) for i in inbox_a] == [("A-INV-1", "faktura")]

    inbox_b = client_b.get(f"/api/v1/companies/{company_b['id']}/peppol/innkommende").json()
    assert [(i["invoice_number"], i["document_type"]) for i in inbox_b] == [
        ("B-CN-1", "kreditnota")
    ]


def test_the_sweep_is_idempotent_when_consume_fails_after_commit(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    nid, _ = stand_in.queue_received(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl())
    stand_in.fail_consume_once.add(nid)

    first = write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser")
    assert first.status_code == 200
    assert first.json() == {"received": 1, "deliveries_updated": 0}
    assert len(stand_in.notifications) == 1  # delete failed once

    second = write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser")
    assert second.status_code == 200
    assert second.json() == {"received": 0, "deliveries_updated": 0}
    assert stand_in.notifications == []

    assert len(inbox(client, company["id"])) == 1


def test_the_webhook_is_closed_until_a_secret_is_set(
    client: TestClient, stand_in: AdemicoStandIn
) -> None:
    event = stand_in.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl())

    assert push(client, event).status_code == 503
    assert stand_in.queries == []


def test_the_webhook_opens_only_to_the_credentials_myntos_gave_ademico(
    client: TestClient, webhook: AdemicoStandIn
) -> None:
    event = webhook.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl())
    caller = TestClient(client.app, base_url=str(client.base_url))

    for auth in (
        None,
        (ademico.WEBHOOK_USER, "wrong"),
        (ademico.WEBHOOK_USER, WEBHOOK_SECRET + "x"),
        ("someone", WEBHOOK_SECRET),
    ):
        assert push(client, event, auth=auth).status_code == 401, auth
    for header in ("Basic not-base64!", f"Bearer {WEBHOOK_SECRET}", "Basic " + "x" * 3):
        refused = caller.post(WEBHOOK_URL, json=event, headers={"Authorization": header})
        assert refused.status_code == 401, header
    assert webhook.queries == []


def test_a_pushed_invoice_is_filed_where_ademico_says_not_where_the_push_says(
    client: TestClient, database: sa.Engine, webhook: AdemicoStandIn
) -> None:
    company_a = books(client, database, email="a@example.no", orgnr=ORGNR, name="Selskap A AS")
    assert set_receiving(client, company_a["id"], enabled=True).status_code == 200
    client_b = TestClient(client.app, base_url=str(client.base_url))
    company_b = books(
        client_b, database, email="b@example.no", orgnr=SECOND_ORGNR, name="Selskap B AS"
    )
    assert set_receiving(client_b, company_b["id"], enabled=True).status_code == 200
    event = webhook.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="A-1"))

    # Anyone holding the secret could write the receiver; only Ademico's own
    # record of the document decides whose inbox it goes in.
    forged = {**event, "receiver": f"0192:{SECOND_ORGNR}", "notificationId": None}
    assert push(client, forged).status_code == 200

    assert inbox(client, company_a["id"]) == ["A-1"]
    assert inbox(client_b, company_b["id"]) == []
    assert asked_for_documents(webhook) == [
        {"transmissionId": event["transmissionId"], "direction": "RECEIVING", "pageSize": "5"}
    ]


def test_a_push_about_nothing_ademico_holds_changes_nothing(
    client: TestClient, database: sa.Engine, webhook: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200

    # A delivery for a send Myntos never made is not even looked up.
    elsewhere = webhook.sending_event(transmission_id="tx-elsewhere")
    assert push(client, elsewhere).status_code == 200
    assert asked_for_documents(webhook) == []

    # A received document Ademico does not list is neither downloaded nor
    # stored. Ademico is asked to push it again, in case its list was behind.
    ghost = {
        "eventType": "DOCUMENT_RECEIVED",
        "transmissionId": "in-tx-ghost",
        "peppolDocumentType": "INVOICE",
        "receiver": f"0192:{ORGNR}",
    }
    assert push(client, ghost).status_code == 503
    assert downloads(webhook) == []
    assert inbox(client, company["id"]) == []


def test_a_pushed_delivery_is_read_back_before_it_is_believed(
    client: TestClient, database: sa.Engine, webhook: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, webhook)
    first = issued_invoice(client, company["id"])
    second = issued_invoice(client, company["id"])
    assert send(client, company["id"], first["id"]).status_code == 201
    assert send(client, company["id"], second["id"]).status_code == 201

    # Pushed before Ademico's own record says so: asked for again, not settled,
    # so the sweep does not later report it as a webhook that failed.
    early = {"eventType": "DOCUMENT_SENT", "transmissionId": "tx-1", "documentStatus": "SENT"}
    assert push(client, early).status_code == 503
    assert sending_status(client, company["id"], first["id"])["status"] == "pending"

    # A push claiming failure loses to Ademico's record of a delivery.
    webhook.set_document_status("tx-1", "SENT")
    forged = {"eventType": "DOCUMENT_SEND_FAILED", "transmissionId": "tx-1"}
    assert push(client, forged).status_code == 200
    assert sending_status(client, company["id"], first["id"])["status"] == "delivered"

    # A real failure keeps the reason the push gave, for the seller to read.
    failed = webhook.sending_event(
        transmission_id="tx-2",
        event_type="DOCUMENT_SEND_FAILED",
        document_status="SEND_FAILED",
        details=[{"code": "AP_DOWN", "message": "Mottakers tilgangspunkt svarte ikke"}],
    )
    assert push(client, failed).status_code == 200
    shown = sending_status(client, company["id"], second["id"])
    assert shown["status"] == "failed"
    assert shown["failure_category"] == "transport"
    assert shown["failure_message"] == "AP_DOWN: Mottakers tilgangspunkt svarte ikke"


def test_a_bulk_push_and_a_repeated_one_store_each_invoice_once(
    client: TestClient, database: sa.Engine, webhook: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    webhook.queue_received(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="B-1"))
    webhook.queue_received(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="B-2"))
    # Whether Ademico also keeps a pushed event in the pull queue is not
    # documented; here it does, so handling the push consumes it there.
    first, second = list(webhook.notifications)

    assert push(client, {"notifications": [first, second]}).status_code == 200
    assert push(client, [first]).status_code == 200
    assert push(client, first).status_code == 200

    assert inbox(client, company["id"]) == ["B-1", "B-2"]
    assert len(downloads(webhook)) == 2
    assert webhook.notifications == []
    synced = write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser")
    assert synced.json() == {"received": 0, "deliveries_updated": 0}


def test_what_ademico_cannot_confirm_yet_is_pushed_again(
    client: TestClient, database: sa.Engine, webhook: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    event = webhook.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="R-1"))
    listed = webhook.received_documents.pop(event["transmissionId"])

    # Pushed before Ademico lists the document, then while Ademico is down.
    assert push(client, event).status_code == 503
    webhook.received_documents[event["transmissionId"]] = listed
    webhook.down = True
    assert push(client, event).status_code == 503
    webhook.down = False
    assert inbox(client, company["id"]) == []

    assert push(client, event).status_code == 200
    assert inbox(client, company["id"]) == ["R-1"]


def test_a_push_myntos_cannot_read_is_reported_without_its_contents(
    client: TestClient, webhook: AdemicoStandIn, caplog: pytest.LogCaptureFixture
) -> None:
    caller = TestClient(client.app, base_url=str(client.base_url))
    auth = (ademico.WEBHOOK_USER, WEBHOOK_SECRET)

    with caplog.at_level(logging.ERROR, logger="myntos.api.v1.peppol"):
        unknown = push(client, {"receiverId": SECOND_ORGNR, "kind": "mystery"})
        not_json = caller.post(WEBHOOK_URL, content=b"<xml/>", auth=auth)

    # 200: a push that cannot be read will not become readable by repetition,
    # and the sweep finds whatever it was about.
    assert unknown.status_code == 200
    assert not_json.status_code == 200
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 2
    assert "keys=kind,receiverId" in errors[0]
    assert SECOND_ORGNR not in caplog.text
    assert webhook.queries == []


def test_the_sweep_finds_what_no_push_brought(
    client: TestClient, database: sa.Engine, webhook: AdemicoStandIn
) -> None:
    company = ready_to_send(client, database, webhook)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    invoice = issued_invoice(client, company["id"])
    assert send(client, company["id"], invoice["id"]).status_code == 201
    # Sent a while ago and delivered since; neither push ever arrived.
    with database.begin() as connection:
        connection.execute(
            sa.text("UPDATE peppol_transmission SET sent_at = :t"),
            {"t": utc_now() - timedelta(minutes=10)},
        )
    webhook.set_document_status("tx-1", "SENT")
    webhook.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="S-1"))
    # Older than the sweep looks back: missed by more than a webhook.
    old = webhook.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="S-0"))
    webhook.received_documents[old["transmissionId"]]["creationTime"] = ademico_time(
        utc_now() - timedelta(days=8)
    )

    synced = write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser")

    assert synced.json() == {"received": 1, "deliveries_updated": 1}
    assert inbox(client, company["id"]) == ["S-1"]
    assert sending_status(client, company["id"], invoice["id"])["status"] == "delivered"
    listed = next(q for q in asked_for_documents(webhook) if "startDateTime" in q)
    assert listed["direction"] == "RECEIVING"
    assert listed["startDateTime"].endswith("Z")
    again = write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser")
    assert again.json() == {"received": 0, "deliveries_updated": 0}


def test_the_background_sweep_raises_the_alarm_once_a_webhook_is_expected(
    client: TestClient,
    database: sa.Engine,
    stand_in: AdemicoStandIn,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    sessions = client.app.dependency_overrides[get_import_sessions]()  # type: ignore[attr-defined]
    # On the app's own loop: the async engine's connections belong to it.
    portal = client.portal
    assert portal is not None

    # No webhook set up: finding things is the sweep's job, and not an error.
    stand_in.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="W-1"))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="myntos.integrations.peppol_sync"):
        portal.call(peppol_sync.sweep_and_report, sessions)
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    # With one, everything the sweep finds is a push that did not arrive.
    monkeypatch.setenv("ADEMICO_WEBHOOK_SECRET", WEBHOOK_SECRET)
    get_settings.cache_clear()
    stand_in.receive(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="W-2"))
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="myntos.integrations.peppol_sync"):
        portal.call(peppol_sync.sweep_and_report, sessions)
    assert [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR] == [
        "Peppol sweep found 1 received documents and 0 delivery updates that no webhook brought"
    ]
    assert inbox(client, company["id"]) == ["W-1", "W-2"]


def test_the_webhook_is_switched_on_one_event_at_a_time_and_its_secret_never_shown(
    webhook: AdemicoStandIn,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level(logging.DEBUG):
        assert peppol_sync.main(["on"]) == 0

    assert webhook.webhook == {
        "active": True,
        "bulk": False,
        "webhookUrl": "https://api.myntos.no/api/v1/webhooks/ademico",
        "authentication": {
            "type": "BASIC_AUTH",
            "user": ademico.WEBHOOK_USER,
            "password": WEBHOOK_SECRET,
        },
        "additionalHeaders": {},
    }
    shown = capsys.readouterr().out
    assert "BASIC_AUTH" in shown
    assert WEBHOOK_SECRET not in shown
    assert WEBHOOK_SECRET not in caplog.text

    assert peppol_sync.main(["off"]) == 0
    assert webhook.webhook["active"] is False
    assert webhook.webhook["webhookUrl"] == "https://api.myntos.no/api/v1/webhooks/ademico"


def test_without_a_secret_the_webhook_is_not_switched_on(
    stand_in: AdemicoStandIn, capsys: pytest.CaptureFixture[str]
) -> None:
    assert peppol_sync.main(["on"]) == 1
    assert "ADEMICO_WEBHOOK_SECRET" in capsys.readouterr().out
    assert stand_in.queries == []


def test_incoming_detail_xml_and_pdf_downloads(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    raw = build_incoming_ubl(invoice_number="DET-42", embedded_pdf=SAMPLE_PDF)
    stand_in.queue_received(receiver_orgnr=ORGNR, raw_xml=raw)
    assert (
        write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser").status_code
        == 200
    )

    summary = client.get(f"/api/v1/companies/{company['id']}/peppol/innkommende").json()[0]
    assert summary["suggested_tax_code"] == "1"
    assert summary["has_embedded_pdf"] is True

    detail = client.get(
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{summary['id']}"
    ).json()
    assert detail["ehf_sha256"] == hashlib.sha256(raw).hexdigest()
    assert detail["buyer_reference"] == "AVD-IT"
    assert detail["order_reference"] == "PO-42"
    assert len(detail["lines"]) == 1
    assert len(detail["tax_subtotals"]) == 1

    xml_resp = client.get(
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{summary['id']}/ehf"
    )
    assert xml_resp.status_code == 200
    assert xml_resp.content == raw

    pdf_resp = client.get(
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{summary['id']}/pdf"
    )
    assert pdf_resp.status_code == 200
    assert pdf_resp.content == SAMPLE_PDF


def test_booking_incoming_invoice_posts_voucher_attaches_pdf_and_creates_supplier(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    stand_in.queue_received(
        receiver_orgnr=ORGNR,
        raw_xml=build_incoming_ubl(invoice_number="BOK-7", gross="1250.00"),
    )
    assert (
        write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser").status_code
        == 200
    )
    incoming_id = client.get(f"/api/v1/companies/{company['id']}/peppol/innkommende").json()[0][
        "id"
    ]

    booked = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "datakostnad", "tax_code": "1"},
    )
    assert booked.status_code == 201, booked.text
    body = booked.json()
    assert body["status"] == "bokfort"
    assert body["voucher_id"] is not None
    assert body["booked_at"] is not None

    # Supplier was created automatically from the EHF.
    parties = client.get(f"/api/v1/companies/{company['id']}/parties").json()
    suppliers = [p for p in parties if p["party_type"] == "leverandor"]
    assert len(suppliers) == 1
    assert suppliers[0]["orgnr"] == CUSTOMER_ORGNR

    # Voucher exists in the ledger with a rendered PDF attached in the bilag archive.
    vouchers = client.get(f"/api/v1/companies/{company['id']}/cashflow").json()
    voucher = next(v for v in vouchers if v["id"] == body["voucher_id"])
    assert voucher["gross_amount"] == "1250.00"
    assert len(voucher["attachments"]) == 1
    pdf_dl = client.get(
        f"/api/v1/companies/{company['id']}/dokument/{voucher['attachments'][0]['id']}"
    )
    assert pdf_dl.status_code == 200
    assert pdf_dl.content.startswith(b"%PDF-")

    # Cannot be booked twice.
    again = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "datakostnad"},
    )
    assert again.status_code == 409
    assert again.json()["code"] == "incoming_invoice_already_booked"


def test_booking_foreign_currency_incoming_requires_nok_amount(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    stand_in.queue_received(
        receiver_orgnr=ORGNR,
        raw_xml=build_incoming_ubl(
            invoice_number="EUR-1",
            currency="EUR",
            net="100.00",
            vat="0.00",
            gross="100.00",
            vat_percent="0",
            vat_category="E",
        ),
    )
    assert (
        write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser").status_code
        == 200
    )
    incoming_id = client.get(f"/api/v1/companies/{company['id']}/peppol/innkommende").json()[0][
        "id"
    ]

    missing_nok = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "datakostnad", "tax_code": "0"},
    )
    assert missing_nok.status_code == 422
    assert missing_nok.json()["code"] == "incoming_invoice_invalid"

    with_nok = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "datakostnad", "tax_code": "0", "gross_amount": "1150.00"},
    )
    assert with_nok.status_code == 201, with_nok.text


def test_dismiss_and_reopen_incoming_invoice(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    stand_in.queue_received(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="D-1"))
    assert (
        write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser").status_code
        == 200
    )
    incoming_id = client.get(f"/api/v1/companies/{company['id']}/peppol/innkommende").json()[0][
        "id"
    ]

    dismissed = write(
        client, "POST", f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/avvis"
    )
    assert dismissed.status_code == 200
    assert dismissed.json()["status"] == "avvist"

    # Cannot book while dismissed.
    refused = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "varekjop"},
    )
    assert refused.status_code == 409
    assert refused.json()["code"] == "incoming_invoice_dismissed"

    # Reopen returns to ny and allows booking.
    reopened = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/gjenapne",
    )
    assert reopened.status_code == 200
    assert reopened.json()["status"] == "ny"

    booked = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "varekjop"},
    )
    assert booked.status_code == 201

    # Booked invoice cannot be dismissed.
    cannot_dismiss = write(
        client, "POST", f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/avvis"
    )
    assert cannot_dismiss.status_code == 409
    assert cannot_dismiss.json()["code"] == "incoming_invoice_already_booked"


def test_agent_key_permissions_and_approval_on_incoming_ehf(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    stand_in.queue_received(receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="AG-1"))

    read_header = agent_header(client, company["id"], scopes=("read",))
    synced = client.post(
        f"/api/v1/companies/{company['id']}/peppol/synkroniser", headers=read_header
    )
    assert synced.status_code == 200
    incoming_id = client.get(
        f"/api/v1/companies/{company['id']}/peppol/innkommende", headers=read_header
    ).json()[0]["id"]
    assert (
        client.get(
            f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/pdf",
            headers=read_header,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/avvis",
            headers=read_header,
        ).status_code
        == 403
    )

    approval_header = agent_header(
        client, company["id"], scopes=("read", "write"), approval_scopes=("post",)
    )
    held = client.post(
        f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}/bokfor",
        json={"type_code": "datakostnad", "tax_code": "1"},
        headers=approval_header,
    )
    assert held.status_code == 202, held.text
    approval_id = held.json()["approval_id"]

    # Approval detail includes the incoming invoice's readable target description.
    detail = client.get(f"/api/v1/companies/{company['id']}/godkjenninger/{approval_id}").json()
    assert detail["label"] == "bokføre en innkommende e-faktura"
    assert any("AG-1" in str(v) for v in detail.values())

    approved = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/godkjenninger/{approval_id}/godkjenn",
        json={"files": []},
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "done"
    assert approved.json()["result_status"] == 201

    after = client.get(f"/api/v1/companies/{company['id']}/peppol/innkommende/{incoming_id}").json()
    assert after["status"] == "bokfort"
    assert after["voucher_id"] is not None


def test_closing_company_clears_receiving_and_keeps_incoming_books(
    client: TestClient, database: sa.Engine, stand_in: AdemicoStandIn
) -> None:
    company = books(client, database)
    assert set_receiving(client, company["id"], enabled=True).status_code == 200
    stand_in.queue_received(
        receiver_orgnr=ORGNR, raw_xml=build_incoming_ubl(invoice_number="RET-1")
    )
    assert (
        write(client, "POST", f"/api/v1/companies/{company['id']}/peppol/synkroniser").status_code
        == 200
    )

    closed = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/sletting",
        json={"password": USER_PASSWORD, "company_name": company["name"]},
    )
    assert closed.status_code == 200, closed.text
    # Having an incoming EHF counts as bookkeeping material (retained, not erased).
    assert closed.json()["outcome"] == "kept"

    # The orgnr is freed so a newly opened company can claim receiving.
    second_client = TestClient(client.app, base_url=str(client.base_url))
    reopened_company = books(second_client, database, email="ny@example.no", orgnr=ORGNR)
    claimed = set_receiving(second_client, reopened_company["id"], enabled=True)
    assert claimed.status_code == 200, claimed.text
    assert claimed.json()["can_receive"] is True
