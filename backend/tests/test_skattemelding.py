"""The skattemelding package: the XSD reader, the writer, the code lists, the build, the envelope.

Every document Myntos writes here is run through Skatteetaten's own XSD with
lxml, for each year Myntos has one, because the writer takes its element order
from the reader and a reader that misread the order would write a document
the two agree on and Skatteetaten refuses. The answers Skatteetaten gave are
TT02's own, kept in ``fixtures/skattemelding`` (synthetic Tenor data; the README
there says where each came from).

The figures are ``digdir_check.sample_grunnlag``'s, the books TT02 validated on
2026-10-21: 1 200 000 in sales, 821 000 in costs, 1 000 net finance, 83 820 in
tax at 22 %, so an årsresultat of 297 180 and a skattemessig resultat of
381 000 once the tax is added back.
"""

from __future__ import annotations

import base64
import functools
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from lxml import etree

from myntos.auth.digdir_check import sample_grunnlag
from myntos.domain.anlegg.rules import Saldogruppe
from myntos.domain.anlegg.saldo import GainLossYear, SaldoSpecification, SaldoYear
from myntos.domain.arsoppgjor.findings import Severity
from myntos.skattemelding import codes, envelope, schema, upersonlig, xmlwriter
from myntos.skattemelding.naeringsspesifikasjon import (
    FORDELT_ID,
    GENERAL_RULES,
    SMALL_COMPANY_RULES,
    AccountFigures,
    Grunnlag,
    Naeringsspesifikasjon,
    build,
    document,
)

FIXTURES = Path(__file__).parent / "fixtures" / "skattemelding"
SCHEMAS = Path(schema.__file__).parent / "xsd"
PARTSNUMMER = 3001570199

# Our own output and Skatteetaten's vendored files only, but a parser that
# fetches DTDs or expands entities has no business near either.
_PARSER = etree.XMLParser(resolve_entities=False, no_network=True)


@functools.cache
def _xsd(filename: str) -> etree.XMLSchema:
    return etree.XMLSchema(etree.parse(str(SCHEMAS / filename), _PARSER))


def assert_valid(filename: str, content: bytes) -> None:
    validator = _xsd(filename)
    if not validator.validate(etree.fromstring(content, _PARSER)):
        messages = "\n".join(f"{e.line}: {e.message}" for e in validator.error_log)
        raise AssertionError(f"not valid against {filename}:\n{messages}")


def _codes(nsp: Naeringsspesifikasjon, severity: Severity | None = None) -> set[str]:
    return {f.code for f in nsp.findings if severity is None or f.severity is severity}


def _with(grunnlag: Grunnlag, *accounts: AccountFigures) -> Grunnlag:
    return replace(grunnlag, accounts=grunnlag.accounts + accounts)


def _utkast() -> envelope.FetchedMelding:
    return envelope.parse_fetch((FIXTURES / "tt02-utkast-314067630-2025.xml").read_bytes())


def _utkast_2026() -> envelope.FetchedMelding:
    namespace = schema.skattemelding_upersonlig(2026).namespace
    content = (
        f'<?xml version="1.0" encoding="UTF-8"?><skattemelding xmlns="{namespace}">'
        f"<partsnummer>{PARTSNUMMER}</partsnummer><inntektsaar>2026</inntektsaar>"
        "</skattemelding>"
    ).encode()
    return envelope.FetchedMelding("SKI:755:1", "skattemeldingUpersonligUtkast", content)


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _root(content: bytes) -> ET.Element:
    return ET.fromstring(content)  # noqa: S314 - our own output


# --- The XSD reader ----------------------------------------------------------------------


def test_the_order_is_each_year_s_own() -> None:
    # v7 (2026) moved sumEgenkapital after the lists it sums. A writer with
    # 2025's order typed into it would write 2026's balance in the wrong order.
    def position(year: int) -> int:
        names = [c.name for c in schema.naeringsspesifikasjon(year).types["GjeldOgEgenkapital"]]
        return names.index("sumEgenkapital") - names.index("egenkapital")

    assert position(2025) < 0
    assert position(2026) > 0


