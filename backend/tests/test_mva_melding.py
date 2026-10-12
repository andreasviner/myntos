"""The MVA-melding as a pure function of ledger lines, and the XML it is sent as.

No database here. What these tests pin down is the arithmetic and the signs,
which is where a melding goes wrong quietly: a deduction reported positive
doubles what the company owes, an import reported once claims a deduction on
VAT nobody calculated, and a reversal that does not net out reports a sale
that was cancelled. Each of those validates as perfectly well-formed XML.

The XML itself is checked against Skatteetaten's published XSD
(``tests/fixtures/mva``), with ``xmllint`` when the machine has it.
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from myntos.domain.ledger.chart import STANDARD_TAX_CODES
from myntos.domain.ledger.models import NON_REPORTABLE_STANDARD_TAX_CODES
from myntos.domain.mva import melding as m
from myntos.domain.mva.codes import MVA_CODES, Meldingskategori
from myntos.domain.mva.periods import PeriodType, Termin, termin_for, terminer

FIXTURES = Path(__file__).parent / "fixtures" / "mva"
XSD = FIXTURES / "no.skatteetaten.fastsetting.avgift.mva.skattemeldingformerverdiavgift.v1.0.xsd"
NS = {"m": m.MELDING_NAMESPACE}
RESULT_NS = "no:skatteetaten:fastsetting:avgift:mva:valideringsresultat:v1"
ORGNR = "910000004"
TERMIN = Termin(PeriodType.TO_MAANEDER, 2026, 2)


def sale(code: str, rate: str, base: str, vat: str) -> m.TaxedLine:
    """A credit on a revenue account: what a sale looks like in the ledger."""
    return m.TaxedLine(
        code=code,
        rate=Decimal(rate),
        is_debit=False,
        base=Decimal(base),
        debit_tax=Decimal("0.00"),
        credit_tax=Decimal(vat),
    )


def purchase(code: str, rate: str, base: str, vat: str) -> m.TaxedLine:
    """A debit on a cost account: a purchase, an import, a tollpass."""
    return m.TaxedLine(
        code=code,
        rate=Decimal(rate),
        is_debit=True,
        base=Decimal(base),
        debit_tax=Decimal(vat),
        credit_tax=Decimal("0.00"),
    )


def reversed_(line: m.TaxedLine) -> m.TaxedLine:
    """What posting.build_reversal does to a line: everything swaps sides."""
    return m.TaxedLine(
        code=line.code,
        rate=line.rate,
        is_debit=not line.is_debit,
        base=line.base,
        debit_tax=line.credit_tax,
        credit_tax=line.debit_tax,
    )


def by_code(lines: tuple[m.Spesifikasjonslinje, ...]) -> list[tuple[str, str | None, str]]:
    return [
        (
            line.mva_kode,
            None if line.grunnlag is None else str(line.grunnlag),
            str(line.merverdiavgift),
        )
        for line in lines
    ]


# --- Aggregation ------------------------------------------------------------------


def test_a_sale_is_reported_with_its_basis_rate_and_a_positive_amount() -> None:
    lines = m.aggregate([sale("3", "0.25", "20000.00", "5000.00")])
    assert len(lines) == 1
    assert lines[0].grunnlag == Decimal("20000.00")
    assert lines[0].sats == Decimal("0.25")
    assert lines[0].merverdiavgift == Decimal("5000.00")


def test_a_deduction_is_negative_and_has_no_basis_or_rate() -> None:
    """Skatteetaten's mvakode1.xml: mvaKode and merverdiavgift, nothing else."""
    (line,) = m.aggregate([purchase("1", "0.25", "10000.00", "2500.00")])
    assert line.mva_kode == "1"
    assert line.grunnlag is None
    assert line.sats is None
    assert line.merverdiavgift == Decimal("-2500.00")


