"""Printable PDF generators for årsregnskap, styreprotokoll, and generalforsamlingsprotokoll.

Built directly on :mod:`myntos.domain.faktura.pdf`'s zero-dependency PDF 1.4
assembler (WinAnsi encoding for Norwegian letters, exact xref offsets).

Privacy rule (Brønnøysundregistrene RR-0002 guidance): everything attached to
an årsregnskap filing becomes public in Regnskapsregisteret, so these PDFs
never print fødselsnummer, personal addresses, or IP addresses.
"""

from __future__ import annotations

from datetime import date

from myntos.aarsregnskap.compute import ComputedAarsregnskap, StatementLine
from myntos.domain.faktura.pdf import (
    MARGIN,
    PAGE_HEIGHT,
    PAGE_WIDTH,
    _build_pdf,
    _Page,
    truncate,
    wrap,
)
from myntos.domain.ledger.models import Company

CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
RIGHT_X = PAGE_WIDTH - MARGIN


def _fmt_kr(amount: int | None) -> str:
    if amount is None:
        return "-"
    sign = "-" if amount < 0 else ""
    grouped = f"{abs(amount):,}".replace(",", " ")
    return f"{sign}{grouped}"


def _fmt_date(value: date | None) -> str:
    if value is None:
        return "___.___.______"
    return value.strftime("%d.%m.%Y")


def _fmt_orgnr(orgnr: str) -> str:
    digits = "".join(ch for ch in orgnr if ch.isdigit())
    if len(digits) == 9:
        return f"{digits[:3]} {digits[3:6]} {digits[6:]}"
    return orgnr


class _DocWriter:
    """Simple multi-page flow writer over :class:`_Page`."""

    def __init__(self, title: str, subtitle: str) -> None:
        self._title = title
        self._subtitle = subtitle
        self.pages: list[_Page] = []
        self.y = 0.0
        self._new_page(first=True)

    @property
    def page(self) -> _Page:
        return self.pages[-1]

    def _new_page(self, *, first: bool = False) -> None:
        p = _Page()
        self.pages.append(p)
        top = PAGE_HEIGHT - MARGIN
        if first:
            p.text(MARGIN, top, self._title, size=15, bold=True)
            p.text(MARGIN, top - 18, self._subtitle, size=9.5)
            p.line(MARGIN, top - 28, RIGHT_X, width=0.8, grey=0.35)
            self.y = top - 46
        else:
            p.text(MARGIN, top, self._title, size=9, bold=True)
            p.right(RIGHT_X, top, self._subtitle, size=8.5)
            p.line(MARGIN, top - 8, RIGHT_X, width=0.5, grey=0.7)
            self.y = top - 26

    def ensure(self, height: float) -> None:
        if self.y - height < MARGIN + 24:
            self._new_page()

    def heading(self, text: str, *, space_before: float = 10) -> None:
        self.y -= space_before
        self.ensure(24)
        self.page.text(MARGIN, self.y, text, size=11, bold=True)
        self.page.line(MARGIN, self.y - 4, RIGHT_X, width=0.5, grey=0.65)
        self.y -= 18

    def subheading(self, text: str) -> None:
        self.ensure(18)
        self.page.text(MARGIN, self.y, text, size=9.5, bold=True)
        self.y -= 14

    def paragraph(self, text: str, *, size: float = 9, leading: float = 13) -> None:
        for para in text.split("\n"):
            stripped = para.strip()
            if not stripped:
                self.y -= leading * 0.5
                continue
            for line in wrap(stripped, size, CONTENT_WIDTH):
                self.ensure(leading)
                self.page.text(MARGIN, self.y, line, size=size)
                self.y -= leading

    def gap(self, pts: float = 8) -> None:
        self.y -= pts

    def statement_table(
        self,
        title: str,
        lines: tuple[StatementLine, ...],
        year: int,
        has_prior: bool,
    ) -> None:
        self.heading(title)
        self.ensure(20)
        note_x = RIGHT_X - (155 if has_prior else 85)
        curr_x = RIGHT_X - (75 if has_prior else 0)
        prior_x = RIGHT_X

        self.page.text(MARGIN, self.y, "Post (tall i hele kroner)", size=8.5, bold=True)
        self.page.right(note_x, self.y, "Note", size=8.5, bold=True)
        self.page.right(curr_x, self.y, str(year), size=8.5, bold=True)
        if has_prior:
            self.page.right(prior_x, self.y, str(year - 1), size=8.5, bold=True)
        self.page.line(MARGIN, self.y - 4, RIGHT_X, width=0.5, grey=0.5)
        self.y -= 15

        label_limit = note_x - MARGIN - 28
        for row in lines:
            self.ensure(16)
            is_sum = row.kind == "sum"
            if is_sum:
                self.page.line(MARGIN, self.y + 9, RIGHT_X, width=0.35, grey=0.8)
            label = truncate(row.label, 9, label_limit)
            self.page.text(MARGIN, self.y, label, size=9, bold=is_sum)
            if row.note_ref:
                self.page.right(note_x, self.y, row.note_ref, size=8.5)
            self.page.right(curr_x, self.y, _fmt_kr(row.current), size=9, bold=is_sum)
            if has_prior:
                self.page.right(prior_x, self.y, _fmt_kr(row.prior), size=9, bold=is_sum)
            self.y -= 15 if is_sum else 13.5

    def finalize(self) -> bytes:
        total = len(self.pages)
        for idx, p in enumerate(self.pages, start=1):
            p.line(MARGIN, MARGIN + 10, RIGHT_X, width=0.4, grey=0.8)
            p.text(MARGIN, MARGIN, "Utarbeidet i Myntos", size=7.5)
            p.right(RIGHT_X, MARGIN, f"Side {idx} av {total}", size=7.5)
        return _build_pdf(self.pages)