def test_derived_elements_are_marked() -> None:
    nsp = schema.naeringsspesifikasjon(2025)
    driftsinntekt = {c.name: c for c in nsp.types["Driftsinntekt"]}
    assert driftsinntekt["sumDriftsinntekt"].derived
    assert not driftsinntekt["salgsinntekt"].derived
    result = {c.name: c for c in nsp.types["Resultatregnskap"]}
    assert result["aarsresultat"].derived


def test_an_extension_carries_its_base_s_elements_first() -> None:
    types = schema.naeringsspesifikasjon(2025).types
    saldo = [c.name for c in types["SaldoavskrevetAnleggsmiddel"]]
    # objektidentifikator comes from the base type, the saldo's own elements after.
    assert saldo.index("objektidentifikator") < saldo.index("saldogruppe")
    assert "avskrivningssats" in saldo


def test_a_choice_is_numbered() -> None:
    avstemming = {
        c.name: c for c in schema.naeringsspesifikasjon(2025).types["Egenkapitalavstemming"]
    }
    assert avstemming["utsattSkatt"].choice is not None
    assert avstemming["utsattSkatt"].choice == avstemming["utsattSkattefordel"].choice
    assert avstemming["inngaaendeEgenkapital"].choice is None


def test_a_year_without_a_schema_is_refused() -> None:
    with pytest.raises(schema.SchemaError, match="2024"):
        schema.naeringsspesifikasjon(2024)
    with pytest.raises(schema.SchemaError, match="2027"):
        schema.skattemelding_upersonlig(2027)


# --- The writer ----------------------------------------------------------------------------


def _minimal() -> dict[str, object]:
    return {
        "partsreferanse": PARTSNUMMER,
        "inntektsaar": 2025,
        "virksomhet": {
            "regnskapspliktstype": {"regnskapspliktstype": "fullRegnskapsplikt"},
            "regnskapsperiode": {
                "start": {"dato": date(2025, 1, 1)},
                "slutt": {"dato": date(2025, 12, 31)},
            },
            "virksomhetstype": {"virksomhetstype": "oevrigSelskap"},
        },
        "skalBekreftesAvRevisor": False,
    }


def test_the_writer_puts_elements_in_the_xsd_s_order() -> None:
    tree = _minimal()
    # Given in the reverse of the XSD's order.
    reordered = dict(reversed(list(tree.items())))
    content = xmlwriter.write(schema.naeringsspesifikasjon(2025), reordered)
    assert_valid(schema.NAERINGSSPESIFIKASJON[2025], content)
    names = [_local(child.tag) for child in _root(content)]
    assert names == ["partsreferanse", "inntektsaar", "virksomhet", "skalBekreftesAvRevisor"]


def test_the_writer_refuses_a_derived_element() -> None:
    tree = _minimal() | {
        "resultatregnskap": {
            "driftsinntekt": {"sumDriftsinntekt": {"beloep": {"beloep": Decimal("1.00")}}}
        }
    }
    with pytest.raises(xmlwriter.DocumentError, match=r"sumDriftsinntekt.*erAvledet"):
        xmlwriter.write(schema.naeringsspesifikasjon(2025), tree)


def test_the_writer_refuses_what_the_type_does_not_have() -> None:
    with pytest.raises(xmlwriter.DocumentError, match="no such element"):
        xmlwriter.write(schema.naeringsspesifikasjon(2025), _minimal() | {"aarsresultatet": 1})


def test_the_writer_refuses_a_missing_required_element() -> None:
    tree = _minimal()
    del tree["skalBekreftesAvRevisor"]
    with pytest.raises(xmlwriter.DocumentError, match="skalBekreftesAvRevisor: required"):
        xmlwriter.write(schema.naeringsspesifikasjon(2025), tree)


def test_the_writer_refuses_two_of_a_choice() -> None:
    tree = _minimal() | {
        "egenkapitalavstemming": {
            "utsattSkatt": {"beloep": {"beloep": Decimal("1.00")}},
            "utsattSkattefordel": {"beloep": {"beloep": Decimal("1.00")}},
        }
    }
    with pytest.raises(xmlwriter.DocumentError, match="only one of"):
        xmlwriter.write(schema.naeringsspesifikasjon(2025), tree)