def test_a_deductible_import_is_reported_twice_and_nets_to_zero() -> None:
    """mvakode81.xml: an output line with basis and rate, and the same amount back."""
    melding = m.build(
        [purchase("81", "0.25", "10000.00", "2500.00")],
        termin=TERMIN,
        category=Meldingskategori.ALMINNELIG,
        orgnr=ORGNR,
    )
    assert by_code(melding.lines) == [("81", "10000.00", "2500.00"), ("81", None, "-2500.00")]
    assert melding.fastsatt_merverdiavgift == Decimal("0.00")


def test_an_import_without_deduction_is_owed_and_not_reclaimed() -> None:
    melding = m.build(
        [purchase("87", "0.25", "4000.00", "1000.00")],
        termin=TERMIN,
        category=Meldingskategori.ALMINNELIG,
        orgnr=ORGNR,
    )
    assert by_code(melding.lines) == [("87", "4000.00", "1000.00")]
    assert melding.fastsatt_merverdiavgift == Decimal("1000.00")


def test_border_vat_on_a_tollpass_is_a_deduction() -> None:
    """Code 14: the bill was the tax, posted on 2710 with tax information."""
    (line,) = m.aggregate([purchase("14", "0.25", "10000.00", "2500.00")])
    assert (line.mva_kode, line.grunnlag, line.merverdiavgift) == ("14", None, Decimal("-2500.00"))


def test_a_reversed_sale_nets_out_and_leaves_no_line() -> None:
    original = sale("3", "0.25", "1000.00", "250.00")
    assert m.aggregate([original, reversed_(original)]) == ()


def test_a_credit_note_reduces_the_period_it_lands_in() -> None:
    """A kreditnota for an earlier period's sale is a negative basis here."""
    lines = m.aggregate(
        [
            sale("3", "0.25", "8000.00", "2000.00"),
            reversed_(sale("3", "0.25", "1000.00", "250.00")),
        ]
    )
    assert by_code(lines) == [("3", "7000.00", "1750.00")]


def test_lines_at_different_rates_under_one_code_are_kept_apart() -> None:
    """The 2025 water cut: code 31 at one rate and then another, in one period."""
    lines = m.aggregate(
        [sale("31", "0.25", "100.00", "25.00"), sale("31", "0.15", "100.00", "15.00")]
    )
    assert sorted(line.sats or Decimal(0) for line in lines) == [Decimal("0.15"), Decimal("0.25")]


def test_amounts_are_reported_in_ore_not_rounded() -> None:
    (line,) = m.aggregate([sale("3", "0.25", "123.45", "30.86")])
    assert line.grunnlag == Decimal("123.45")
    assert m.format_amount(line.merverdiavgift) == "30.86"


def test_a_whole_period_owes_output_minus_input() -> None:
    melding = m.build(
        [
            sale("3", "0.25", "40000.00", "10000.00"),
            sale("33", "0.12", "1000.00", "120.00"),
            sale("52", "0", "5000.00", "0.00"),
            purchase("1", "0.25", "8000.00", "2000.00"),
            purchase("13", "0.12", "500.00", "60.00"),
        ],
        termin=TERMIN,
        category=Meldingskategori.ALMINNELIG,
        orgnr=ORGNR,
    )
    assert melding.output_vat == Decimal("10120.00")
    assert melding.input_vat == Decimal("2060.00")
    assert melding.fastsatt_merverdiavgift == Decimal("8060.00")
    # Sales first, then deductions, the order the portal shows them in.
    assert [line.mva_kode for line in melding.lines] == ["3", "33", "52", "1", "13"]


def test_a_code_with_no_meaning_in_the_melding_is_refused_rather_than_guessed() -> None:
    with pytest.raises(m.MeldingError, match="99"):
        m.aggregate([sale("99", "0.25", "100.00", "25.00")])


def test_every_seeded_reportable_code_has_a_meaning_in_the_melding() -> None:
    """A code the chart offers and the melding cannot place is a filing that fails."""
    reportable = {
        seed.standard_tax_code
        for seed in STANDARD_TAX_CODES
        if seed.standard_tax_code not in NON_REPORTABLE_STANDARD_TAX_CODES
    }
    assert reportable <= set(MVA_CODES)


