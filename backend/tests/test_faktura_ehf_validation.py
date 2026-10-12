"""The EHF output against the rule sets that will actually judge it.

``test_faktura_ehf`` checks the shape of the XML by reading it back, which
catches what we already knew to look for. This file runs the generated documents
through the three validators a receiving Peppol access point runs, from the
artefacts their owners publish, vendored under ``tests/fixtures/ehf`` (see the
README there for versions, sources and licences):

1. the **OASIS UBL 2.1 XSD** — element names, order and data types;
2. the **CEN EN 16931 Schematron** — the semantic model: totals, VAT breakdown,
   which category needs which reason;
3. the **Peppol BIS Billing 3.0 Schematron** — Peppol's restrictions on top,
   including the Norwegian national rules (NO-R-*).

Golden fixtures in the sense the SAF-T plan uses the word: the official
artefacts, unmodified, plus the official example documents as a control. If the
examples ever stop validating, the validator is broken, not the generator — and
a validator that passes everything is worse than none, so a deliberately broken
document has to fail as well.

The documents under test come from the **real code path** — company, draft,
issue, kreditnota over HTTP, and the frozen ``ehf_xml`` read back from the
download endpoint — so a fix that only made a hand-built fixture pass would not
show up as a pass here.

Saxon-HE (``saxonche``) runs the Schematron because both rule sets are XSLT 2.0
and lxml only speaks XSLT 1.0. It is a dev dependency only: nothing in the
running application validates its own output against these, because a document
that fails here is a generator bug to fix, not a runtime condition to handle.
"""

from __future__ import annotations

import functools
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from lxml import etree
from saxonche import PySaxonProcessor

from myntos.domain.faktura import compliance
from myntos.domain.faktura.compliance import E_INVOICE_MANDATORY_FROM
from tests.conftest import register, write
from tests.test_faktura_api import create_draft, issue, make_company, signed_in_company
from tests.test_faktura_domain import make_document

FIXTURES = Path(__file__).parent / "fixtures" / "ehf"
UBL_XSD = FIXTURES / "ubl-2.1" / "xsd" / "maindoc"
CEN_XSLT = FIXTURES / "cen-en16931-1.3.16" / "EN16931-UBL-validation.xslt"
PEPPOL_SCH = FIXTURES / "peppol-bis-billing-3.0.21" / "PEPPOL-EN16931-UBL.sch"
PEPPOL_EXAMPLES = FIXTURES / "peppol-bis-billing-3.0.21" / "examples"
SCHXSLT = FIXTURES / "schxslt-1.10.1" / "xslt" / "2.0" / "pipeline-for-svrl.xsl"

SVRL = "http://purl.oclc.org/dsdl/svrl"

# Our own output and vendored upstream files only, but a parser that fetches
# DTDs or expands entities has no business near either.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


@dataclass(frozen=True)
class Finding:
    """One rule a document broke, and who said so."""

    # "xsd", "cen" or "peppol".
    source: str
    # The rule id as the rule set names it (BR-CO-15, PEPPOL-EN16931-R001), or
    # "XSD" for a schema error.
    rule: str
    # "fatal" or "warning". The Schematrons use exactly these two flags; an XSD
    # error is always fatal, because the access point rejects the document.
    flag: str
    text: str

    @property
    def fatal(self) -> bool:
        # Anything not explicitly a warning counts as fatal. A rule set that one
        # day starts using a third flag ("error") should fail loudly here rather
        # than be waved through as advice.
        return self.flag != "warning"