def test_the_writer_refuses_more_than_the_xsd_allows() -> None:
    with pytest.raises(xmlwriter.DocumentError, match="at most 1"):
        xmlwriter.write(schema.naeringsspesifikasjon(2025), _minimal() | {"inntektsaar": [1, 2]})


def test_an_amount_is_written_as_given_and_never_in_exponent_form() -> None:
    tree = _minimal() | {
        "egenkapitalavstemming": {"inngaaendeEgenkapital": {"beloep": {"beloep": Decimal("1E+3")}}}
    }
    content = xmlwriter.write(schema.naeringsspesifikasjon(2025), tree)
    assert b"<beloep>1000</beloep>" in content
    assert_valid(schema.NAERINGSSPESIFIKASJON[2025], content)


def test_merge_adds_a_section_in_the_xsd_s_place_and_keeps_one_already_there() -> None:
    upersonlig_2025 = schema.skattemelding_upersonlig(2025)
    utkast = _utkast()
    once, added = xmlwriter.merge(
        upersonlig_2025,
        utkast.content,
        {"opplysningOmSkattesubjekt": {"erBoersnotert": False}},
    )
    assert added == ("opplysningOmSkattesubjekt",)
    names = [_local(child.tag) for child in _root(once)]
    assert names[:2] == ["partsnummer", "inntektsaar"]
    assert names[-1] == "opplysningOmSkattesubjekt"

    twice, added_again = xmlwriter.merge(
        upersonlig_2025, once, {"opplysningOmSkattesubjekt": {"erBoersnotert": True}}
    )
    assert added_again == ()
    assert b"<erBoersnotert>false</erBoersnotert>" in twice


def test_merge_takes_out_what_skatteetaten_derives_from_a_filed_melding() -> None:
    # The shape of 314067630's fastsatt 2026 melding in TT02 (2026-10-22),
    # which came back refused as avvikSkattemelding when sent as it was served.
    ns = schema.skattemelding_upersonlig(2026).namespace
    fastsatt = (
        f'<skattemelding xmlns="{ns}"><partsnummer>{PARTSNUMMER}</partsnummer>'
        "<inntektsaar>2026</inntektsaar><inntektOgUnderskudd>"
        "<underskuddTilFremfoering><fremfoerbartUnderskuddIInntekt><beloep>"
        "<beloepSomHeltall>500</beloepSomHeltall></beloep></fremfoerbartUnderskuddIInntekt>"
        "</underskuddTilFremfoering>"
        "<inntektFoerFradragForEventueltAvgittKonsernbidrag><beloepSomHeltall>-500"
        "</beloepSomHeltall></inntektFoerFradragForEventueltAvgittKonsernbidrag>"
        "<samletUnderskudd><beloep><beloepSomHeltall>700</beloepSomHeltall></beloep>"
        "<erOverstyrt><boolsk>true</boolsk></erOverstyrt></samletUnderskudd>"
        "</inntektOgUnderskudd><opplysningOmSkattesubjekt><erBoersnotert>false</erBoersnotert>"
        "</opplysningOmSkattesubjekt></skattemelding>"
    ).encode()
    merged, added = xmlwriter.merge(
        schema.skattemelding_upersonlig(2026),
        fastsatt,
        {"opplysningOmSkattesubjekt": {"erBoersnotert": True}},
    )
    assert added == ()
    assert b"fremfoerbartUnderskuddIInntekt" not in merged
    assert b"underskuddTilFremfoering" not in merged
    assert b"inntektFoerFradragForEventueltAvgittKonsernbidrag" not in merged
    # The person overrode this one on skatteetaten.no: theirs, so it stays.
    assert b"<beloepSomHeltall>700</beloepSomHeltall>" in merged
    assert b"<erBoersnotert>false</erBoersnotert>" in merged
    assert_valid(schema.SKATTEMELDING_UPERSONLIG[2026], merged)


def test_merge_refuses_a_document_of_another_kind() -> None:
    with pytest.raises(xmlwriter.DocumentError, match="not a skattemelding"):
        xmlwriter.merge(
            schema.skattemelding_upersonlig(2026), _utkast().content, {"erBoersnotert": False}
        )


# --- The code lists ------------------------------------------------------------------------