def test_the_seed_agrees_with_the_melding_on_which_imports_are_deductible() -> None:
    """Two tables say it; this is the test that they say the same thing."""
    for seed in STANDARD_TAX_CODES:
        if not seed.reverse_charge or seed.rate == 0:
            continue
        assert MVA_CODES[seed.standard_tax_code].deductible is seed.deductible, seed.code


def test_33_is_the_low_rate_and_32_is_fish() -> None:
    seeds = {seed.code: seed for seed in STANDARD_TAX_CODES}
    assert seeds["33"].rate == Decimal("0.120000")
    assert seeds["32"].rate == Decimal("0.111100")


# --- Rates as the code list spells them -------------------------------------------


@pytest.mark.parametrize(
    ("rate", "text"),
    [("0.25", "25"), ("0.15", "15"), ("0.12", "12"), ("0.1111", "11,11"), ("0", "0")],
)
def test_the_rate_is_written_as_a_percentage_with_a_comma(rate: str, text: str) -> None:
    assert m.format_sats(Decimal(rate)) == text


# --- The XML ----------------------------------------------------------------------


def _melding() -> m.Melding:
    return m.build(
        [
            sale("3", "0.25", "20000.00", "5000.00"),
            purchase("1", "0.25", "4000.00", "1000.00"),
            purchase("86", "0.25", "800.00", "200.00"),
            sale("32", "0.1111", "900.00", "99.99"),
        ],
        termin=TERMIN,
        category=Meldingskategori.ALMINNELIG,
        orgnr=ORGNR,
    )


