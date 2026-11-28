"""Official XSD validation and domain calculation tests for RF-1086 XML.

Every generated Hovedskjema (RF-1086) and Underskjema (RF-1086-U) is validated
against Skatteetaten's published XSD schemas in ``tests/fixtures/aksjonaer/``
using ``lxml.etree.XMLSchema``.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from lxml import etree

from myntos.aksjonaer import codes, compute, xmlwriter
from myntos.aksjonaer.errors import InvalidAksjonaerDataError
from myntos.domain.arsoppgjor.findings import Severity

FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures" / "aksjonaer"
HOVED_XSD = etree.XMLSchema(
    etree.parse(str(FIXTURES_DIR / "aksjonaerregisteroppgaveHovedskjema.xsd"))
)
UNDER_XSD = etree.XMLSchema(
    etree.parse(str(FIXTURES_DIR / "aksjonaerregisteroppgaveUnderskjema.xsd"))
)

# Official Skatteetaten Tenor synthetic fødselsnumre (mod-11 valid, non-real persons).
TENOR_FNR_1 = "26829398612"
TENOR_FNR_2 = "08866995401"
TENOR_FNR_3 = "05849898878"
VALID_ORGNR_COMPANY = "910000004"
VALID_ORGNR_HOLDER = "910000012"


def _assert_hoved_valid(xml_str: str) -> etree._Element:
    doc = etree.fromstring(xml_str.encode("utf-8"))
    HOVED_XSD.assertValid(doc)
    return doc


def _assert_under_valid(xml_str: str) -> etree._Element:
    doc = etree.fromstring(xml_str.encode("utf-8"))
    UNDER_XSD.assertValid(doc)
    return doc


def _company(**kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": 1,
        "orgnr": VALID_ORGNR_COMPANY,
        "name": "Nordlys Teknologi AS",
        "organisation_form": "AS",
        "address_line": "Storgata 12",
        "postal_code": "0155",
        "city": "Oslo",
        "email": "post@nordlys.example",
        "phone": "+47 900 12 345",
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _holder(hid: int, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": hid,
        "kind": "person",
        "name": f"Aksjonær {hid}",
        "national_id": TENOR_FNR_1,
        "orgnr": None,
        "foreign_id": None,
        "address": "Storgata 1",
        "postal_code": "0155",
        "city": "Oslo",
        "country_code": "NO",
        "email": None,
        "is_own_shares": False,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _share_class(sc_id: int, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": sc_id,
        "code": "01",
        "name": "Ordinære aksjer",
        "isin": None,
        "opening_year": 2025,
        "opening_shares": 1000,
        "opening_par_value": Decimal("30"),
        "opening_share_capital": Decimal("30000.00"),
        "opening_paid_in_capital": Decimal("30000.00"),
        "opening_paid_in_premium": Decimal("0.00"),
        "opening_holdings": [],
        "events": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _opening(shareholder_id: int, shares: int) -> Any:
    return SimpleNamespace(shareholder_id=shareholder_id, shares=shares)


def _line(shareholder_id: int, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "shareholder_id": shareholder_id,
        "role": "mottaker",
        "shares": 0,
        "amount": None,
        "paid_in_premium": None,
        "withholding_tax": None,
        "withholding_tax_rate": None,
        "withholding_tax_country": None,
        "counterparty_national_id": None,
        "counterparty_orgnr": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


def _event(eid: int, event_type: str, event_date: date, **kwargs: Any) -> Any:
    defaults: dict[str, Any] = {
        "id": eid,
        "event_type": event_type,
        "subtype": None,
        "event_date": event_date,
        "description": None,
        "total_shares": None,
        "par_value_after": None,
        "total_amount": None,
        "premium_amount": None,
        "amount_per_share": None,
        "lines": [],
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# --- Control test: validator actually rejects invalid XML -------------------------


def test_xsd_validator_rejects_broken_hovedskjema_and_underskjema() -> None:
    with pytest.raises(etree.DocumentInvalid):
        _assert_hoved_valid("<Skjema/>")
    with pytest.raises(etree.DocumentInvalid):
        _assert_under_valid("<Skjema/>")


# --- Identifier & code validators -------------------------------------------------


def test_national_id_validation_and_masking() -> None:
    assert codes.validate_national_id(TENOR_FNR_1) == TENOR_FNR_1
    assert codes.validate_national_id(f"{TENOR_FNR_2[:6]} {TENOR_FNR_2[6:]}") == TENOR_FNR_2
    assert codes.mask_national_id(TENOR_FNR_1) == "268293 *****"
    assert codes.mask_national_id(None) is None

    with pytest.raises(InvalidAksjonaerDataError):
        codes.validate_national_id("26829398613")  # bad k2
    with pytest.raises(InvalidAksjonaerDataError):
        codes.validate_national_id("26829398622")  # bad k1
    with pytest.raises(InvalidAksjonaerDataError):
        codes.validate_national_id("1234567890")  # wrong length


def test_foreign_id_and_share_class_code_validators() -> None:
    assert codes.validate_foreign_id("utl123456789") == "UTL123456789"
    with pytest.raises(InvalidAksjonaerDataError):
        codes.validate_foreign_id("UTL12345")
    assert codes.validate_share_class_code("01") == "01"
    with pytest.raises(InvalidAksjonaerDataError):
        codes.validate_share_class_code("99")


# --- Full XSD tests across corporate & shareholder event types --------------------


def test_unchanged_as_with_person_and_corporate_shareholders_validates_xsd() -> None:
    comp = _company()
    h1 = _holder(1, name="Kari Nordmann", national_id=TENOR_FNR_1)
    h2 = _holder(
        2,
        kind="selskap",
        name="Holding Invest AS",
        national_id=None,
        orgnr=VALID_ORGNR_HOLDER,
        address="Fjordveien 4\nBygg B",
    )
    sc = _share_class(
        1,
        opening_holdings=[_opening(1, 600), _opening(2, 400)],
    )

    computed = compute.compute_year(
        company=comp,
        year=2026,
        share_classes=[sc],
        shareholders=[h1, h2],
        contact_email="post@nordlys.example",
        ledger_share_capital=Decimal("30000.00"),
    )
    assert not computed.blocked
    assert not computed.findings
    assert len(computed.classes) == 1
    assert [h.ownership_percent for h in computed.classes[0].shareholders] == [
        Decimal("60.00"),
        Decimal("40.00"),
    ]

    built = xmlwriter.build_filing_xml(
        computed.classes[0],
        comp,
        xmlwriter.ContactInfo(
            email="post@nordlys.example",
            name="Kari Nordmann",
            role="Daglig leder",
            phone="+47 900 12 345",
        ),
    )
    hoved_doc = _assert_hoved_valid(built.hovedskjema_xml)
    assert hoved_doc.findtext(".//Aksjekapital-datadef-87") == "30000"
    assert len(built.underskjemaer) == 2
    for _, _, under_xml in built.underskjemaer:
        _assert_under_valid(under_xml)


def test_stiftelse_and_nyemisjon_with_overkurs_validate_xsd() -> None:
    comp = _company()
    h1 = _holder(1, name="Stifter En", national_id=TENOR_FNR_1)
    h2 = _holder(2, name="Investor To", national_id=TENOR_FNR_2)
    sc = _share_class(
        1,
        opening_year=None,
        opening_shares=0,
        opening_par_value=Decimal("0"),
        opening_share_capital=Decimal("0.00"),
        opening_paid_in_capital=Decimal("0.00"),
        opening_paid_in_premium=Decimal("0.00"),
        events=[
            _event(
                1,
                "stiftelse",
                date(2026, 2, 1),
                subtype="N",
                total_shares=3000,
                par_value_after=Decimal("10"),
                total_amount=Decimal("30000.00"),
                premium_amount=Decimal("0.00"),
                lines=[_line(1, shares=3000, amount=Decimal("30000.00"))],
            ),
            _event(
                2,
                "nyemisjon",
                date(2026, 6, 15),
                subtype="N",
                total_shares=1000,
                par_value_after=Decimal("10"),
                total_amount=Decimal("10000.00"),
                premium_amount=Decimal("90000.00"),
                lines=[
                    _line(
                        2,
                        shares=1000,
                        amount=Decimal("100000.00"),
                        paid_in_premium=Decimal("90000.00"),
                    )
                ],
            ),
        ],
    )
    computed = compute.compute_year(
        company=comp,
        year=2026,
        share_classes=[sc],
        shareholders=[h1, h2],
        contact_email="post@nordlys.example",
    )
    assert not computed.blocked
    c0 = computed.classes[0]
    assert c0.opening.shares == 0
    assert c0.closing.shares == 4000
    assert c0.closing.share_capital == Decimal("40000.00")
    assert c0.closing.paid_in_capital == Decimal("40000.00")
    assert c0.closing.paid_in_premium == Decimal("90000.00")

    built = xmlwriter.build_filing_xml(c0, comp)
    hoved = _assert_hoved_valid(built.hovedskjema_xml)
    assert hoved.findtext(".//AksjeOverkursISINAksjetype-datadef-17661") == "90000"
    assert hoved.findtext(".//AksjerNyutstedteStiftelseMvOverkurs-datadef-23948") == "90"
    for _, _, uxml in built.underskjemaer:
        _assert_under_valid(uxml)


def test_overdragelse_utbytte_and_foreign_withholding_tax_validate_xsd() -> None:
    comp = _company()
    h1 = _holder(1, name="Selger En", national_id=TENOR_FNR_1)
    h2 = _holder(
        2,
        kind="utenlandsk",
        name="Svensk Investor AB",
        national_id=None,
        foreign_id="UTL123456789",
        country_code="SE",
        postal_code="11122",
        city="Stockholm",
    )
    h3 = _holder(
        3,
        kind="utenlandsk",
        name="Nordic Fund Ltd",
        national_id=None,
        foreign_id=None,
        country_code="GB",
        postal_code="EC1A 1BB",
        city="London",
    )
    sc = _share_class(
        1,
        opening_shares=1000,
        opening_par_value=Decimal("30"),
        opening_share_capital=Decimal("30000.00"),
        opening_paid_in_capital=Decimal("30000.00"),
        opening_holdings=[_opening(1, 700), _opening(2, 200), _opening(3, 100)],
        events=[
            _event(
                1,
                "overdragelse",
                date(2026, 3, 10),
                subtype="salg",
                lines=[
                    _line(1, role="avgiver", shares=200, amount=Decimal("50000.00")),
                    _line(2, role="mottaker", shares=200, amount=Decimal("50000.00")),
                ],
            ),
            _event(
                2,
                "utbytte",
                date(2026, 5, 20),
                subtype="Y",
                total_amount=Decimal("100000.00"),
                amount_per_share=Decimal("100"),
                lines=[
                    _line(1, shares=500, amount=Decimal("50000.00")),
                    _line(
                        2,
                        shares=400,
                        amount=Decimal("40000.00"),
                        withholding_tax=Decimal("6000.00"),
                        withholding_tax_rate=15,
                        withholding_tax_country="SE",
                    ),
                    _line(3, shares=100, amount=Decimal("10000.00")),
                ],
            ),
        ],
    )
    computed = compute.compute_year(
        company=comp,
        year=2026,
        share_classes=[sc],
        shareholders=[h1, h2, h3],
        contact_email="post@nordlys.example",
    )
    assert not computed.blocked
    built = xmlwriter.build_filing_xml(computed.classes[0], comp)
    _assert_hoved_valid(built.hovedskjema_xml)
    assert len(built.underskjemaer) == 3
    for hid, _, uxml in built.underskjemaer:
        doc = _assert_under_valid(uxml)
        if hid == 2:
            assert doc.findtext(".//AksjonarUtenlandskIdenifikasjonsnummer-datadef-26626") == (
                "UTL123456789"
            )
            assert doc.findtext(".//Kildeskatt-datadef-17743") == "6000"
            assert doc.findtext(".//KildeskattProsent-datadef-2280") == "15"


def test_continuity_transfers_validate_and_catch_missing_counterparty() -> None:
    comp = _company()
    h1 = _holder(1, name="Person En", national_id=TENOR_FNR_1)
    h2 = _holder(2, name="Person To", national_id=TENOR_FNR_2)
    # Missing counterparty fnr on skilsmisse (ES) blocks submission.
    sc_bad = _share_class(
        1,
        opening_holdings=[_opening(1, 1000)],
        events=[
            _event(
                1,
                "overdragelse",
                date(2026, 4, 1),
                subtype="skilsmisse",
                lines=[
                    _line(1, role="avgiver", shares=500),
                    _line(2, role="mottaker", shares=500),
                ],
            )
        ],
    )
    bad = compute.compute_year(
        company=comp, year=2026, share_classes=[sc_bad], shareholders=[h1, h2]
    )
    assert bad.blocked
    assert any(f.code == "kontinuitet_mangler_fnr" for f in bad.classes[0].findings)

    # With counterparty fnr on both lines, validates against XSD cleanly.
    sc_ok = _share_class(
        1,
        opening_holdings=[_opening(1, 1000)],
        events=[
            _event(
                1,
                "overdragelse",
                date(2026, 4, 1),
                subtype="skilsmisse",
                lines=[
                    _line(1, role="avgiver", shares=500, counterparty_national_id=TENOR_FNR_2),
                    _line(2, role="mottaker", shares=500, counterparty_national_id=TENOR_FNR_1),
                ],
            )
        ],
    )
    ok = compute.compute_year(company=comp, year=2026, share_classes=[sc_ok], shareholders=[h1, h2])
    assert not ok.blocked
    built = xmlwriter.build_filing_xml(ok.classes[0], comp)
    _assert_hoved_valid(built.hovedskjema_xml)
    for _, _, uxml in built.underskjemaer:
        _assert_under_valid(uxml)


def test_splitt_spleis_fondsemisjon_and_capital_adjustments_validate_xsd() -> None:
    comp = _company()
    h1 = _holder(1, name="Eier En", national_id=TENOR_FNR_1)
    sc = _share_class(
        1,
        opening_shares=1000,
        opening_par_value=Decimal("30"),
        opening_share_capital=Decimal("30000.00"),
        opening_paid_in_capital=Decimal("30000.00"),
        opening_paid_in_premium=Decimal("20000.00"),
        opening_holdings=[_opening(1, 1000)],
        events=[
            # 1) Split 1 -> 2 (+1000 shares, par 30 -> 15)
            _event(
                1,
                "splitt",
                date(2026, 2, 1),
                total_shares=1000,
                par_value_after=Decimal("15"),
                lines=[_line(1, shares=1000)],
            ),
            # 2) Fondsemisjon new shares (+1000 shares @ 15 = +15000 cap, paid_in unchanged!)
            _event(
                2,
                "fondsemisjon",
                date(2026, 3, 1),
                subtype="nye_aksjer",
                total_shares=1000,
                par_value_after=Decimal("15"),
                total_amount=Decimal("15000.00"),
                lines=[_line(1, shares=1000)],
            ),
            # 3) Fondsemisjon increased par (+15000 cap -> par 20 on 3000 shares, paid_in unchanged)
            _event(
                3,
                "fondsemisjon",
                date(2026, 4, 1),
                subtype="okning_palydende",
                total_amount=Decimal("15000.00"),
                par_value_after=Decimal("20"),
                amount_per_share=Decimal("5"),
                lines=[],
            ),
            # 4) Kapitalforhøyelse pålydende (+30000 cap + 10000 overkurs -> par 30)
            _event(
                4,
                "kapitalforhoyelse_palydende",
                date(2026, 5, 1),
                subtype="6",
                total_amount=Decimal("30000.00"),
                premium_amount=Decimal("10000.00"),
                par_value_after=Decimal("30"),
                amount_per_share=Decimal("10"),
                lines=[
                    _line(
                        1,
                        shares=3000,
                        amount=Decimal("30000.00"),
                        paid_in_premium=Decimal("10000.00"),
                    )
                ],
            ),
            # 5) Kapitalnedsettelse overkurs (-10000 premium)
            _event(
                5,
                "kapitalnedsettelse",
                date(2026, 6, 1),
                subtype="overkurs",
                total_amount=Decimal("10000.00"),
                lines=[_line(1, shares=3000, amount=Decimal("10000.00"))],
            ),
            # 6) Kapitalnedsettelse tapsdekning (-15000 cap -> par 25)
            _event(
                6,
                "kapitalnedsettelse",
                date(2026, 7, 1),
                subtype="tapsdekning",
                total_amount=Decimal("15000.00"),
                par_value_after=Decimal("25"),
                amount_per_share=Decimal("5"),
                lines=[],
            ),
            # 7) Kapitalnedsettelse utbetaling (-15000 cap & paid_in -> par 20)
            _event(
                7,
                "kapitalnedsettelse",
                date(2026, 8, 1),
                subtype="utbetaling",
                total_amount=Decimal("15000.00"),
                par_value_after=Decimal("20"),
                amount_per_share=Decimal("5"),
                lines=[_line(1, shares=3000, amount=Decimal("15000.00"))],
            ),
            # 8) Spleis 3000 -> 1500 (-1500 shares, par 20 -> 40, cap stays 60000)
            _event(
                8,
                "spleis",
                date(2026, 9, 1),
                total_shares=1500,
                par_value_after=Decimal("40"),
                lines=[_line(1, shares=1500)],
            ),
        ],
    )
    computed = compute.compute_year(company=comp, year=2026, share_classes=[sc], shareholders=[h1])
    assert not computed.blocked, computed.classes[0].findings
    c0 = computed.classes[0]
    assert c0.closing.shares == 1500
    assert c0.closing.par_value == Decimal("40")
    assert c0.closing.share_capital == Decimal("60000.00")
    # Opening 30000 + 30000 (kap.forh. pålydende) - 15000 (utbetaling) = 45000
    assert c0.closing.paid_in_capital == Decimal("45000.00")
    # Opening 20000 + 10000 - 10000 = 20000
    assert c0.closing.paid_in_premium == Decimal("20000.00")

    built = xmlwriter.build_filing_xml(c0, comp)
    _assert_hoved_valid(built.hovedskjema_xml)
    for _, _, uxml in built.underskjemaer:
        _assert_under_valid(uxml)


def test_likvidasjon_zeroes_post_1_to_6_and_validates_xsd() -> None:
    comp = _company()
    h1 = _holder(1, name="Eier En", national_id=TENOR_FNR_1)
    sc = _share_class(
        1,
        opening_holdings=[_opening(1, 1000)],
        events=[
            _event(
                1,
                "likvidasjon",
                date(2026, 10, 15),
                total_shares=1000,
                total_amount=Decimal("250000.00"),
                lines=[_line(1, shares=1000, amount=Decimal("250000.00"))],
            )
        ],
    )
    computed = compute.compute_year(company=comp, year=2026, share_classes=[sc], shareholders=[h1])
    assert not computed.blocked
    c0 = computed.classes[0]
    assert c0.liquidated is True
    assert c0.closing.shares == 0
    assert c0.closing.share_capital == Decimal("0.00")
    assert c0.closing.paid_in_capital == Decimal("0.00")
    assert c0.closing.paid_in_premium == Decimal("0.00")

    built = xmlwriter.build_filing_xml(c0, comp)
    hoved = _assert_hoved_valid(built.hovedskjema_xml)
    assert hoved.findtext(".//Aksjekapital-datadef-87") == "0"
    assert hoved.findtext(".//AksjerMvAntall-datadef-29167") == "0"
    assert hoved.findtext(".//AksjerLividasjonMvAntall-datadef-17689") == "0"
    assert len(built.underskjemaer) == 1
    under = _assert_under_valid(built.underskjemaer[0][2])
    assert under.findtext(".//AksjonarAksjerAntall-datadef-17741") == "0"


def test_multiple_share_classes_sum_post_1_and_separate_post_2() -> None:
    comp = _company()
    h1 = _holder(1, name="A-eier", national_id=TENOR_FNR_1)
    h2 = _holder(2, name="B-eier", national_id=TENOR_FNR_2)
    sc_a = _share_class(
        1,
        code="02",
        name="A-aksjer",
        opening_shares=100,
        opening_par_value=Decimal("200"),
        opening_share_capital=Decimal("20000.00"),
        opening_paid_in_capital=Decimal("20000.00"),
        opening_holdings=[_opening(1, 100)],
    )
    sc_b = _share_class(
        2,
        code="03",
        name="B-aksjer",
        opening_shares=100,
        opening_par_value=Decimal("100"),
        opening_share_capital=Decimal("10000.00"),
        opening_paid_in_capital=Decimal("10000.00"),
        opening_holdings=[_opening(2, 100)],
    )
    computed = compute.compute_year(
        company=comp, year=2026, share_classes=[sc_a, sc_b], shareholders=[h1, h2]
    )
    assert not computed.blocked
    assert computed.company_closing_capital == Decimal("30000.00")

    for csc, expected_class_cap in zip(computed.classes, ("20000", "10000"), strict=True):
        built = xmlwriter.build_filing_xml(csc, comp)
        hoved = _assert_hoved_valid(built.hovedskjema_xml)
        # Post 1 is always total company share capital (30 000).
        assert hoved.findtext(".//Aksjekapital-datadef-87") == "30000"
        # Post 2 is this share class's capital.
        assert hoved.findtext(".//AksjekapitalISINAksjetype-datadef-17664") == expected_class_cap


def test_prefill_roundtrip_parses_generated_rf1086_xml() -> None:
    comp = _company()
    h1 = _holder(1, name="Kari Nordmann", national_id=TENOR_FNR_1)
    h2 = _holder(2, kind="selskap", name="Invest AS", national_id=None, orgnr=VALID_ORGNR_HOLDER)
    sc = _share_class(
        1,
        opening_shares=1000,
        opening_par_value=Decimal("30"),
        opening_share_capital=Decimal("30000.00"),
        opening_paid_in_capital=Decimal("30000.00"),
        opening_paid_in_premium=Decimal("5000.00"),
        opening_holdings=[_opening(1, 600), _opening(2, 400)],
    )
    computed = compute.compute_year(
        company=comp, year=2025, share_classes=[sc], shareholders=[h1, h2]
    )
    built = xmlwriter.build_filing_xml(computed.classes[0], comp)

    docs = [("ordinaer", built.hovedskjema_xml)] + [
        ("ordinaer", uxml) for _, _, uxml in built.underskjemaer
    ]
    parsed = xmlwriter.parse_prefill_documents(docs, for_year=2026)

    assert len(parsed.share_classes) == 1
    psc = parsed.share_classes[0]
    assert psc.code == "01"
    assert psc.opening_year == 2025
    assert psc.opening_shares == 1000
    assert psc.opening_par_value == Decimal("30")
    assert psc.opening_share_capital == Decimal("30000")
    assert psc.opening_paid_in_premium == Decimal("5000")

    assert len(parsed.shareholders) == 2
    by_name = {h.name: h for h in parsed.shareholders}
    assert by_name["Kari Nordmann"].national_id == TENOR_FNR_1
    assert by_name["Kari Nordmann"].opening_shares == 600
    assert by_name["Invest AS"].orgnr == VALID_ORGNR_HOLDER
    assert by_name["Invest AS"].opening_shares == 400


def test_ledger_account_2000_mismatch_produces_merknad_not_feil() -> None:
    comp = _company()
    h1 = _holder(1, name="Eier En", national_id=TENOR_FNR_1)
    sc = _share_class(1, opening_holdings=[_opening(1, 1000)])
    computed = compute.compute_year(
        company=comp,
        year=2026,
        share_classes=[sc],
        shareholders=[h1],
        contact_email="post@nordlys.example",
        ledger_share_capital=Decimal("50000.00"),
    )
    assert not computed.blocked
    assert any(
        f.code == "aksjekapital_avviker_fra_regnskap" and f.severity is Severity.MERKNAD
        for f in computed.findings
    )