def test_the_code_lists_say_what_a_post_is() -> None:
    posts = codes.posts(2025)
    sales = posts[("salgsinntekt", "3000")]
    assert sales.kategori == "positiv"
    assert sales.allows(regnskapsplikt="fullRegnskapsplikt", business_type="oevrigSelskap")
    assert posts[("salgsinntekt", "3300")].kategori == "negativ"
    # Real estate an AS owns is a fullRegnskapsplikt post; 1140 is only for
    # the books of someone without regnskapsplikt.
    assert not posts[("balanseverdiForAnleggsmiddel", "1140")].allows(
        regnskapsplikt="fullRegnskapsplikt", business_type="oevrigSelskap"
    )
    assert posts[("balanseverdiForAnleggsmiddel", "1205")].gives_discount
    assert not posts[("balanseverdiForOmloepsmiddel", "1920")].gives_discount
    assert codes.by_code(2026, codes.PERMANENT_DIFFERENCE)["positivSkattekostnad"].kategori == (
        "tillegg"
    )


def test_a_year_without_code_lists_is_refused() -> None:
    with pytest.raises(codes.CodeListError, match="2024"):
        codes.posts(2024)


# --- The build -----------------------------------------------------------------------------


def test_the_sample_books_give_the_figures_tt02_accepted() -> None:
    nsp = build(sample_grunnlag(2025))
    assert not nsp.blocked, nsp.findings
    assert nsp.result == Decimal("297180.00")
    assert nsp.additions == Decimal("83820.00")
    assert nsp.deductions == Decimal("0.00")
    assert nsp.taxable_result == Decimal("381000.00")
    assert [(d.type, d.amount, d.adds) for d in nsp.permanent] == [
        ("positivSkattekostnad", Decimal("83820.00"), True)
    ]
    assert nsp.equity_opening == Decimal("130000.00")
    assert nsp.equity_closing == Decimal("427180.00")
    assert [(c.type, c.amount) for c in nsp.equity_changes] == [
        ("aaretsOverskudd", Decimal("297180.00"))
    ]
    assert nsp.rules == SMALL_COMPANY_RULES
    assert nsp.assets == nsp.claims == Decimal("511000.00")


def test_a_post_carries_the_accounts_behind_it() -> None:
    nsp = build(sample_grunnlag(2025))
    by_code = {post.code: post for post in nsp.posts}
    assert by_code["4005"].accounts == ("4300",)
    assert by_code["4005"].amount == Decimal("400000.00")
    # The disposition of the result is SAF-T's, not a post.
    assert "8800" not in by_code


def test_a_post_skatteetaten_subtracts_is_sent_turned_round() -> None:
    grunnlag = _with(
        sample_grunnlag(2025),
        AccountFigures(
            "3300", "Særavgifter", "salgsinntekt", "3300", Decimal("0"), Decimal("5000")
        ),
        AccountFigures(
            "1910",
            "Kontanter",
            "balanseverdiForOmloepsmiddel",
            "1900",
            Decimal("0"),
            Decimal("-5000"),
        ),
    )
    post = {p.code: p for p in build(grunnlag).posts}["3300"]
    # A debit on an income post reads -5 000 as income; Skatteetaten subtracts
    # 3300, so it is sent as 5 000.
    assert post.natural == Decimal("-5000.00")
    assert post.amount == Decimal("5000.00")
    assert post.subtracted


def test_an_open_year_is_refused() -> None:
    nsp = build(replace(sample_grunnlag(2025), year_closed=False))
    assert "aret_er_apent" in _codes(nsp, Severity.FEIL)


@pytest.mark.parametrize(
    ("form", "code"), [("ENK", "enk_ikke_ferdig"), ("ANS", "organisasjonsform_ikke_stottet")]
)
def test_only_an_as_is_built_so_far(form: str, code: str) -> None:
    nsp = build(replace(sample_grunnlag(2025), organisation_form=form))
    assert nsp.blocked
    assert code in _codes(nsp, Severity.FEIL)


def test_an_account_without_a_post_is_refused_by_name() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures("1599", "Diverse", None, None, Decimal("0"), Decimal("100")),
        )
    )
    finding = next(f for f in nsp.findings if f.code == "konto_uten_post")
    assert "1599 Diverse" in finding.message


