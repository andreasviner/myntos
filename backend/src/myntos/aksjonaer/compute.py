"""Deterministic year computation and pre-flight compliance checks for RF-1086.

Pure functions over loaded ORM/data structures: no database queries, no network.
Given a company's share classes, shareholders, opening holdings, and events,
this module computes the exact opening (01.01) and closing (31.12) state of
every share class and every shareholder for a chosen income year, plus every
blocking error (``feil``) and advisory note (``merknad``) before sending to
Skatteetaten.

Every monetary calculation uses :class:`decimal.Decimal` end to end (CLAUDE.md
rule 4).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from myntos.aksjonaer.codes import (
    CONTINUITY_ANY_ID_REQUIRED,
    CONTINUITY_FNR_REQUIRED,
    OVERDRAGELSE_SUBTYPES,
    EventLineRole,
    EventType,
    ShareholderKind,
    validate_country_code,
    validate_foreign_id,
    validate_national_id,
    validate_shareholder_orgnr,
)
from myntos.aksjonaer.errors import InvalidAksjonaerDataError
from myntos.aksjonaer.models import ShareClass, ShareEvent, ShareEventLine, Shareholder
from myntos.domain.arsoppgjor.findings import Finding, Severity
from myntos.domain.ledger.models import Company

ZERO = Decimal("0.00")
ZERO_PAR = Decimal("0")
TWO_DEC = Decimal("0.01")
SIX_DEC = Decimal("0.000001")

# Organisation forms that file RF-1086 (aksjeloven / allmennaksjeloven).
SUPPORTED_FORMS = frozenset({"AS", "ASA"})


@dataclass(frozen=True)
class ClassSnapshot:
    """Point-in-time state of one share class."""

    shares: int
    par_value: Decimal
    share_capital: Decimal
    paid_in_capital: Decimal
    paid_in_premium: Decimal


@dataclass(frozen=True)
class ShareholderYearEvent:
    """One event entry attached to a specific shareholder's Underskjema."""

    event: ShareEvent
    line: ShareEventLine


@dataclass(frozen=True)
class ShareholderYearState:
    """Computed RF-1086-U state for one shareholder in one share class and year."""

    shareholder: Shareholder
    opening_shares: int
    closing_shares: int
    ownership_percent: Decimal
    dividends_total: Decimal
    events: tuple[ShareholderYearEvent, ...]


@dataclass(frozen=True)
class ComputedShareClass:
    """Computed RF-1086 state for one share class and income year."""

    share_class: ShareClass
    year: int
    opening: ClassSnapshot
    closing: ClassSnapshot
    # Company-wide share capital across all share classes (Hovedskjema Post 1).
    company_opening_capital: Decimal
    company_closing_capital: Decimal
    # Events that occurred during ``year`` for this class, with shares-after stamped.
    year_events: tuple[tuple[ShareEvent, int, Decimal], ...]
    # Only shareholders who held > 0 shares at 01.01, > 0 at 31.12, or had an
    # event during the year need an Underskjema (RF-1086-U).
    shareholders: tuple[ShareholderYearState, ...]
    liquidated: bool
    findings: tuple[Finding, ...] = field(default_factory=tuple)

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.FEIL for f in self.findings)


@dataclass(frozen=True)
class ComputedYear:
    """Computed state for all share classes of a company in one income year."""

    year: int
    classes: tuple[ComputedShareClass, ...]
    company_opening_capital: Decimal
    company_closing_capital: Decimal
    ledger_share_capital: Decimal | None
    findings: tuple[Finding, ...]

    @property
    def blocked(self) -> bool:
        return any(f.severity is Severity.FEIL for f in self.findings) or any(
            c.blocked for c in self.classes
        )


def _q2(val: Decimal) -> Decimal:
    return val.quantize(TWO_DEC, rounding=ROUND_HALF_UP)


def _q6(val: Decimal) -> Decimal:
    return val.quantize(SIX_DEC, rounding=ROUND_HALF_UP)