def render_aarsregnskap_pdf(company: Company, computed: ComputedAarsregnskap) -> bytes:
    """Render the full annual accounts (resultatregnskap, balanse, noter) as PDF."""
    subtitle = (
        f"{company.name}  |  Org.nr. {_fmt_orgnr(company.orgnr)}  |  "
        f"Regnskapsår {computed.year} ({_fmt_date(computed.start_date)} - "
        f"{_fmt_date(computed.end_date)})"
    )
    doc = _DocWriter(f"Årsregnskap {computed.year}", subtitle)
    has_prior = computed.has_prior_year_in_books or computed.uses_prior_year_overrides

    doc.statement_table("Resultatregnskap", computed.result_lines, computed.year, has_prior)
    if computed.disposition_lines:
        doc.statement_table(
            "Overføringer og disponeringer",
            computed.disposition_lines,
            computed.year,
            has_prior,
        )

    doc._new_page()
    doc.statement_table(
        f"Balanse per {_fmt_date(computed.end_date)} - Eiendeler",
        computed.balance_asset_lines,
        computed.year,
        has_prior,
    )
    doc.statement_table(
        f"Balanse per {_fmt_date(computed.end_date)} - Egenkapital og gjeld",
        computed.balance_equity_liability_lines,
        computed.year,
        has_prior,
    )
    if computed.off_balance_lines:
        doc.statement_table(
            "Poster utenom balansen",
            computed.off_balance_lines,
            computed.year,
            has_prior,
        )

    # --- Noter ---
    notes = computed.notes
    doc._new_page()
    doc.heading(f"Noter til årsregnskapet for {computed.year}", space_before=0)

    doc.subheading("Note 1 - Regnskapsprinsipper")
    doc.paragraph(notes.regnskapsprinsipper)
    if notes.usikkerhet_fortsatt_drift and notes.usikkerhet_fortsatt_drift_tekst:
        doc.gap(4)
        doc.subheading("Usikkerhet om fortsatt drift")
        doc.paragraph(notes.usikkerhet_fortsatt_drift_tekst)
    doc.gap(8)

    doc.subheading("Note 2 - Lønnskostnader, antall årsverk og pensjon")
    aarsverk_str = str(notes.antall_aarsverk.normalize()).replace(".", ",")
    doc.paragraph(
        f"Selskapet har sysselsatt {aarsverk_str} årsverk i regnskapsåret {computed.year}.\n"
        f"Lønn: kr {_fmt_kr(notes.lonn_aarets)} | "
        f"Arbeidsgiveravgift: kr {_fmt_kr(notes.arbeidsgiveravgift_aarets)} | "
        f"Pensjonskostnader: kr {_fmt_kr(notes.pensjon_aarets)} | "
        f"Andre ytelser: kr {_fmt_kr(notes.andre_ytelser_aarets)} | "
        f"Sum: kr {_fmt_kr(notes.sum_lonnskostnad_aarets)}."
    )
    if notes.lonnskostnad_fritekst:
        doc.paragraph(notes.lonnskostnad_fritekst)
    doc.gap(8)

    if notes.aksjekapital_tekst:
        doc.subheading("Note 3 - Aksjekapital og aksjonærer")
        doc.paragraph(notes.aksjekapital_tekst)
        doc.gap(8)

    fa = notes.anleggsmidler
    if fa.tangible_closing_cost or fa.intangible_closing_cost or notes.anleggsmidler_fritekst:
        doc.subheading("Note 4 - Varige driftsmidler og immaterielle eiendeler")
        if fa.tangible_closing_cost or fa.tangible_carrying_value:
            doc.paragraph(
                f"Varige driftsmidler: Anskaffelseskost 01.01: "
                f"kr {_fmt_kr(fa.tangible_opening_cost)}, "
                f"tilgang: kr {_fmt_kr(fa.tangible_additions)}, "
                f"avgang: kr {_fmt_kr(fa.tangible_disposals)}, "
                f"anskaffelseskost 31.12: kr {_fmt_kr(fa.tangible_closing_cost)}, "
                f"årets avskrivninger: kr {_fmt_kr(fa.tangible_year_depreciation)}, "
                f"balanseført verdi 31.12: kr {_fmt_kr(fa.tangible_carrying_value)}."
            )
        if fa.intangible_closing_cost or fa.intangible_carrying_value:
            doc.paragraph(
                f"Immaterielle eiendeler: Anskaffelseskost 01.01: "
                f"kr {_fmt_kr(fa.intangible_opening_cost)}, "
                f"tilgang: kr {_fmt_kr(fa.intangible_additions)}, "
                f"avgang: kr {_fmt_kr(fa.intangible_disposals)}, "
                f"balanseført verdi 31.12: kr {_fmt_kr(fa.intangible_carrying_value)}."
            )
        if notes.anleggsmidler_fritekst:
            doc.paragraph(notes.anleggsmidler_fritekst)
        doc.gap(8)

    doc.subheading("Note 5 - Skatt")
    doc.paragraph(notes.skatt_tekst)
    doc.gap(8)

    doc.subheading("Note 6 - Egenkapital")
    doc.paragraph(
        f"Egenkapital 01.01.{computed.year}: kr {_fmt_kr(notes.egenkapital_ib)}\n"
        f"Årsresultat: kr {_fmt_kr(notes.egenkapital_aarsresultat)}\n"
        f"Avsatt utbytte: kr {_fmt_kr(notes.egenkapital_utbytte)}\n"
        f"Andre egenkapitalendringer: kr {_fmt_kr(notes.egenkapital_andre_endringer)}\n"
        f"Egenkapital 31.12.{computed.year}: kr {_fmt_kr(notes.egenkapital_ub)}"
    )
    doc.gap(8)

    if (
        notes.bundne_bankinnskudd
        or notes.gjeld_over_fem_aar
        or notes.pantstillelser_gjeld
        or notes.garantiforpliktelser
        or notes.gjeld_fritekst
        or notes.laan_til_styret
        or notes.laan_til_daglig_leder
        or notes.laan_fritekst
    ):
        doc.subheading("Note 7 - Bundne midler, gjeld, pantstillelser og nærstående")
        if notes.bundne_bankinnskudd:
            doc.paragraph(f"Bundne skattetrekksmidler: kr {_fmt_kr(notes.bundne_bankinnskudd)}.")
        if notes.gjeld_over_fem_aar:
            doc.paragraph(
                "Gjeld som forfaller mer enn fem år etter balansedagen: "
                f"kr {_fmt_kr(notes.gjeld_over_fem_aar)}."
            )
        if notes.pantstillelser_gjeld:
            doc.paragraph(
                f"Gjeld sikret ved pant: kr {_fmt_kr(notes.pantstillelser_gjeld)} "
                "(balanseført verdi av pantsatte eiendeler: "
                f"kr {_fmt_kr(notes.pantsatte_eiendeler_verdi)})."
            )
        if notes.garantiforpliktelser:
            doc.paragraph(
                f"Garantiforpliktelser utenom balansen: kr {_fmt_kr(notes.garantiforpliktelser)}."
            )
        if notes.gjeld_fritekst:
            doc.paragraph(notes.gjeld_fritekst)
        if notes.laan_til_styret or notes.laan_til_daglig_leder:
            doc.paragraph(
                f"Lån til styremedlemmer: kr {_fmt_kr(notes.laan_til_styret)}. "
                "Lån til daglig leder / øvrige ledende personer: "
                f"kr {_fmt_kr(notes.laan_til_daglig_leder)}."
            )
        if notes.laan_fritekst:
            doc.paragraph(notes.laan_fritekst)
        doc.gap(8)

    for idx, item in enumerate(notes.egendefinerte_noter, start=8):
        doc.subheading(f"Note {idx} - {item.get('tittel') or 'Tilleggsnote'}")
        doc.paragraph(item.get("tekst") or "")
        doc.gap(8)

    if notes.ytterligere_opplysninger:
        doc.subheading("Ytterligere opplysninger")
        doc.paragraph(notes.ytterligere_opplysninger)
        doc.gap(8)

    # Signature lines for the board (regnskapsloven § 3-5).
    doc.gap(12)
    doc.ensure(90)
    place = computed.draft.board_place or "Sted"
    meeting_date = _fmt_date(computed.draft.board_meeting_date or computed.draft.approval_date)
    doc.paragraph(f"{place}, {meeting_date}", size=9.5)
    doc.paragraph(f"Styret i {company.name}", size=9.5)
    doc.gap(16)

    members = computed.draft.board_members or (
        {"name": computed.draft.confirming_representative or "Styreleder", "role": "Styreleder"},
    )
    for member in members:
        doc.ensure(38)
        doc.page.line(MARGIN, doc.y, MARGIN + 220, width=0.5, grey=0.35)
        doc.y -= 12
        doc.page.text(
            MARGIN,
            doc.y,
            f"{member.get('name', '')} ({member.get('role', 'Styremedlem')})",
            size=9,
        )
        doc.y -= 24

    return doc.finalize()