def test_a_post_not_in_the_year_s_list_is_refused() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "6999", "Annet", "annenDriftskostnad", "6999", Decimal("0"), Decimal("1")
            ),
        )
    )
    assert "ukjent_post" in _codes(nsp, Severity.FEIL)


def test_a_post_outside_the_company_s_regnskapsplikt_is_refused() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "1140",
                "Tomt",
                "balanseverdiForAnleggsmiddel",
                "1140",
                Decimal("0"),
                Decimal("1"),
            ),
        )
    )
    assert "post_ikke_tillatt" in _codes(nsp, Severity.FEIL)


def test_a_balance_that_does_not_hold_is_refused() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "1500",
                "Kundefordringer",
                "balanseverdiForOmloepsmiddel",
                "1500",
                Decimal("0"),
                Decimal("1000"),
            ),
        )
    )
    assert "balansen_gar_ikke_opp" in _codes(nsp, Severity.FEIL)


def test_other_changes_in_equity_are_named_and_said() -> None:
    # A capital increase of 50 000: equity and the bank both up.
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "2020", "Overkurs", "egenkapital", "2020", Decimal("0"), Decimal("-50000")
            ),
            AccountFigures(
                "1921",
                "Bank 2",
                "balanseverdiForOmloepsmiddel",
                "1920",
                Decimal("0"),
                Decimal("50000"),
            ),
        )
    )
    assert not nsp.blocked, nsp.findings
    assert ("annenPositivEndringIEgenkapital", Decimal("50000.00")) in [
        (c.type, c.amount) for c in nsp.equity_changes
    ]
    assert "annen_endring_i_egenkapital" in _codes(nsp, Severity.MERKNAD)


def test_a_large_company_follows_the_general_rules() -> None:
    big = Decimal("80000000")
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures("3001", "Salg", "salgsinntekt", "3000", Decimal("0"), -big),
            AccountFigures("4301", "Varer", "varekostnad", "4005", Decimal("0"), big),
            AccountFigures(
                "1501", "Fordringer", "balanseverdiForOmloepsmiddel", "1500", Decimal("0"), big
            ),
            AccountFigures("2401", "Leverandører", "kortsiktigGjeld", "2400", Decimal("0"), -big),
        )
    )
    assert nsp.rules == GENERAL_RULES
    assert "ikke_sma_foretak" in _codes(nsp, Severity.MERKNAD)


def _saldo(depreciation: Decimal, basis: Decimal = Decimal("100000")) -> SaldoYear:
    zero = Decimal("0")
    return SaldoYear(
        key="d",
        saldogruppe=Saldogruppe.D,
        label="Saldogruppe d",
        asset_id=None,
        rate_variant=None,
        rate=Decimal("0.20"),
        opening=basis,
        additions=zero,
        grants=zero,
        disposals=zero,
        basis=basis,
        max_depreciation=basis * Decimal("0.20"),
        min_income=zero,
        depreciation=depreciation,
        income=zero,
        to_gain_loss_account=zero,
        closing=basis - depreciation,
        small_rest=False,
        accounts=(),
    )


def _spec(*saldi: SaldoYear, posted: bool = True) -> SaldoSpecification:
    zero = Decimal("0")
    return SaldoSpecification(
        company_id=1,
        year=2025,
        start_year=2024,
        rules_from_year=2024,
        rates_confirmed=True,
        posted=posted,
        saldi=saldi,
        gain_loss_account=GainLossYear(zero, zero, zero, zero, zero, zero, zero),
    )


def test_the_year_s_depreciation_is_sent_as_the_rate_that_gives_it() -> None:
    # Less than the group's 20 %: the law says «inntil», and the rate is how
    # the choice is told.
    basis, depreciation = Decimal("98765.43"), Decimal("12345.67")
    nsp = build(replace(sample_grunnlag(2025), saldo=_spec(_saldo(depreciation, basis))))
    (line,) = nsp.saldi
    assert (basis * line.rate_percent / 100).quantize(Decimal("0.01")) == depreciation
    assert line.physical


