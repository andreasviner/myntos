"""Official XSD validation and domain computation tests for RR-0002 årsregnskap.

Validates every generated Hovedskjema (RR-0002) and Underskjema (RR-0002-U)
document against ALL FOUR vendored Brønnøysundregistrene XSD schemas:

- ``RR-0002_1266-51820.xsd`` (live Hovedskjema)
- ``RR-0002_1266-51820_januar_2027.xsd`` (Jan 2027 Hovedskjema)
- ``RR-0002-U_758-51980.xsd`` (live Underskjema)
- ``RR-0002-U_758-51980_januar_2027.xsd`` (Jan 2027 Underskjema)

Also includes negative control assertions proving that malformed XML is
rejected by the validators (so a silent pass is impossible).
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from myntos.aarsregnskap.compute import (
    DraftInput,
    FixedAssetNoteSummary,
    LedgerAccountBalance,
    ShareholderSummary,
    compute_aarsregnskap,
)
from myntos.aarsregnskap.pdf import (
    render_aarsregnskap_pdf,
    render_generalforsamling_pdf,
    render_styreprotokoll_pdf,
)
from myntos.aarsregnskap.xmlwriter import render_hovedskjema, render_underskjema
from myntos.domain.ledger.models import AccountType, Company

FIXTURES = Path(__file__).parent / "fixtures" / "aarsregnskap"

HOVED_LIVE = etree.XMLSchema(etree.parse(str(FIXTURES / "RR-0002_1266-51820.xsd")))
HOVED_2027 = etree.XMLSchema(etree.parse(str(FIXTURES / "RR-0002_1266-51820_januar_2027.xsd")))
UNDER_LIVE = etree.XMLSchema(etree.parse(str(FIXTURES / "RR-0002-U_758-51980.xsd")))
UNDER_2027 = etree.XMLSchema(etree.parse(str(FIXTURES / "RR-0002-U_758-51980_januar_2027.xsd")))


def _company(*, form: str = "AS", name: str = "Nordmann Teknologi AS") -> Company:
    return Company(
        organization_id=1,
        orgnr="915933149",
        name=name,
        organisation_form=form,
    )


def _validate_both(hoved_xml: str, under_xml: str) -> None:
    hoved_doc = etree.fromstring(hoved_xml.encode("utf-8"))
    under_doc = etree.fromstring(under_xml.encode("utf-8"))
    HOVED_LIVE.assertValid(hoved_doc)
    HOVED_2027.assertValid(hoved_doc)
    UNDER_LIVE.assertValid(under_doc)
    UNDER_2027.assertValid(under_doc)


def test_profitable_as_with_prior_year_fixed_assets_and_notes_passes_all_xsds() -> None:
    company = _company()
    movements = [
        LedgerAccountBalance("3000", "Salgsinntekt", AccountType.INNTEKT, Decimal("-1200000.00")),
        LedgerAccountBalance("4000", "Varekjøp", AccountType.KOSTNAD, Decimal("200000.00")),
        LedgerAccountBalance("5000", "Lønn", AccountType.KOSTNAD, Decimal("450000.00")),
        LedgerAccountBalance("5400", "AGA", AccountType.KOSTNAD, Decimal("63450.00")),
        LedgerAccountBalance("6010", "Avskrivning", AccountType.KOSTNAD, Decimal("25000.00")),
        LedgerAccountBalance("6300", "Husleie", AccountType.KOSTNAD, Decimal("120000.00")),
        LedgerAccountBalance("8050", "Renteinntekt", AccountType.INNTEKT, Decimal("-5000.00")),
        LedgerAccountBalance("8150", "Rentekostnad", AccountType.KOSTNAD, Decimal("1550.00")),
        LedgerAccountBalance("8300", "Betalbar skatt", AccountType.KOSTNAD, Decimal("75900.00")),
        LedgerAccountBalance("8960", "Overført EK", AccountType.KOSTNAD, Decimal("269100.00")),
    ]
    cumulative = [
        LedgerAccountBalance("1250", "Inventar", AccountType.EIENDEL, Decimal("100000.00")),
        LedgerAccountBalance("1500", "Kundefordringer", AccountType.EIENDEL, Decimal("150000.00")),
        LedgerAccountBalance("1920", "Bankinnskudd", AccountType.EIENDEL, Decimal("300000.00")),
        LedgerAccountBalance("1950", "Skattetrekkskonto", AccountType.EIENDEL, Decimal("25000.00")),
        LedgerAccountBalance("2000", "Aksjekapital", AccountType.EGENKAPITAL, Decimal("-30000.00")),
        LedgerAccountBalance("2050", "Annen EK", AccountType.EGENKAPITAL, Decimal("-369100.00")),
        LedgerAccountBalance("2220", "Banklån", AccountType.GJELD, Decimal("-50000.00")),
        LedgerAccountBalance("2400", "Leverandørgjeld", AccountType.GJELD, Decimal("-30000.00")),
        LedgerAccountBalance("2500", "Betalbar skatt", AccountType.GJELD, Decimal("-75900.00")),
        LedgerAccountBalance("2600", "Forskuddstrekk", AccountType.GJELD, Decimal("-20000.00")),
    ]
    prior_movements = [
        LedgerAccountBalance("3000", "Salgsinntekt", AccountType.INNTEKT, Decimal("-900000.00")),
        LedgerAccountBalance("6300", "Husleie", AccountType.KOSTNAD, Decimal("700000.00")),
        LedgerAccountBalance("8300", "Skatt", AccountType.KOSTNAD, Decimal("44000.00")),
        LedgerAccountBalance("8960", "Overført EK", AccountType.KOSTNAD, Decimal("156000.00")),
    ]
    prior_cumulative = [
        LedgerAccountBalance("1920", "Bank", AccountType.EIENDEL, Decimal("180000.00")),
        LedgerAccountBalance("2000", "AK", AccountType.EGENKAPITAL, Decimal("-30000.00")),
        LedgerAccountBalance("2050", "EK", AccountType.EGENKAPITAL, Decimal("-100000.00")),
        LedgerAccountBalance("2400", "Lev", AccountType.GJELD, Decimal("-50000.00")),
    ]
    draft = DraftInput(
        small_company=True,
        auditor_elected_out=True,
        approval_date=date(2026, 5, 15),
        confirming_representative="Kari Nordmann",
        contact_email="kari@nordmann.no",
        board_meeting_date=date(2026, 4, 20),
        board_place="Oslo",
        board_members=(
            {"name": "Kari Nordmann", "role": "Styreleder"},
            {"name": "Ola Hansen", "role": "Styremedlem"},
        ),
        general_meeting_date=date(2026, 5, 15),
        general_meeting_place="Oslo",
        general_meeting_chair="Kari Nordmann",
        general_meeting_cosigner="Ola Hansen",
        notes={
            "antall_aarsverk": "1.5",
            "har_otp_plikt": True,
            "gjeld_over_fem_aar": 20000,
            "pantstillelser_gjeld": 50000,
            "pantsatte_eiendeler_verdi": 100000,
            "garantiforpliktelser": 15000,
            "laan_til_styret": 10000,
            "laan_rentesats_styret": "4.50",
            "laan_vilkaar_styret": "Nedbetales over 2 år",
            "egendefinerte_noter": [
                {"tittel": "Hendelser etter balansedagen", "tekst": "Ingen vesentlige hendelser."}
            ],
        },
    )
    computed = compute_aarsregnskap(
        company=company,
        year=2025,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        year_closed=True,
        current_movements=movements,
        current_cumulative=cumulative,
        prior_movements=prior_movements,
        prior_cumulative=prior_cumulative,
        draft=draft,
        shareholders=[
            ShareholderSummary("Kari Nordmann", 70, Decimal("70.00")),
            ShareholderSummary("Ola Hansen", 30, Decimal("30.00")),
        ],
        share_count=100,
        par_value=Decimal("300.00"),
        fixed_assets=FixedAssetNoteSummary(
            tangible_opening_cost=125000,
            tangible_closing_cost=125000,
            tangible_year_depreciation=25000,
            tangible_accumulated_depreciation=25000,
            tangible_carrying_value=100000,
        ),
        today=date(2026, 6, 1),
    )

    assert not computed.blocked, computed.findings
    assert computed.driftsinntekter == 1_200_000
    assert computed.driftsresultat == 341_550
    assert computed.resultat_for_skatt == 345_000
    assert computed.aarsresultat == 269_100
    assert computed.sum_eiendeler == 575_000
    assert computed.sum_egenkapital == 399_100
    assert computed.sum_gjeld == 175_900
    assert computed.sum_egenkapital_og_gjeld == 575_000
    assert computed.balance_diff == 0
    assert computed.notes.bundne_bankinnskudd == 25_000

    hoved_xml = render_hovedskjema(company, computed)
    under_xml = render_underskjema(computed)
    _validate_both(hoved_xml, under_xml)

    # All three PDF documents render cleanly with valid PDF header and EOF.
    for pdf_bytes in (
        render_aarsregnskap_pdf(company, computed),
        render_styreprotokoll_pdf(company, computed),
        render_generalforsamling_pdf(company, computed),
    ):
        assert pdf_bytes.startswith(b"%PDF-1.4\n")
        assert pdf_bytes.endswith(b"%%EOF\n")


def test_loss_with_udekket_tap_and_going_concern_uncertainty_matches_brreg_formula() -> None:
    """Verify Brreg ORID 249 (udekketTap) is positive and subtracted in ORID 9702."""
    company = _company()
    movements = [
        LedgerAccountBalance("3000", "Salgsinntekt", AccountType.INNTEKT, Decimal("-50000.00")),
        LedgerAccountBalance("6300", "Husleie", AccountType.KOSTNAD, Decimal("120000.00")),
        LedgerAccountBalance("8990", "Udekket tap", AccountType.KOSTNAD, Decimal("-70000.00")),
    ]
    cumulative = [
        LedgerAccountBalance("1920", "Bank", AccountType.EIENDEL, Decimal("10000.00")),
        LedgerAccountBalance("2000", "Aksjekapital", AccountType.EGENKAPITAL, Decimal("-30000.00")),
        LedgerAccountBalance("2080", "Udekket tap", AccountType.EGENKAPITAL, Decimal("70000.00")),
        LedgerAccountBalance("2400", "Leverandørgjeld", AccountType.GJELD, Decimal("-50000.00")),
    ]
    draft = DraftInput(
        approval_date=date(2026, 4, 10),
        confirming_representative="Kari Nordmann",
        winding_up_accounts=True,
        prior_year_overrides={
            "salgsinntekt": 40000,
            "annen_driftskostnad": 40000,
            "bankinnskudd_kontanter": 30000,
            "selskapskapital": 30000,
        },
        notes={
            "antall_aarsverk": "0",
            "usikkerhet_fortsatt_drift": True,
            "usikkerhet_fortsatt_drift_tekst": "Egenkapitalen er tapt og selskapet avvikles.",
        },
    )
    computed = compute_aarsregnskap(
        company=company,
        year=2025,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        year_closed=True,
        current_movements=movements,
        current_cumulative=cumulative,
        prior_movements=None,
        prior_cumulative=None,
        draft=draft,
        today=date(2026, 5, 1),
    )

    assert not computed.blocked
    assert computed.aarsresultat == -70_000
    assert computed.current_values["udekket_tap"] == 70_000
    assert computed.sum_opptjent_egenkapital == -70_000
    assert computed.sum_egenkapital == -40_000
    assert computed.sum_egenkapital_og_gjeld == 10_000
    assert computed.sum_eiendeler == 10_000
    assert computed.uses_prior_year_overrides is True
    assert computed.prior_values["selskapskapital"] == 30_000
    assert any(f.code == "negativ_egenkapital" for f in computed.findings)

    hoved_xml = render_hovedskjema(company, computed)
    under_xml = render_underskjema(computed)
    _validate_both(hoved_xml, under_xml)


def test_net_vat_receivable_moves_to_short_term_receivables_preserving_balance() -> None:
    company = _company()
    movements = [
        LedgerAccountBalance("6300", "Kostnad", AccountType.KOSTNAD, Decimal("10000.00")),
        LedgerAccountBalance("8990", "Tap", AccountType.KOSTNAD, Decimal("-10000.00")),
    ]
    # Net debit on 2710 (2500 kr VAT refund owed by Skatteetaten)
    cumulative = [
        LedgerAccountBalance("1920", "Bank", AccountType.EIENDEL, Decimal("17500.00")),
        LedgerAccountBalance("2710", "Inngående mva", AccountType.EIENDEL, Decimal("2500.00")),
        LedgerAccountBalance("2000", "Aksjekapital", AccountType.EGENKAPITAL, Decimal("-30000.00")),
        LedgerAccountBalance("2080", "Udekket tap", AccountType.EGENKAPITAL, Decimal("10000.00")),
    ]
    computed = compute_aarsregnskap(
        company=company,
        year=2025,
        start_date=date(2025, 1, 1),
        end_date=date(2025, 12, 31),
        year_closed=True,
        current_movements=movements,
        current_cumulative=cumulative,
        prior_movements=None,
        prior_cumulative=None,
        draft=DraftInput(approval_date=date(2026, 4, 1), confirming_representative="Kari Nordmann"),
        today=date(2026, 5, 1),
    )
    assert computed.current_values["andre_kortsiktige_fordringer"] == 2_500
    assert computed.current_values["skyldige_offentlige_avgifter"] == 0
    assert computed.sum_eiendeler == 20_000
    assert computed.sum_egenkapital_og_gjeld == 20_000
    _validate_both(render_hovedskjema(company, computed), render_underskjema(computed))


def test_xsd_control_rejects_broken_xml() -> None:
    """Guard against a broken test harness that never actually validates."""
    bad_hoved = etree.fromstring(
        b'<melding xmlns="http://schema.brreg.no/regnsys/aarsregnskap_vanlig"><bad/></melding>'
    )
    bad_under = etree.fromstring(
        b'<melding xmlns="http://schema.brreg.no/regnsys/aarsregnskap_vanlig/underskjema"><bad/></melding>'
    )
    with pytest.raises(etree.DocumentInvalid):
        HOVED_LIVE.assertValid(bad_hoved)
    with pytest.raises(etree.DocumentInvalid):
        UNDER_LIVE.assertValid(bad_under)