def render_styreprotokoll_pdf(company: Company, computed: ComputedAarsregnskap) -> bytes:
    """Render a board meeting minutes template (styreprotokoll, aksjeloven § 6-29)."""
    draft = computed.draft
    subtitle = f"{company.name}  |  Org.nr. {_fmt_orgnr(company.orgnr)}"
    doc = _DocWriter(f"Styreprotokoll - Årsregnskap {computed.year}", subtitle)

    place = draft.board_place or "_______________"
    m_date = _fmt_date(draft.board_meeting_date)
    doc.paragraph(
        f"Dato: {m_date}\n"
        f"Sted: {place}\n"
        f"Selskap: {company.name} (org.nr. {_fmt_orgnr(company.orgnr)})"
    )
    doc.gap(6)

    doc.heading("1. Tilstede")
    if draft.board_members:
        for member in draft.board_members:
            doc.paragraph(f"- {member.get('name', '')}, {member.get('role', 'Styremedlem')}")
    else:
        doc.paragraph(
            f"- {draft.confirming_representative or 'Styrets medlemmer'} "
            "(fyll inn styremedlemmer i utkastet)"
        )

    doc.heading("2. Godkjenning av innkalling og dagsorden")
    doc.paragraph(
        "Styret bekreftet at innkalling og dagsorden var mottatt og godkjent, og at "
        "styret var beslutningsdyktig etter aksjeloven § 6-24."
    )

    doc.heading(f"3. Behandling av årsregnskap for {computed.year}")
    doc.paragraph(
        f"Styret gjennomgikk forslag til årsregnskap for {computed.year} "
        f"({_fmt_date(computed.start_date)} - {_fmt_date(computed.end_date)}):\n"
        f"- Sum driftsinntekter: kr {_fmt_kr(computed.driftsinntekter)}\n"
        f"- Driftsresultat: kr {_fmt_kr(computed.driftsresultat)}\n"
        f"- Resultat før skattekostnad: kr {_fmt_kr(computed.resultat_for_skatt)}\n"
        f"- Skattekostnad: kr {_fmt_kr(computed.skattekostnad)}\n"
        f"- Årsresultat: kr {_fmt_kr(computed.aarsresultat)}\n"
        f"- Sum eiendeler per 31.12.{computed.year}: kr {_fmt_kr(computed.sum_eiendeler)}\n"
        f"- Sum egenkapital per 31.12.{computed.year}: kr {_fmt_kr(computed.sum_egenkapital)}"
    )
    doc.gap(4)
    if computed.notes.usikkerhet_fortsatt_drift:
        doc.paragraph(
            "Fortsatt drift: Styret har vurdert forutsetningen om fortsatt drift og viser til "
            "redegjørelse i note 1 om usikkerhet knyttet til fortsatt drift."
        )
    else:
        doc.paragraph(
            "Fortsatt drift: Styret bekrefter at forutsetningen om fortsatt drift er til stede "
            "(regnskapsloven § 3-3a), og at årsregnskapet gir et rettvisende bilde av "
            "selskapets eiendeler, gjeld, finansielle stilling og resultat."
        )
    if draft.small_company:
        doc.paragraph(
            "Selskapet er et lite foretak etter regnskapsloven § 1-6 og er unntatt fra kravet "
            "om årsberetning."
        )

    doc.heading("4. Forslag til disponering av årsresultat")
    div = int(draft.dividend_proposed)
    if computed.aarsresultat >= 0:
        to_eq = computed.aarsresultat - div
        doc.paragraph(
            f"Styret foreslår overfor generalforsamlingen at årsresultatet på kr "
            f"{_fmt_kr(computed.aarsresultat)} disponeres slik:\n"
            f"- Avsatt til ordinært utbytte: kr {_fmt_kr(div)}\n"
            f"- Overført til annen egenkapital / dekning av udekket tap: kr {_fmt_kr(to_eq)}"
        )
    else:
        doc.paragraph(
            f"Styret foreslår overfor generalforsamlingen at årsunderskuddet på kr "
            f"{_fmt_kr(abs(computed.aarsresultat))} dekkes av annen egenkapital / overføres "
            "til udekket tap."
        )

    doc.heading("5. Vedtak")
    doc.paragraph(
        f"Enstemmig vedtak: Styret vedtar årsregnskapet for {computed.year} med noter og "
        "innstiller forslaget til disponering overfor ordinær generalforsamling."
    )
    doc.gap(16)

    members = draft.board_members or (
        {"name": draft.confirming_representative or "Styreleder", "role": "Styreleder"},
    )
    for member in members:
        doc.ensure(38)
        doc.page.line(MARGIN, doc.y, MARGIN + 220, width=0.5, grey=0.35)
        doc.y -= 12
        doc.page.text(
            MARGIN,
            doc.y,
            f"{member.get('name', '')} ({member.get('role', 'Styremedlem')})",
            size=9,
        )
        doc.y -= 24

    return doc.finalize()