def test_unposted_depreciation_is_refused() -> None:
    nsp = build(replace(sample_grunnlag(2025), saldo=_spec(_saldo(Decimal("20000")), posted=False)))
    assert "avskrivning_ikke_fort" in _codes(nsp, Severity.FEIL)


# --- The document --------------------------------------------------------------------------


@pytest.mark.parametrize("year", sorted(schema.NAERINGSSPESIFIKASJON))
def test_the_naeringsspesifikasjon_is_valid_for_each_year(year: int) -> None:
    nsp = build(replace(sample_grunnlag(year), saldo=_spec(_saldo(Decimal("15000")))))
    content = xmlwriter.write(
        schema.naeringsspesifikasjon(year), document(nsp, partsreferanse=PARTSNUMMER)
    )
    assert_valid(schema.NAERINGSSPESIFIKASJON[year], content)


def test_the_document_sends_the_inputs_and_repeats_the_taxable_result() -> None:
    nsp = build(sample_grunnlag(2025))
    root = _root(
        xmlwriter.write(
            schema.naeringsspesifikasjon(2025), document(nsp, partsreferanse=PARTSNUMMER)
        )
    )
    ns = {"n": schema.naeringsspesifikasjon(2025).namespace}
    assert root.findtext("n:partsreferanse", namespaces=ns) == str(PARTSNUMMER)
    fordelt = root.find(
        "n:beregnetNaeringsinntekt/n:fordeltBeregnetNaeringsinntektForUpersonligSkattepliktig",
        ns,
    )
    assert fordelt is not None
    assert fordelt.findtext("n:id", namespaces=ns) == FORDELT_ID
    assert (
        fordelt.findtext("n:fordeltSkattemessigResultat/n:beloep/n:beloep", namespaces=ns)
        == "381000.00"
    )
    sales = root.find("n:resultatregnskap/n:driftsinntekt/n:salgsinntekt/n:inntekt", ns)
    assert sales is not None
    assert sales.findtext("n:id", namespaces=ns) == "3000"
    assert sales.findtext("n:beloep/n:beloep/n:beloep", namespaces=ns) == "1200000.00"
    assert root.findtext("n:skalBekreftesAvRevisor", namespaces=ns) == "false"


def test_the_saldo_specification_carries_the_rate_not_the_amount() -> None:
    nsp = build(replace(sample_grunnlag(2025), saldo=_spec(_saldo(Decimal("15000")))))
    content = xmlwriter.write(
        schema.naeringsspesifikasjon(2025), document(nsp, partsreferanse=PARTSNUMMER)
    )
    ns = {"n": schema.naeringsspesifikasjon(2025).namespace}
    saldo = _root(content).find("n:spesifikasjonAvAnleggsmiddel/n:saldoavskrevetAnleggsmiddel", ns)
    assert saldo is not None
    assert saldo.findtext("n:avskrivningssats/n:prosent", namespaces=ns) == "15"
    assert saldo.find("n:aaretsAvskrivning", ns) is None


# --- The envelope --------------------------------------------------------------------------


def test_the_fetch_gives_the_utkast_and_its_partsnummer() -> None:
    melding = _utkast()
    assert melding.id == "SKI:755:32858"
    assert melding.type == "skattemeldingUpersonligUtkast"
    assert melding.request_type == "skattemeldingUpersonlig"
    assert not melding.personal
    assert melding.partsnummer == PARTSNUMMER


def test_a_fetch_without_a_melding_is_refused() -> None:
    with pytest.raises(envelope.EnvelopeError, match="uten noen skattemelding"):
        envelope.parse_fetch(b"<svar><dokumenter/></svar>")
    with pytest.raises(envelope.EnvelopeError, match="lesbar"):
        envelope.parse_fetch(b"not xml")