class Validator:
    """The three validators, compiled once for the whole session."""

    def __init__(self) -> None:
        self._saxon = PySaxonProcessor(license=False)
        xslt = self._saxon.new_xslt30_processor()

        # CEN publishes its rules pre-compiled to XSLT; that file is used as is.
        self._cen = xslt.compile_stylesheet(stylesheet_file=str(CEN_XSLT))

        # Peppol publishes only the .sch — its own tooling compiles it at run
        # time — so it is compiled here, with SchXslt, from the unmodified
        # upstream file. Vendoring a generated XSLT instead would put a file in
        # the tree that no upstream checksum can vouch for.
        compiler = xslt.compile_stylesheet(stylesheet_file=str(SCHXSLT))
        peppol_xslt = compiler.transform_to_string(source_file=str(PEPPOL_SCH))
        self._peppol = xslt.compile_stylesheet(stylesheet_text=peppol_xslt)

        self._xsd = {
            "Invoice": etree.XMLSchema(etree.parse(str(UBL_XSD / "UBL-Invoice-2.1.xsd"), _PARSER)),
            "CreditNote": etree.XMLSchema(
                etree.parse(str(UBL_XSD / "UBL-CreditNote-2.1.xsd"), _PARSER)
            ),
        }

    def validate(self, xml: str) -> list[Finding]:
        """Every finding from all three validators, XSD first."""
        root = etree.fromstring(xml.encode(), _PARSER)
        local = etree.QName(root).localname
        schema = self._xsd[local]
        findings = []
        if not schema.validate(root):
            findings += [
                Finding("xsd", "XSD", "fatal", f"line {error.line}: {error.message}")
                for error in schema.error_log
            ]
        node = self._saxon.parse_xml(xml_text=xml)
        for source, executable in (("cen", self._cen), ("peppol", self._peppol)):
            report = executable.transform_to_string(xdm_node=node)
            findings += _findings(source, report)
        return findings


def _findings(source: str, report: str) -> list[Finding]:
    """The failed assertions and successful reports in an SVRL document.

    Both count. ISO Schematron lets a rule be written either way round — assert
    something must hold, or report when something does — and an SVRL reader
    that only looked at ``failed-assert`` would silently skip every rule written
    as a report.
    """
    tree = etree.fromstring(report.encode(), _PARSER)
    found = []
    for element in tree.iter(f"{{{SVRL}}}failed-assert", f"{{{SVRL}}}successful-report"):
        text = " ".join("".join(element.itertext()).split())
        found.append(Finding(source, element.get("id") or "?", element.get("flag") or "", text))
    return found


@functools.cache
def validator() -> Validator:
    return Validator()


def fatal(findings: list[Finding]) -> list[Finding]:
    return [finding for finding in findings if finding.fatal]


def rules(findings: list[Finding]) -> set[str]:
    return {finding.rule for finding in findings}


def ehf_of(client: TestClient, company_id: int, document_id: int) -> str:
    response = client.get(f"/api/v1/companies/{company_id}/faktura/{document_id}/ehf")
    assert response.status_code == 200, response.text
    text: str = response.text
    return text


def issued_ehf(client: TestClient, company: dict[str, Any], **draft: Any) -> tuple[int, str]:
    """Draft, issue and download one invoice; the id and the frozen XML."""
    created = create_draft(client, company["id"], **draft)
    response = issue(client, company["id"], created["id"])
    assert response.status_code == 201, response.text
    document_id: int = response.json()["id"]
    return document_id, ehf_of(client, company["id"], document_id)


def line(description: str, tax_code: str, unit_price: str, **extra: Any) -> dict[str, Any]:
    return {
        "description": description,
        "quantity": extra.pop("quantity", "1"),
        "unit_price": unit_price,
        "tax_code": tax_code,
        **extra,
    }


# --- The control: the official examples must pass -------------------------------------


EXAMPLES = sorted(PEPPOL_EXAMPLES.glob("*.xml"))


def test_the_official_examples_are_all_there() -> None:
    """A glob that matched nothing would make the control below vacuous."""
    assert len(EXAMPLES) >= 10


@pytest.mark.parametrize("example", EXAMPLES, ids=[path.name for path in EXAMPLES])
def test_the_official_peppol_examples_validate(example: Path) -> None:
    """If these fail, the validator is wrong — not the generator."""
    findings = validator().validate(example.read_text(encoding="utf-8"))
    assert fatal(findings) == []