def render_generalforsamling_pdf(company: Company, computed: ComputedAarsregnskap) -> bytes:
    """Render an ordinary general meeting minutes template (aksjeloven § 5-5 / § 5-16)."""
    draft = computed.draft
    subtitle = f"{company.name}  |  Org.nr. {_fmt_orgnr(company.orgnr)}"
    doc = _DocWriter(f"Protokoll fra ordinær generalforsamling {computed.year + 1}", subtitle)

    place = draft.general_meeting_place or draft.board_place or "_______________"
    gm_date = _fmt_date(draft.general_meeting_date or draft.approval_date)
    chair = draft.general_meeting_chair or draft.confirming_representative or "_______________"
    cosigner = draft.general_meeting_cosigner or "_______________"

    doc.paragraph(
        f"Dato: {gm_date}\n"
        f"Sted: {place}\n"
        f"Selskap: {company.name} (org.nr. {_fmt_orgnr(company.orgnr)})"
    )
    doc.gap(6)

    doc.heading("1. Åpning og fortegnelse over møtende aksjonærer")
    doc.paragraph(
        "Generalforsamlingen ble åpnet. Det ble opprettet fortegnelse over fremmøtte "
        "aksjonærer (aksjeloven § 5-13):"
    )
    if computed.notes.aksjonaerer:
        for sh in computed.notes.aksjonaerer:
            pct = f"{sh.ownership_percent:.2f}".replace(".", ",")
            doc.paragraph(f"- {sh.name}: {sh.shares} aksjer ({pct} %)")
    else:
        doc.paragraph("- Samtlige aksjer i selskapet var representert.")

    doc.heading("2. Valg av møteleder og medundertegner")
    doc.paragraph(
        f"Til møteleder ble valgt {chair}. Til å medundertegne protokollen ble valgt {cosigner}."
    )

    doc.heading("3. Godkjenning av innkalling og dagsorden")
    doc.paragraph(
        "Innkalling og dagsorden ble enstemmig godkjent. Generalforsamlingen ble erklært "
        "lovlig satt."
    )

    doc.heading(f"4. Fastsettelse av årsregnskap for {computed.year} og disponering av resultat")
    doc.paragraph(
        f"Styrets forslag til årsregnskap for {computed.year} ble gjennomgått.\n"
        f"- Årsresultat: kr {_fmt_kr(computed.aarsresultat)}\n"
        f"- Sum eiendeler: kr {_fmt_kr(computed.sum_eiendeler)}\n"
        f"- Sum egenkapital: kr {_fmt_kr(computed.sum_egenkapital)}"
    )
    doc.gap(4)
    div = int(draft.dividend_proposed)
    if computed.aarsresultat >= 0:
        to_eq = computed.aarsresultat - div
        doc.paragraph(
            f"Vedtak: Generalforsamlingen fastsetter årsregnskapet for {computed.year} slik det "
            "er fremlagt av styret. Årsresultatet disponeres i samsvar med styrets forslag "
            f"(utbytte kr {_fmt_kr(div)}, overført annen egenkapital / dekning av udekket tap "
            f"kr {_fmt_kr(to_eq)})."
        )
    else:
        doc.paragraph(
            f"Vedtak: Generalforsamlingen fastsetter årsregnskapet for {computed.year} slik det "
            "er fremlagt av styret. Årsunderskuddet dekkes av annen egenkapital / føres mot "
            "udekket tap i samsvar med styrets forslag."
        )

    doc.heading("5. Revisjon")
    if draft.auditor_elected_out:
        doc.paragraph(
            "Selskapets årsregnskap skal ikke revideres i samsvar med tidligere vedtak om "
            "fravalg av revisjon (aksjeloven § 7-6)."
        )
    else:
        doc.paragraph("Revisjonsberetning ble fremlagt og godtgjørelse til revisor ble godkjent.")

    doc.heading("6. Avslutning")
    doc.paragraph("Ingen ytterligere saker forelå til behandling. Generalforsamlingen ble hevet.")
    doc.gap(20)

    doc.ensure(60)
    doc.page.line(MARGIN, doc.y, MARGIN + 210, width=0.5, grey=0.35)
    doc.page.line(MARGIN + 250, doc.y, MARGIN + 460, width=0.5, grey=0.35)
    doc.y -= 12
    doc.page.text(MARGIN, doc.y, f"{chair} (Møteleder)", size=9)
    doc.page.text(MARGIN + 250, doc.y, f"{cosigner} (Medundertegner)", size=9)

    return doc.finalize()