def test_the_request_is_valid_and_names_the_melding_it_answers() -> None:
    nsp_xml = b"<naeringsspesifikasjon/>"
    request = envelope.request_xml(
        year=2025,
        melding=_utkast(),
        naeringsspesifikasjon=nsp_xml,
        innsendingstype=envelope.Innsendingstype.KOMPLETT,
        tin="314067630",
    )
    assert_valid(schema.REQUEST, request)
    root = _root(request)
    ns = {"r": schema.load(schema.REQUEST).namespace}
    kinds = [
        d.findtext("r:type", namespaces=ns) for d in root.iterfind("r:dokumenter/r:dokument", ns)
    ]
    assert kinds == ["skattemeldingUpersonlig", "naeringsspesifikasjon"]
    second = root.findall("r:dokumenter/r:dokument", ns)[1]
    assert base64.b64decode(second.findtext("r:content", namespaces=ns) or "") == nsp_xml
    reference = root.find("r:dokumentreferanseTilGjeldendeDokument", ns)
    assert reference is not None
    assert reference.findtext("r:dokumentidentifikator", namespaces=ns) == "SKI:755:32858"
    information = root.find("r:innsendingsinformasjon", ns)
    assert information is not None
    assert information.findtext("r:innsendingstype", namespaces=ns) == "komplett"
    assert information.findtext("r:innsendingsformaal", namespaces=ns) == "egenfastsetting"
    assert information.findtext("r:opprettetAv", namespaces=ns) == envelope.CREATED_BY


def test_tt02_s_answer_reads_as_accepted_with_the_tax_it_computed() -> None:
    answer = (FIXTURES / "tt02-validering-314067630-2025.xml").read_bytes()
    assert_valid(schema.RESPONSE, answer)
    result = envelope.parse_validation(answer)
    assert result.accepted
    assert result.reasons == ()
    assert result.errors == ()
    assert result.computed_tax == Decimal("83820")
    # Every avvik is a derived value Skatteetaten filled in itself: what a
    # melding that sends no derived values gets back.
    assert result.avvik
    assert all(item.type.startswith("mangler") for item in result.avvik)
    assert {"naeringsspesifikasjonEtterBeregning", "beregnetSkattUpersonlig"} <= set(
        result.documents
    )


def test_tt02_s_answer_to_the_filing_reads_like_the_check() -> None:
    """``tilbakemelding`` is the validation's own document, computed on what was filed.

    Found when the synthetic AS was filed in TT02 on 2026-10-22: Altinn's
    status said «Mottatt», and the answer on the instance is a
    skattemeldingOgNaeringsspesifikasjonResponse like the check's.
    """
    answer = (FIXTURES / "tt02-tilbakemelding-314067630-2025.xml").read_bytes()
    assert_valid(schema.RESPONSE, answer)
    result = envelope.parse_validation(answer)
    assert result.accepted
    assert result.computed_tax == Decimal("83820")


def test_a_refusal_reads_as_one_with_its_reasons() -> None:
    namespace = schema.load(schema.RESPONSE).namespace
    answer = (
        f'<skattemeldingOgNaeringsspesifikasjonResponse xmlns="{namespace}">'
        "<resultatAvValidering>validertMedFeil</resultatAvValidering>"
        "<aarsakTilValidertMedFeil>avvikNaeringsopplysninger</aarsakTilValidertMedFeil>"
        "<veiledningEtterKontroll><veiledning><veiledningstype>N_FEIL</veiledningstype>"
        "<hjelpetekst>Feil</hjelpetekst><betjeningsstrategi>faktiskFeil</betjeningsstrategi>"
        "</veiledning></veiledningEtterKontroll>"
        "</skattemeldingOgNaeringsspesifikasjonResponse>"
    ).encode()
    result = envelope.parse_validation(answer)
    assert not result.accepted
    assert result.reasons == ("avvikNaeringsopplysninger",)
    assert [item.type for item in result.errors] == ["N_FEIL"]
    assert result.computed_tax is None


# --- The upersonlig skattemelding ----------------------------------------------------------


def test_formue_is_the_balance_sorted_by_discount() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "1230",
                "Maskiner",
                "balanseverdiForAnleggsmiddel",
                "1205",
                Decimal("0"),
                Decimal("40000"),
            ),
            AccountFigures(
                "2400",
                "Leverandører",
                "kortsiktigGjeld",
                "2400",
                Decimal("0"),
                Decimal("-40000"),
            ),
        )
    )
    values, findings = upersonlig.formue(nsp)
    assert values == upersonlig.Formue(
        with_discount=40000, without_discount=511000, debt=83820 + 40000
    )
    assert {f.code for f in findings} == {"formue_fra_regnskapet"}