def _apply_class_event(state: ClassSnapshot, event: ShareEvent) -> tuple[ClassSnapshot, bool]:
    """Advance ``state`` by one corporate event; returns ``(new_state, liquidated)``."""
    etype = EventType(event.event_type)
    shares = state.shares
    par = state.par_value
    capital = state.share_capital
    paid_in = state.paid_in_capital
    premium = state.paid_in_premium
    liquidated = False

    if etype in (EventType.STIFTELSE, EventType.NYEMISJON):
        added = int(event.total_shares or 0)
        new_par = event.par_value_after if event.par_value_after is not None else par
        new_shares = shares + added
        added_capital = (
            _q2(event.total_amount)
            if event.total_amount is not None
            else _q2(Decimal(added) * new_par)
        )
        added_premium = _q2(event.premium_amount) if event.premium_amount is not None else ZERO
        shares = new_shares
        par = new_par
        capital = _q2(capital + added_capital)
        paid_in = _q2(paid_in + added_capital)
        premium = _q2(premium + added_premium)

    elif etype is EventType.SPLITT:
        added = int(event.total_shares or 0)
        shares = shares + added
        if event.par_value_after is not None:
            par = event.par_value_after
        elif shares > 0:
            par = _q6(capital / Decimal(shares))

    elif etype is EventType.SPLEIS:
        removed = int(event.total_shares or 0)
        shares = max(0, shares - removed)
        if event.par_value_after is not None:
            par = event.par_value_after
        elif shares > 0:
            par = _q6(capital / Decimal(shares))

    elif etype is EventType.FONDSEMISJON:
        sub = event.subtype or "nye_aksjer"
        if sub == "okning_palydende":
            inc = _q2(event.total_amount or ZERO)
            capital = _q2(capital + inc)
            if event.par_value_after is not None:
                par = event.par_value_after
            elif shares > 0:
                par = _q6(capital / Decimal(shares))
        else:
            added = int(event.total_shares or 0)
            shares = shares + added
            new_par = event.par_value_after if event.par_value_after is not None else par
            added_cap = (
                _q2(event.total_amount)
                if event.total_amount is not None
                else _q2(Decimal(added) * new_par)
            )
            par = new_par
            capital = _q2(capital + added_cap)
        # Fondsemisjon does not increase skattemessig innbetalt aksjekapital.

    elif etype is EventType.KAPITALFORHOYELSE_PALYDENDE:
        inc = _q2(event.total_amount or ZERO)
        inc_prem = _q2(event.premium_amount or ZERO)
        capital = _q2(capital + inc)
        paid_in = _q2(paid_in + inc)
        premium = _q2(premium + inc_prem)
        if event.par_value_after is not None:
            par = event.par_value_after
        elif shares > 0:
            par = _q6(capital / Decimal(shares))

    elif etype is EventType.KAPITALNEDSETTELSE:
        sub = event.subtype or "utbetaling"
        amt = _q2(event.total_amount or ZERO)
        if sub == "overkurs":
            premium = max(ZERO, _q2(premium - amt))
        elif sub == "tapsdekning":
            capital = max(ZERO, _q2(capital - amt))
            if event.par_value_after is not None:
                par = event.par_value_after
            elif shares > 0:
                par = _q6(capital / Decimal(shares))
        else:
            # Utbetaling til aksjonærene ved reduksjon av pålydende.
            capital = max(ZERO, _q2(capital - amt))
            paid_in = _q2(paid_in - amt)
            if event.par_value_after is not None:
                par = event.par_value_after
            elif shares > 0:
                par = _q6(capital / Decimal(shares))

    elif etype is EventType.LIKVIDASJON:
        # Skatteetaten hard rule: on liquidation, Post 1-6 closing values are 0.
        shares = 0
        par = ZERO_PAR
        capital = ZERO
        paid_in = ZERO
        premium = ZERO
        liquidated = True

    # OVERDRAGELSE and UTBYTTE do not change share capital or share count.
    return (
        ClassSnapshot(
            shares=shares,
            par_value=par,
            share_capital=capital,
            paid_in_capital=paid_in,
            paid_in_premium=premium,
        ),
        liquidated,
    )