def test_a_broken_document_is_caught_by_every_layer() -> None:
    """The other half of the control: the validators can say no.

    One defect per layer, each the kind this generator could plausibly make.
    """
    base = (PEPPOL_EXAMPLES / "base-example.xml").read_text(encoding="utf-8")

    # A payable amount that does not follow from the totals: EN 16931.
    wrong_total = base.replace(
        '<cbc:PayableAmount currencyID="EUR">', '<cbc:PayableAmount currencyID="EUR">1', 1
    )
    assert "BR-CO-16" in rules(fatal(validator().validate(wrong_total)))

    # No buyer endpoint: Peppol, which routes on it.
    start = base.index("<cbc:EndpointID", base.index("<cac:AccountingCustomerParty>"))
    end = base.index("</cbc:EndpointID>", start) + len("</cbc:EndpointID>")
    unroutable = base[:start] + base[end:]
    assert "PEPPOL-EN16931-R010" in rules(fatal(validator().validate(unroutable)))

    # An element out of sequence: the XSD.
    swapped = base.replace("<cbc:IssueDate>", "<cbc:Foo/><cbc:IssueDate>", 1)
    assert "XSD" in rules(fatal(validator().validate(swapped)))


# --- What Myntos actually produces --------------------------------------------------------


# Warnings the generator is allowed to produce, each for a reason that is written
# down in docs/integrations/efaktura.md. Anything else is either a fix or a new
# line here and there — never a silent pass.
#
# NO-R-002 asks every Norwegian seller for "Foretaksregisteret", but § 5-1-2
# requires it only of the forms that are registered there (AS, ASA, NUF). An
# enkeltpersonforetak outside the register that wrote it would be stating
# something untrue on its own invoice.
ACCEPTED_WARNINGS = frozenset({"NO-R-002"})


def describe(findings: list[Finding]) -> str:
    return "\n".join(f"{f.source} {f.flag} {f.rule}: {f.text}" for f in findings)


def assert_valid(xml: str) -> list[Finding]:
    """No fatal finding from any layer, and no warning nobody has accounted for."""
    findings = validator().validate(xml)
    assert fatal(findings) == [], describe(fatal(findings))
    unexplained = [f for f in findings if f.rule not in ACCEPTED_WARNINGS]
    assert unexplained == [], describe(unexplained)
    return findings


def test_an_ordinary_invoice_validates(client: TestClient) -> None:
    company = signed_in_company(client)
    _, xml = issued_ehf(client, company)
    assert_valid(xml)


def test_every_rate_and_zero_category_on_one_invoice_validates(client: TestClient) -> None:
    """§ 5-1-5's separate groups are EN 16931's TaxSubtotals; all of them at once."""
    company = signed_in_company(client)
    _, xml = issued_ehf(
        client,
        company,
        lines=[
            line("Rådgivning", "3", "1000.00"),
            line("Matvarer", "31", "333.33", quantity="3"),
            line("Persontransport", "33", "100.00", discount_percent="15"),
            line("Aviser", "5", "50.00"),
            line("Eksport", "52", "200.00"),
            line("Utleie av fast eiendom", "6", "1500.00"),
        ],
    )
    assert_valid(xml)


def test_awkward_arithmetic_still_adds_up_for_the_validator(client: TestClient) -> None:
    """Fractional hours, a four-decimal rate and a discount on the same line.

    The validators recompute every line (PEPPOL-EN16931-R120) and every group
    (BR-CO-17) from what the document says, with a rounding tolerance of their
    own — so this is where a disagreement between our rounding and theirs would
    surface.
    """
    company = signed_in_company(client)
    _, xml = issued_ehf(
        client,
        company,
        lines=[
            line(
                "Konsulenttimer",
                "3",
                "1234.5625",
                quantity="7.5",
                unit_code="HUR",
                discount_percent="12.5",
            ),
            line("Småvarer", "3", "0.10"),
            line("Småvarer", "3", "0.10"),
            line("Småvarer", "3", "0.10"),
            line("Kaffe", "31", "19.99", quantity="0.333"),
        ],
    )
    assert_valid(xml)