def test_real_estate_and_shares_are_left_out_and_said() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "1100",
                "Bygninger",
                "balanseverdiForAnleggsmiddel",
                "1105",
                Decimal("0"),
                Decimal("1000000"),
            ),
            AccountFigures(
                "1350",
                "Aksjer",
                "balanseverdiForAnleggsmiddel",
                "1350",
                Decimal("0"),
                Decimal("10000"),
            ),
            AccountFigures(
                "2220", "Lån", "langsiktigGjeld", "2220", Decimal("0"), Decimal("-1010000")
            ),
        )
    )
    values, findings = upersonlig.formue(nsp)
    assert values.with_discount == 0
    assert values.without_discount == 511000
    assert values.debt == 83820 + 1010000
    assert {"formue_eiendom_utelatt", "formue_aksjer_utelatt"} <= {f.code for f in findings}


def test_an_overdrawn_bank_account_is_debt() -> None:
    nsp = build(
        _with(
            sample_grunnlag(2025),
            AccountFigures(
                "1925",
                "Driftskreditt",
                "balanseverdiForOmloepsmiddel",
                "1920",
                Decimal("0"),
                Decimal("-1000"),
            ),
            AccountFigures(
                "1926",
                "Bank 3",
                "balanseverdiForOmloepsmiddel",
                "1920",
                Decimal("0"),
                Decimal("1000"),
            ),
        )
    )
    # Both on post 1920 net to nothing, so this is the sum per post, as sent.
    values, _ = upersonlig.formue(nsp)
    assert values.without_discount == 511000


@pytest.mark.parametrize("year", sorted(schema.SKATTEMELDING_UPERSONLIG))
def test_the_completed_skattemelding_is_valid_for_each_year(year: int) -> None:
    melding = _utkast() if year == 2025 else _utkast_2026()
    content, findings = upersonlig.complete(
        melding, build(sample_grunnlag(year)), upersonlig.Answers(benefits_with_shareholders=True)
    )
    assert_valid(schema.SKATTEMELDING_UPERSONLIG[year], content)
    names = [_local(child.tag) for child in _root(content)]
    assert names[:2] == ["partsnummer", "inntektsaar"]
    assert {"formueOgGjeld", "opplysningOmSkattesubjekt"} <= set(names)
    assert b"<erBoersnotert>false</erBoersnotert>" in content
    assert (
        b">true</harYtelseMellomAksjonaerEllerNaerstaaendeOgSelskapEllerSelskapetsDatterselskap>"
        in content
    )
    assert "formue_fra_regnskapet" in {f.code for f in findings}


def test_a_section_skatteetaten_already_wrote_is_left_alone_and_said() -> None:
    nsp = build(sample_grunnlag(2025))
    answers = upersonlig.Answers(benefits_with_shareholders=False)
    once, _ = upersonlig.complete(_utkast(), nsp, answers)
    twice, findings = upersonlig.complete(replace(_utkast(), content=once), nsp, answers)
    assert ET.tostring(_root(twice)) == ET.tostring(_root(once))
    assert {"formueOgGjeld_fra_skatteetaten", "opplysningOmSkattesubjekt_fra_skatteetaten"} <= {
        f.code for f in findings
    }


def test_the_whole_envelope_is_what_tt02_validated() -> None:
    # The same path digdir_check took on 2026-10-21, short of the network.
    melding = _utkast()
    nsp = build(sample_grunnlag(2025))
    nsp_xml = xmlwriter.write(
        schema.naeringsspesifikasjon(2025),
        document(nsp, partsreferanse=melding.partsnummer or 0),
    )
    completed, _ = upersonlig.complete(
        melding, nsp, upersonlig.Answers(benefits_with_shareholders=False)
    )
    request = envelope.request_xml(
        year=2025,
        melding=replace(melding, content=completed),
        naeringsspesifikasjon=nsp_xml,
        innsendingstype=envelope.Innsendingstype.KOMPLETT,
        tin="314067630",
    )
    assert_valid(schema.REQUEST, request)
    assert_valid(schema.NAERINGSSPESIFIKASJON[2025], nsp_xml)
    assert_valid(schema.SKATTEMELDING_UPERSONLIG[2025], completed)