def _apply_holder_delta(holdings: dict[int, int], event: ShareEvent) -> None:
    """Update per-shareholder share counts for one event."""
    etype = EventType(event.event_type)
    for line in event.lines:
        hid = line.shareholder_id
        current = holdings.get(hid, 0)
        qty = int(line.shares or 0)
        if etype in (
            EventType.STIFTELSE,
            EventType.NYEMISJON,
            EventType.SPLITT,
        ):
            holdings[hid] = current + qty
        elif etype is EventType.FONDSEMISJON:
            if (event.subtype or "nye_aksjer") == "nye_aksjer":
                holdings[hid] = current + qty
        elif etype in (EventType.SPLEIS, EventType.LIKVIDASJON):
            holdings[hid] = current - qty
        elif etype is EventType.OVERDRAGELSE:
            if line.role == EventLineRole.AVGIVER.value:
                holdings[hid] = current - qty
            else:
                holdings[hid] = current + qty


def _validate_shareholder(holder: Shareholder) -> list[Finding]:
    """Validate one shareholder's identification against Skatteetaten rules."""
    findings: list[Finding] = []
    kind = holder.kind
    if kind == ShareholderKind.PERSON.value:
        if not holder.national_id:
            findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="aksjonaer_mangler_fodselsnummer",
                    message=(
                        f"Aksjonæren «{holder.name}» mangler fødselsnummer eller D-nummer. "
                        "Skatteetaten krever 11 siffer for norske personlige aksjonærer."
                    ),
                )
            )
        else:
            try:
                validate_national_id(holder.national_id)
            except InvalidAksjonaerDataError:
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="aksjonaer_ugyldig_fodselsnummer",
                        message=(
                            f"Aksjonæren «{holder.name}» har et ugyldig fødselsnummer eller "
                            "D-nummer."
                        ),
                    )
                )
    elif kind == ShareholderKind.SELSKAP.value:
        if not holder.orgnr:
            findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="aksjonaer_mangler_orgnr",
                    message=(
                        f"Aksjonæren «{holder.name}» mangler organisasjonsnummer. "
                        "Skatteetaten krever 9 siffer for norske selskaper."
                    ),
                )
            )
        else:
            try:
                validate_shareholder_orgnr(holder.orgnr)
            except InvalidAksjonaerDataError:
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="aksjonaer_ugyldig_orgnr",
                        message=f"Aksjonæren «{holder.name}» har et ugyldig organisasjonsnummer.",
                    )
                )
    else:
        # Foreign shareholder: either valid UTL id, or both name and ISO-2 country code.
        if holder.foreign_id:
            try:
                validate_foreign_id(holder.foreign_id)
            except InvalidAksjonaerDataError:
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="aksjonaer_ugyldig_utenlandsk_id",
                        message=(
                            f"Aksjonæren «{holder.name}» har en ugyldig utenlandsk ID "
                            "(må være UTL etterfulgt av 9 siffer)."
                        ),
                    )
                )
        else:
            if not holder.name.strip():
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="aksjonaer_mangler_navn",
                        message="En utenlandsk aksjonær uten UTL-ID må ha navn og landkode.",
                    )
                )
            try:
                validate_country_code(holder.country_code or "")
            except InvalidAksjonaerDataError:
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="aksjonaer_mangler_landkode",
                        message=(
                            f"Utenlandsk aksjonær «{holder.name}» mangler gyldig landkode "
                            "(to bokstaver, for eksempel SE eller US)."
                        ),
                    )
                )
    return findings