def test_a_zero_rated_invoice_validates(client: TestClient) -> None:
    company = signed_in_company(client)
    _, xml = issued_ehf(client, company, lines=[line("Aviser", "5", "450.00")])
    assert_valid(xml)


def test_a_reverse_charge_invoice_validates(client: TestClient) -> None:
    company = signed_in_company(client)
    _, xml = issued_ehf(client, company, reverse_charge=True)
    assert_valid(xml)


def test_a_kreditnota_referencing_its_invoice_validates(client: TestClient) -> None:
    company = signed_in_company(client)
    invoice_id, _ = issued_ehf(
        client,
        company,
        lines=[line("Rådgivning", "3", "1000.00"), line("Matvarer", "31", "200.00")],
    )
    response = write(
        client,
        "POST",
        f"/api/v1/companies/{company['id']}/faktura/{invoice_id}/kreditnota",
        json={},
    )
    assert response.status_code == 201, response.text

    xml = ehf_of(client, company["id"], response.json()["id"])
    assert "<cac:BillingReference>" in xml
    assert_valid(xml)


def test_payment_means_with_a_norwegian_account_and_kid_validates(client: TestClient) -> None:
    company = signed_in_company(client)
    _, xml = issued_ehf(client, company)

    assert "<cbc:PaymentID>" in xml
    assert "<cac:PayeeFinancialAccount>" in xml
    assert_valid(xml)


def test_payment_means_without_a_kid_validates(client: TestClient) -> None:
    company = signed_in_company(client)
    _, xml = issued_ehf(client, company, payment_reference_auto=False)

    assert "<cbc:PaymentID>" not in xml
    assert_valid(xml)


def test_a_seller_outside_the_vat_register_validates(client: TestClient) -> None:
    session = register(client)
    company = make_company(
        client, session["memberships"][0]["organization_id"], vat_registered=False
    )
    _, xml = issued_ehf(client, company, lines=[line("Rådgivning", "0", "5000.00")])

    assert "MVA</cbc:CompanyID>" not in xml
    assert_valid(xml)


def test_the_foretaksregisteret_marking_validates(client: TestClient) -> None:
    session = register(client)
    company = make_company(
        client,
        session["memberships"][0]["organization_id"],
        organisation_form="AS",
        in_foretaksregisteret=True,
    )
    _, xml = issued_ehf(client, company)

    assert "Foretaksregisteret" in xml
    # Not even the accepted warning: NO-R-002 is exactly what this seller satisfies.
    assert validator().validate(xml) == []


def test_a_buyer_without_an_orgnr_is_unroutable_and_stays_refused(client: TestClient) -> None:
    """A business buyer with no Norwegian orgnr — a foreign one, typically.

    There is no Peppol address to put in ``EndpointID``, so the document fails
    PEPPOL-EN16931-R010 and nothing in the generator can honestly fix that:
    inventing an identifier would route the invoice to somebody else. What keeps
    it from being sent is the compliance check, which warns until the
    e-invoice requirement starts and refuses from then. This pins both halves,
    so the refusal cannot be loosened by making the XML look valid.
    """
    company = signed_in_company(client)
    _, xml = issued_ehf(client, company, buyer_orgnr=None)

    assert rules(fatal(validator().validate(xml))) == {"PEPPOL-EN16931-R010"}

    document = make_document(buyer_orgnr=None, buyer_is_business=True)
    refused = compliance.blocking(compliance.check(document, today=E_INVOICE_MANDATORY_FROM))
    assert [v.field for v in refused] == ["buyer_orgnr"]
    before = compliance.blocking(compliance.check(document, today=date(2026, 8, 16)))
    assert before == []