def test_the_melding_says_what_the_lines_say() -> None:
    xml = m.melding_xml(_melding(), reference="ref-1", system_version="9.9", merknad="Hei")
    root = ET.fromstring(xml)  # noqa: S314 - our own output
    assert root.tag == f"{{{m.MELDING_NAMESPACE}}}mvaMeldingDto"
    assert root.findtext("m:innsending/m:regnskapssystemsreferanse", namespaces=NS) == "ref-1"
    assert root.findtext("m:skattepliktig/m:organisasjonsnummer", namespaces=NS) == ORGNR
    assert root.findtext("m:meldingskategori", namespaces=NS) == "alminnelig"
    period = "m:skattegrunnlagOgBeregnetSkatt/m:skattleggingsperiode"
    assert (
        root.findtext(f"{period}/m:periode/m:skattleggingsperiodeToMaaneder", namespaces=NS)
        == "mars-april"
    )
    assert root.findtext(f"{period}/m:aar", namespaces=NS) == "2026"
    assert (
        root.findtext("m:skattegrunnlagOgBeregnetSkatt/m:fastsattMerverdiavgift", namespaces=NS)
        == "4099.99"
    )
    lines = root.findall("m:skattegrunnlagOgBeregnetSkatt/m:mvaSpesifikasjonslinje", NS)
    fish = next(line for line in lines if line.findtext("m:mvaKode", namespaces=NS) == "32")
    assert fish.findtext("m:sats", namespaces=NS) == "11,11"
    deduction = next(line for line in lines if line.findtext("m:mvaKode", namespaces=NS) == "1")
    assert deduction.find("m:grunnlag", NS) is None
    assert deduction.findtext("m:merverdiavgift", namespaces=NS) == "-1000.00"
    assert root.findtext("m:merknad/m:beskrivelse", namespaces=NS) == "Hei"


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint is not installed")
def test_the_melding_is_valid_against_skatteetatens_schema(tmp_path: Path) -> None:
    for merknad in (None, "Forklaring"):
        target = tmp_path / "melding.xml"
        target.write_bytes(
            m.melding_xml(_melding(), reference="ref-2", system_version="1.0", merknad=merknad)
        )
        result = subprocess.run(  # noqa: S603 - fixed arguments, test-only
            ["xmllint", "--noout", "--schema", str(XSD), str(target)],  # noqa: S607
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr


@pytest.mark.skipif(shutil.which("xmllint") is None, reason="xmllint is not installed")
def test_a_nullmelding_is_valid_too(tmp_path: Path) -> None:
    """A period with nothing in it is still filed, with no lines and a zero."""
    empty = m.build([], termin=TERMIN, category=Meldingskategori.ALMINNELIG, orgnr=ORGNR)
    target = tmp_path / "null.xml"
    target.write_bytes(m.melding_xml(empty, reference="ref-3", system_version="1.0"))
    result = subprocess.run(  # noqa: S603
        ["xmllint", "--noout", "--schema", str(XSD), str(target)],  # noqa: S607
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_the_envelope_agrees_with_the_melding_and_lists_its_file() -> None:
    """Altinn compares orgnr, category and the vedlegg list, and answers 409."""
    melding = _melding()
    root = ET.fromstring(  # noqa: S314 - our own output
        m.envelope_xml(
            melding, created_by="Kari Nordmann", created_at=datetime(2026, 5, 2, 9, 0, tzinfo=UTC)
        )
    )
    assert root.tag == "mvaMeldingInnsending"
    assert root.findtext("norskIdentifikator/organisasjonsnummer") == ORGNR
    assert (
        root.findtext("skattleggingsperiode/periode/skattleggingsperiodeToMaaneder") == "mars-april"
    )
    assert root.findtext("meldingskategori") == "alminnelig"
    assert root.findtext("innsendingstype") == "komplett"
    # Optional in the XSD and required by the app.
    assert root.findtext("instansstatus")
    assert root.findtext("opprettetAv") == "Kari Nordmann"
    assert root.findtext("vedlegg/vedleggstype") == "mva-melding"
    assert root.findtext("vedlegg/kildegruppe") == "sluttbrukersystem"
    # The XSD's sequence: opprettetAv before opprettingstidspunkt before vedlegg.
    order = [child.tag for child in root]
    assert order.index("opprettetAv") < order.index("opprettingstidspunkt") < order.index("vedlegg")


# --- Reading the answers ----------------------------------------------------------


def test_a_clean_validation_may_be_submitted() -> None:
    result = m.parse_validation((FIXTURES / "valideringsresultat.xml").read_bytes())
    assert result.status == "ingen avvik"
    assert not result.blocks_submission
    assert not result.has_findings


def test_an_invalid_melding_blocks_and_says_where() -> None:
    answer = f"""<valideringsresultat xmlns="{RESULT_NS}">
      <avvikVedMeldingslevering>ugyldig skattemelding</avvikVedMeldingslevering>
      <avvik>
        <stiTilAvvik>//mvaSpesifikasjonslinje[1]</stiTilAvvik>
        <mvaKode>3</mvaKode>
        <avviksinformasjon>
          <begrunnelse>Grunnlag og sats stemmer ikke med merverdiavgift</begrunnelse>
          <avvikstype>ugyldig skattemelding</avvikstype>
          <avvikKode>R-123</avvikKode>
          <regelDefinisjon>grunnlag * sats</regelDefinisjon>
        </avviksinformasjon>
      </avvik>
    </valideringsresultat>""".encode()
    result = m.parse_validation(answer)
    assert result.blocks_submission
    (finding,) = result.avvik
    assert finding.mva_kode == "3"
    assert finding.kode == "R-123"
    assert "Grunnlag" in finding.melding


def test_the_older_shape_the_app_answers_with_is_read_too() -> None:
    answer = b"""<valideringsresultat>
      <status>UGYLDIG_SKATTEMELDING</status>
      <valideringsfeil>
        <stiTilFeil>//innsending</stiTilFeil>
        <valideringsDetaljer>
          <feilmelding>Mva meldingen m\xc3\xa5 v\xc3\xa6re p\xc3\xa5 gyldig format</feilmelding>
          <alvorlighetsgrad>UGYLDIG_SKATTEMELDING</alvorlighetsgrad>
          <avvikKode>MvaMeldingsinnhold_Xml_SkjemaValideringsfeil</avvikKode>
        </valideringsDetaljer>
      </valideringsfeil>
    </valideringsresultat>"""
    result = m.parse_validation(answer)
    assert result.blocks_submission
    assert result.avvik[0].sti == "//innsending"


def test_an_answer_nobody_can_read_blocks() -> None:
    """Submitting on a verdict nobody saw is not a risk worth an øre."""
    assert m.parse_validation(b"<html>502 Bad Gateway").blocks_submission
    assert m.parse_validation(b"<svar/>").blocks_submission


def test_a_deviating_melding_may_still_be_sent() -> None:
    answer = (
        f'<valideringsresultat xmlns="{RESULT_NS}">'
        "<avvikVedMeldingslevering>avvikende skattemelding</avvikVedMeldingslevering>"
        "</valideringsresultat>"
    ).encode()
    result = m.parse_validation(answer)
    assert not result.blocks_submission
    assert result.has_findings


def test_the_payment_information_gives_kid_amount_due_date_and_account() -> None:
    payment = m.parse_payment((FIXTURES / "betalingsinformasjon.xml").read_bytes())
    assert payment.kid == "99999999999999"
    assert payment.beloep == Decimal("0")
    assert payment.betalingsfrist == date(2021, 5, 21)
    assert payment.kontonummer == "12345678903"


# --- Periods ----------------------------------------------------------------------


def test_two_month_periods_are_named_as_the_schema_spells_them() -> None:
    names = [t.name for t in terminer(PeriodType.TO_MAANEDER, 2026)]
    assert names == [
        "januar-februar",
        "mars-april",
        "mai-juni",
        "juli-august",
        "september-oktober",
        "november-desember",
    ]
    assert Termin(PeriodType.MAANED, 2026, 7).name == "juli"
    assert Termin(PeriodType.AAR, 2026, 1).name == "aarlig"


def test_a_period_covers_whole_months() -> None:
    first = Termin(PeriodType.TO_MAANEDER, 2028, 1)
    assert (first.start, first.end) == (date(2028, 1, 1), date(2028, 2, 29))
    assert termin_for(PeriodType.TO_MAANEDER, date(2026, 4, 30)).index == 2


@pytest.mark.parametrize(
    ("period_type", "index", "category", "due"),
    [
        # 10 April 2026 is a Friday.
        (PeriodType.TO_MAANEDER, 1, Meldingskategori.ALMINNELIG, date(2026, 4, 10)),
        # The one exception in the law: May-June is due on 31 August.
        (PeriodType.TO_MAANEDER, 3, Meldingskategori.ALMINNELIG, date(2026, 8, 31)),
        # 10 October 2026 is a Saturday, so the Monday.
        (PeriodType.TO_MAANEDER, 4, Meldingskategori.ALMINNELIG, date(2026, 10, 12)),
        # The sixth period is due in the following year.
        (PeriodType.TO_MAANEDER, 6, Meldingskategori.ALMINNELIG, date(2027, 2, 10)),
        (PeriodType.MAANED, 12, Meldingskategori.ALMINNELIG, date(2027, 2, 10)),
        (PeriodType.MAANED, 11, Meldingskategori.ALMINNELIG, date(2027, 1, 11)),
        # Yearly: 10 March, and 10 April for primærnæring. 2027-04-10 is a Saturday.
        (PeriodType.AAR, 1, Meldingskategori.ALMINNELIG, date(2027, 3, 10)),
        (PeriodType.AAR, 1, Meldingskategori.PRIMAERNAERING, date(2027, 4, 12)),
    ],
)
def test_deadlines(
    period_type: PeriodType, index: int, category: Meldingskategori, due: date
) -> None:
    assert Termin(period_type, 2026, index).deadline(category) == due


def test_a_period_that_does_not_exist_is_refused() -> None:
    from myntos.domain.mva.periods import UnknownPeriodError

    with pytest.raises(UnknownPeriodError):
        Termin(PeriodType.TO_MAANEDER, 2026, 7)