def _validate_event(event: ShareEvent) -> list[Finding]:
    """Check one event against Skatteetaten's RF-1086 business rules."""
    findings: list[Finding] = []
    etype = EventType(event.event_type)

    if etype is EventType.OVERDRAGELSE:
        bought = sum(
            int(line.shares or 0)
            for line in event.lines
            if line.role == EventLineRole.MOTTAKER.value
        )
        sold = sum(
            int(line.shares or 0)
            for line in event.lines
            if line.role == EventLineRole.AVGIVER.value
        )
        if bought <= 0 or sold <= 0 or bought != sold:
            findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="overdragelse_ubalansert",
                    message=(
                        f"Overdragelsen {event.event_date.isoformat()} har {bought} aksjer inn "
                        f"og {sold} aksjer ut. Kjøpt og solgt antall må være likt."
                    ),
                )
            )
        sub = event.subtype or "salg"
        in_code, out_code, _ = OVERDRAGELSE_SUBTYPES.get(sub, ("K", "R", "Overdragelse"))
        for line in event.lines:
            code = out_code if line.role == EventLineRole.AVGIVER.value else in_code
            has_fnr = bool(line.counterparty_national_id)
            has_org = bool(line.counterparty_orgnr)
            if has_fnr and has_org:
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="kontinuitet_begge_id",
                        message=(
                            f"Overdragelsen {event.event_date.isoformat()} har både fødselsnummer "
                            "og org.nr. på motparten. Fyll bare ut ett av feltene."
                        ),
                    )
                )
            if code in CONTINUITY_FNR_REQUIRED and not has_fnr:
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="kontinuitet_mangler_fnr",
                        message=(
                            f"Overdragelsen {event.event_date.isoformat()} ({code}) krever "
                            "fødselsnummer på avgiver/mottaker."
                        ),
                    )
                )
            elif code in CONTINUITY_ANY_ID_REQUIRED and not (has_fnr or has_org):
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="kontinuitet_mangler_id",
                        message=(
                            f"Overdragelsen {event.event_date.isoformat()} ({code}) krever "
                            "enten fødselsnummer eller org.nr. på avgiver/mottaker."
                        ),
                    )
                )

    elif etype is EventType.UTBYTTE:
        total = _q2(event.total_amount or ZERO)
        line_sum = _q2(sum((line.amount or ZERO for line in event.lines), ZERO))
        if total <= ZERO:
            findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="utbytte_null",
                    message=f"Utbyttet {event.event_date.isoformat()} må ha et beløp større enn 0.",
                )
            )
        elif line_sum != total:
            findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="utbytte_sum_ulik",
                    message=(
                        f"Utbyttet {event.event_date.isoformat()} har samlet beløp {total} kr, "
                        f"men summen fordelt på aksjonærene er {line_sum} kr."
                    ),
                )
            )
        for line in event.lines:
            has_wt_amt = line.withholding_tax is not None
            has_wt_rate = line.withholding_tax_rate is not None
            has_wt_country = bool(line.withholding_tax_country)
            if any((has_wt_amt, has_wt_rate, has_wt_country)) and not all(
                (has_wt_amt, has_wt_rate, has_wt_country)
            ):
                findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="kildeskatt_ufullstendig",
                        message=(
                            f"Utbyttet {event.event_date.isoformat()} mangler kildeskatt i kroner, "
                            "prosent eller landkode. Enten fylles alle tre ut, eller ingen."
                        ),
                    )
                )

    elif etype in (EventType.STIFTELSE, EventType.NYEMISJON):
        total_new = int(event.total_shares or 0)
        alloc = sum(int(line.shares or 0) for line in event.lines)
        if total_new <= 0 or alloc != total_new:
            findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="emisjon_aksjer_ulik",
                    message=(
                        f"Hendelsen {event.event_date.isoformat()} har {total_new} nye aksjer, "
                        f"men {alloc} fordelt på aksjonærene."
                    ),
                )
            )
    return findings


def compute_year(
    *,
    company: Company,
    year: int,
    share_classes: Sequence[ShareClass],
    shareholders: Sequence[Shareholder],
    contact_email: str | None = None,
    ledger_share_capital: Decimal | None = None,
) -> ComputedYear:
    """Compute opening/closing RF-1086 state and pre-flight checks for ``year``."""
    top_findings: list[Finding] = []
    form = (company.organisation_form or "").strip().upper()
    if form not in SUPPORTED_FORMS:
        top_findings.append(
            Finding(
                severity=Severity.FEIL,
                code="organisasjonsform_ikke_stottet",
                message=(
                    f"Aksjonærregisteroppgaven (RF-1086) gjelder aksjeselskap (AS/ASA), "
                    f"ikke «{company.organisation_form or 'ukjent'}»."
                ),
            )
        )

    if not share_classes:
        top_findings.append(
            Finding(
                severity=Severity.FEIL,
                code="ingen_aksjeklasse",
                message=(
                    "Selskapet har ingen aksjeklasse registrert ennå. Legg til aksjeklassen "
                    "(vanligvis 01 Ordinære aksjer) eller hent fra fjorårets oppgave."
                ),
            )
        )

    effective_email = (contact_email or company.email or "").strip()
    if not effective_email:
        top_findings.append(
            Finding(
                severity=Severity.MERKNAD,
                code="mangler_kontakt_epost",
                message=(
                    "Ingen kontakt-e-post er satt. Oppgi en e-postadresse før innsending slik "
                    "at Skatteetaten kan sende kvittering og eventuelle tilbakemeldinger."
                ),
            )
        )

    holder_by_id = {h.id: h for h in shareholders}

    # First pass: compute opening & closing snapshots for every share class so
    # Post 1 (AksjekapitalForHeleSelskapet) sums across all classes accurately.
    per_class_raw: list[
        tuple[
            ShareClass,
            ClassSnapshot,
            ClassSnapshot,
            list[tuple[ShareEvent, int, Decimal]],
            dict[int, int],
            dict[int, int],
            bool,
        ]
    ] = []

    year_start = date(year, 1, 1)
    year_end = date(year, 12, 31)

    for sc in share_classes:
        base_state = ClassSnapshot(
            shares=int(sc.opening_shares),
            par_value=sc.opening_par_value,
            share_capital=_q2(sc.opening_share_capital),
            paid_in_capital=_q2(sc.opening_paid_in_capital),
            paid_in_premium=_q2(sc.opening_paid_in_premium),
        )
        # Opening holdings apply as of end of opening_year (or initial state if None).
        base_holdings: dict[int, int] = {
            oh.shareholder_id: int(oh.shares) for oh in sc.opening_holdings if oh.shares
        }

        ordered_events = sorted(sc.events, key=lambda e: (e.event_date, e.id))
        # Events strictly after opening_year (when set) and before year_start advance opening.
        after_opening_date = date(sc.opening_year, 12, 31) if sc.opening_year is not None else None

        opening_state = base_state
        opening_holdings = dict(base_holdings)
        for ev in ordered_events:
            if after_opening_date is not None and ev.event_date <= after_opening_date:
                continue
            if ev.event_date < year_start:
                opening_state, _ = _apply_class_event(opening_state, ev)
                _apply_holder_delta(opening_holdings, ev)

        closing_state = opening_state
        closing_holdings = dict(opening_holdings)
        year_events_stamped: list[tuple[ShareEvent, int, Decimal]] = []
        liquidated = False

        for ev in ordered_events:
            if year_start <= ev.event_date <= year_end:
                closing_state, ev_liq = _apply_class_event(closing_state, ev)
                _apply_holder_delta(closing_holdings, ev)
                year_events_stamped.append((ev, closing_state.shares, closing_state.par_value))
                if ev_liq:
                    liquidated = True

        per_class_raw.append(
            (
                sc,
                opening_state,
                closing_state,
                year_events_stamped,
                opening_holdings,
                closing_holdings,
                liquidated,
            )
        )

    company_open_cap = _q2(sum((item[1].share_capital for item in per_class_raw), ZERO))
    company_close_cap = _q2(sum((item[2].share_capital for item in per_class_raw), ZERO))

    # Cross-check against ledger account 2000 when the company has postings for the year.
    if ledger_share_capital is not None and share_classes:
        ledger_cap = _q2(ledger_share_capital)
        if ledger_cap != company_close_cap:
            top_findings.append(
                Finding(
                    severity=Severity.MERKNAD,
                    code="aksjekapital_avviker_fra_regnskap",
                    message=(
                        f"Aksjekapitalen i aksjeeierboken ({company_close_cap} kr) avviker fra "
                        f"konto 2000 i regnskapet ({ledger_cap} kr) ved utgangen av {year}. "
                        "Kontroller at eventuelle kapitalendringer er registrert begge steder."
                    ),
                )
            )

    computed_classes: list[ComputedShareClass] = []
    for (
        sc,
        opening_state,
        closing_state,
        year_events_stamped,
        opening_holdings,
        closing_holdings,
        liquidated,
    ) in per_class_raw:
        class_findings: list[Finding] = []

        # Collect per-shareholder events in ``year``.
        holder_events: dict[int, list[ShareholderYearEvent]] = {}
        for ev, _, _ in year_events_stamped:
            class_findings.extend(_validate_event(ev))
            for line in ev.lines:
                holder_events.setdefault(line.shareholder_id, []).append(
                    ShareholderYearEvent(event=ev, line=line)
                )

        # Determine every shareholder who needs an Underskjema (RF-1086-U) for this year.
        involved_ids = sorted(
            {hid for hid, cnt in opening_holdings.items() if cnt != 0}
            | {hid for hid, cnt in closing_holdings.items() if cnt != 0}
            | set(holder_events.keys())
        )

        holder_states: list[ShareholderYearState] = []
        for hid in involved_ids:
            holder = holder_by_id.get(hid)
            if holder is None:
                continue
            o_cnt = opening_holdings.get(hid, 0)
            c_cnt = closing_holdings.get(hid, 0)
            if o_cnt < 0 or c_cnt < 0:
                class_findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="negativ_beholdning",
                        message=(
                            f"Aksjonæren «{holder.name}» har negativ aksjebeholdning "
                            f"({o_cnt} ved årets start, {c_cnt} ved årets slutt)."
                        ),
                    )
                )
            pct = (
                _q2((Decimal(c_cnt) * Decimal("100")) / Decimal(closing_state.shares))
                if closing_state.shares > 0
                else ZERO
            )
            evs = tuple(holder_events.get(hid, ()))
            div_total = _q2(
                sum(
                    (
                        (se.line.amount or ZERO)
                        for se in evs
                        if se.event.event_type == EventType.UTBYTTE.value
                    ),
                    ZERO,
                )
            )
            class_findings.extend(_validate_shareholder(holder))
            holder_states.append(
                ShareholderYearState(
                    shareholder=holder,
                    opening_shares=o_cnt,
                    closing_shares=c_cnt,
                    ownership_percent=pct,
                    dividends_total=div_total,
                    events=evs,
                )
            )

        if not holder_states:
            class_findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="ingen_aksjonaerer",
                    message=(
                        f"Aksjeklasse {sc.code} ({sc.name}) har ingen aksjonærer i {year}. "
                        "Skatteetaten krever minst ett underskjema ved innsending."
                    ),
                )
            )

        sum_open = sum(h.opening_shares for h in holder_states)
        if sum_open != opening_state.shares:
            class_findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="inngaende_aksjer_ulik",
                    message=(
                        f"Aksjeklasse {sc.code} har {opening_state.shares} aksjer 01.01.{year}, "
                        f"men summen på aksjonærene er {sum_open}."
                    ),
                )
            )

        sum_close = sum(h.closing_shares for h in holder_states)
        if sum_close != closing_state.shares:
            class_findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="utgaende_aksjer_ulik",
                    message=(
                        f"Aksjeklasse {sc.code} har {closing_state.shares} aksjer 31.12.{year}, "
                        f"men summen på aksjonærene er {sum_close}."
                    ),
                )
            )

        if not liquidated and closing_state.shares > 0:
            expected_cap = _q2(Decimal(closing_state.shares) * closing_state.par_value)
            if abs(expected_cap - closing_state.share_capital) > Decimal("0.05"):
                class_findings.append(
                    Finding(
                        severity=Severity.FEIL,
                        code="aksjekapital_stemmer_ikke",
                        message=(
                            f"Aksjeklasse {sc.code}: antall aksjer ({closing_state.shares}) ganger "
                            f"pålydende ({closing_state.par_value}) gir {expected_cap} kr, men "
                            f"aksjekapitalen er {closing_state.share_capital} kr."
                        ),
                    )
                )

        if liquidated and (
            closing_state.shares != 0
            or closing_state.share_capital != ZERO
            or closing_state.paid_in_capital != ZERO
            or closing_state.paid_in_premium != ZERO
        ):
            class_findings.append(
                Finding(
                    severity=Severity.FEIL,
                    code="likvidasjon_ikke_null",
                    message=(
                        "Ved likvidasjon av selskapet må utgående aksjekapital og aksjer være 0."
                    ),
                )
            )

        computed_classes.append(
            ComputedShareClass(
                share_class=sc,
                year=year,
                opening=opening_state,
                closing=closing_state,
                company_opening_capital=company_open_cap,
                company_closing_capital=company_close_cap,
                year_events=tuple(year_events_stamped),
                shareholders=tuple(holder_states),
                liquidated=liquidated,
                findings=tuple(class_findings),
            )
        )

    return ComputedYear(
        year=year,
        classes=tuple(computed_classes),
        company_opening_capital=company_open_cap,
        company_closing_capital=company_close_cap,
        ledger_share_capital=ledger_share_capital,
        findings=tuple(top_findings),
    )
