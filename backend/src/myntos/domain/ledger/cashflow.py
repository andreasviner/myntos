"""Cash-flow categories — the "type" the user picks when money moves.

A payment is not self-describing. The same 12 500 kroner leaving the bank
account is a deductible cost, a private withdrawal or a VAT settlement
depending on *what it was for*, and the difference is exactly what the
skattemelding and the næringsspesifikasjon are built from. So the register asks
for one thing the bank statement cannot tell it: the type.

Each type is a triple of decisions that would otherwise fall on the user:

1. **Which account** the amount is booked to — and through that account, which
   næringsspesifikasjon post it lands in (see :mod:`.chart` on the per-year
   ``GroupingCategory``/``GroupingCode`` mapping).
2. **Which VAT code** applies by default, which is what the MVA-melding
   aggregates over (``GROUP BY (mvaKode, spesifikasjon, sats)``).
3. **Which direction** money moves, so the double entry is built the right way
   round.

The default VAT code is a default, not a verdict. Rented premises may or may
not carry VAT depending on whether the landlord is voluntarily registered; a
car expense is deductible for a van and not for a passenger car; a purchase
abroad is reverse-charged. Every type therefore states its assumption in
``note`` and the caller may override the code — the UI shows the note next to
the choice. Guessing silently is what turns a bookkeeping tool into a liability.

This module is pure data and pure functions. The posting itself lives in
:mod:`myntos.domain.ledger.service`.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from myntos.domain.ledger.chart import ACCOUNTS_BY_NUMBER, NO_VAT_CODE, TAX_CODES_BY_CODE
from myntos.domain.ledger.errors import UnknownCashFlowTypeError


class CashFlowDirection(enum.StrEnum):
    """Which way the money moved, from the business's point of view."""

    INN = "inn"
    UT = "ut"


@dataclass(frozen=True)
class CashFlowType:
    """One selectable category of payment."""

    code: str
    label: str
    direction: CashFlowDirection
    # The account the net amount is booked to. Usually a P&L account, and a
    # balance-sheet one wherever money moves without anything being earned or
    # spent: an owner's deposit or withdrawal, a driftsmiddel bought or sold, a
    # loan drawn or repaid, a filed mva-melding settled, withheld tax paid on.
    # None of those is income or cost, and none may reach the
    # næringsspesifikasjon as either.
    account_number: str
    default_tax_code: str
    note: str
    #: The category this one reverses, when it is a refund or a kreditnota.
    #:
    #: A refund is money in that is not income: the supplier sent back what was
    #: paid, so the cost goes down and the VAT deduction on it goes back with it.
    #: A kreditnota is the mirror, money out that is not a cost: the customer got
    #: their money back, so the turnover goes down and the output VAT charged on
    #: it goes back with it.
    #:
    #: Naming the category it reverses is what lets the posting land on the same
    #: account the original did, and what ``books_as`` reads to pick 2700 over
    #: 2710. Between them that is the difference between reducing a deduction and
    #: owing tax on a sale that never happened.
    reverses: str | None = None

    @property
    def is_refund(self) -> bool:
        """Money moving back rather than money earned or spent."""
        return self.reverses is not None

    @property
    def books_as(self) -> CashFlowDirection:
        """The direction the *trade* went, which a reversal flips back.

        A refund is money in on a purchase and a kreditnota is money out on a
        sale, so on both of them ``direction`` says which way the bank moved and
        nothing about which side of the books the amount belongs to. This says
        that, and it is what picks 2700 over 2710.

        Getting it from the direction alone was right only while a sale could
        not be reversed: ``is_incoming and not is_refund`` put a kreditnota's
        VAT on 2710, which claims a deduction on a sale that was cancelled
        rather than giving back the tax that was charged on it.
        """
        if not self.is_refund:
            return self.direction
        return (
            CashFlowDirection.UT
            if self.direction is CashFlowDirection.INN
            else CashFlowDirection.INN
        )

    @property
    def is_income_statement(self) -> bool:
        """False for anything that lands on the balance sheet.

        Classes 1 and 2 in NS 4102 are the balance sheet and 3 upwards is the
        income statement, so both prefixes belong here. It used to test only for
        "2", which was correct while the two equity types were the only
        non-P&L categories and became wrong the moment a purchase could be
        capitalised to 1250: a driftsmiddel would have been counted as a cost in
        the very figures capitalising it is meant to keep it out of.
        """
        return not self.account_number.startswith(("1", "2"))


#: The categories written out by hand. The reversals below are derived from
#: these, and never from each other: a refund of a kreditnota is a sale, which
#: the catalogue already has.
_BASE: tuple[CashFlowType, ...] = (
    # --- Money in ---
    CashFlowType(
        code="salg_hoy_sats",
        label="Salg med 25 % mva",
        direction=CashFlowDirection.INN,
        account_number="3000",
        default_tax_code="3",
        note="Vanlig avgiftspliktig salg av varer og tjenester.",
    ),
    CashFlowType(
        code="salg_middels_sats",
        label="Salg med 15 % mva",
        direction=CashFlowDirection.INN,
        account_number="3010",
        default_tax_code="31",
        note="Næringsmidler, og vann og avløp fra 1. juli 2025.",
    ),
    CashFlowType(
        code="salg_lav_sats",
        label="Salg med 12 % mva",
        direction=CashFlowDirection.INN,
        account_number="3020",
        default_tax_code="33",
        note="Persontransport, overnatting, kino, museer og idrettsarrangement.",
    ),
    CashFlowType(
        code="salg_utenfor_loven",
        label="Salg uten mva",
        direction=CashFlowDirection.INN,
        account_number="3100",
        default_tax_code="6",
        note=(
            "Omsetning utenfor merverdiavgiftsloven, for eksempel helsetjenester "
            "og undervisning. Er du ikke mva-registrert, er det denne du skal bruke."
        ),
    ),
    CashFlowType(
        code="salg_eksport",
        label="Salg til utlandet (utførsel)",
        direction=CashFlowDirection.INN,
        account_number="3120",
        default_tax_code="52",
        note="Avgiftsfri utførsel av varer og tjenester. Skal rapporteres i mva-meldingen.",
    ),
    CashFlowType(
        code="salg_driftsmiddel",
        label="Salg av driftsmiddel",
        direction=CashFlowDirection.INN,
        # The same balance-sheet account the purchase went to. Selling used
        # equipment is not turnover: the money in is the asset leaving, and
        # booked to 3000 it would count as revenue while the thing sold stayed
        # on the balance sheet being depreciated.
        account_number="1250",
        default_tax_code="3",
        note=(
            "Salg av utstyr du har aktivert. Beløpet føres mot samme balansekonto (1250), "
            "så saldoen du avskriver går ned. Er salgssummen større enn det som står igjen "
            "på driftsmidlet, er differansen en gevinst som skal inntektsføres ved "
            "årsoppgjøret."
        ),
    ),
    CashFlowType(
        code="annen_inntekt",
        label="Annen inntekt",
        direction=CashFlowDirection.INN,
        account_number="3900",
        default_tax_code=NO_VAT_CODE,
        note="Tilskudd, refusjoner og annet som ikke er ordinært salg. Sjekk mva-koden selv.",
    ),
    CashFlowType(
        code="renteinntekt",
        label="Renteinntekt",
        direction=CashFlowDirection.INN,
        account_number="8050",
        default_tax_code=NO_VAT_CODE,
        note="Finansielle tjenester er unntatt merverdiavgift.",
    ),
    CashFlowType(
        code="valutagevinst",
        label="Valutagevinst",
        direction=CashFlowDirection.INN,
        account_number="8060",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Differansen når betalingen i kroner ble mindre enn fakturaen i utenlandsk "
            "valuta tilsa, fordi kursen endret seg. Dette er en finansinntekt og ikke et "
            "salg, så det skal ikke inn i mva-meldingen."
        ),
    ),
    CashFlowType(
        code="mva_tilgode",
        # Named for the melding rather than for the money, the same way its
        # money-out twin is, and for the same reason.
        label="Utbetaling av mva-melding fra Skatteetaten",
        direction=CashFlowDirection.INN,
        account_number="2740",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Utbetaling fra Skatteetaten når inngående mva var større enn utgående. Dette "
            "er ikke inntekt: meldingen er alt ført, og dette gjør bare opp den."
        ),
    ),
    CashFlowType(
        code="laneopptak",
        label="Nytt lån",
        direction=CashFlowDirection.INN,
        account_number="2220",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Lånte penger inn på konto. Dette er ikke inntekt og skattlegges ikke: beløpet "
            "står som gjeld til det er nedbetalt. Avdragene føres som «Avdrag på lån»."
        ),
    ),
    CashFlowType(
        code="egenkapitalinnskudd",
        label="Innskudd fra eier",
        direction=CashFlowDirection.INN,
        account_number="2050",
        default_tax_code=NO_VAT_CODE,
        note="Egne penger inn i virksomheten. Dette er ikke inntekt og skattlegges ikke.",
    ),
    # --- Money out ---
    CashFlowType(
        code="varekjop",
        label="Varekjøp",
        direction=CashFlowDirection.UT,
        account_number="4000",
        default_tax_code="1",
        note="Varer for videresalg eller innsatsvarer i produksjonen.",
    ),
    CashFlowType(
        code="mva_innforsel",
        # Named for the bill people actually hold. The freight forwarder's
        # "tollpass" or Tolletaten's own bill is the only paper this payment
        # comes with, and "innførselsmerverdiavgift" is not a word anyone types.
        label="Mva betalt ved innførsel (tollpass)",
        direction=CashFlowDirection.UT,
        # Straight to inngående mva. The whole amount is the tax, so nothing
        # reaches the income statement; the goods themselves were a separate
        # payment to the seller abroad.
        account_number="2710",
        default_tax_code="14",
        note=(
            "Merverdiavgift du betalte da varene kom inn i landet, på regningen fra "
            "speditøren eller Tolletaten. Hele beløpet er avgift og føres til fradrag. "
            "Selve varene betalte du til selgeren, og de føres for seg. Er det mat og "
            "drikke, velg kode 15."
        ),
    ),
    CashFlowType(
        code="fremmed_tjeneste",
        label="Regnskap, revisjon og juridisk bistand",
        direction=CashFlowDirection.UT,
        account_number="6700",
        default_tax_code="1",
        note="Kjøpte tjenester fra andre næringsdrivende.",
    ),
    CashFlowType(
        code="lonn",
        label="Lønn til ansatte",
        direction=CashFlowDirection.UT,
        account_number="5000",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Utbetalt lønn. Husk at forskuddstrekk og arbeidsgiveravgift "
            "føres og innrapporteres for seg (a-melding)."
        ),
    ),
    CashFlowType(
        code="arbeidsgiveravgift",
        label="Arbeidsgiveravgift",
        direction=CashFlowDirection.UT,
        account_number="5400",
        default_tax_code=NO_VAT_CODE,
        note="Innbetalt arbeidsgiveravgift.",
    ),
    CashFlowType(
        code="husleie",
        label="Leie av lokaler",
        direction=CashFlowDirection.UT,
        account_number="6300",
        default_tax_code="1",
        note=(
            "Standardvalget forutsetter at utleier er frivillig registrert for mva. "
            "Står det ingen mva på fakturaen, velg «ingen mva»."
        ),
    ),
    CashFlowType(
        code="strom",
        label="Strøm og oppvarming",
        direction=CashFlowDirection.UT,
        account_number="6340",
        default_tax_code="1",
        note="Strøm til næringslokaler.",
    ),
    CashFlowType(
        code="inventar",
        label="Verktøy og inventar",
        direction=CashFlowDirection.UT,
        account_number="6500",
        default_tax_code="1",
        note=(
            "Kjøp under beløpsgrensen, eller med kortere levetid enn tre år. "
            "Større og varige anskaffelser skal aktiveres og avskrives i stedet."
        ),
    ),
    CashFlowType(
        code="driftsmiddel",
        label="Større anskaffelse (driftsmiddel)",
        direction=CashFlowDirection.UT,
        # A balance-sheet account, which is the whole point: the money left, but
        # nothing was consumed. The value sits as an asset and reaches the
        # income statement over the following years as avskrivning.
        account_number="1250",
        default_tax_code="1",
        note=(
            "Kjøp fra 15 000 kroner som skal vare i tre år eller mer: maskiner, utstyr, "
            "inventar. Føres ikke som kostnad nå, men aktiveres og avskrives (skatteloven "
            "§ 14-40). Avskrivningen selv føres ikke her."
        ),
    ),
    CashFlowType(
        code="kontorrekvisita",
        label="Kontorrekvisita",
        direction=CashFlowDirection.UT,
        account_number="6800",
        default_tax_code="1",
        note="Rekvisita og forbruksmateriell til kontoret.",
    ),
    CashFlowType(
        code="datakostnad",
        label="Programvare og datakostnad",
        direction=CashFlowDirection.UT,
        account_number="6810",
        default_tax_code="1",
        note=(
            "Abonnementer og programvare. Utenlandske leverandører fakturerer ofte "
            "uten norsk mva, og da gjelder omvendt avgiftsplikt."
        ),
    ),
    CashFlowType(
        code="telefon_internett",
        label="Telefon og internett",
        direction=CashFlowDirection.UT,
        account_number="6900",
        default_tax_code="1",
        note="Husk at privat bruk skal fordelsbeskattes.",
    ),
    CashFlowType(
        code="bilkostnad",
        label="Drivstoff og bilkostnad",
        direction=CashFlowDirection.UT,
        account_number="7000",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Standardvalget er uten fradrag, fordi personbil ikke gir fradragsrett. "
            "For varebil klasse 2 og lastebil kan du velge inngående mva."
        ),
    ),
    CashFlowType(
        code="reise",
        label="Reise og opphold",
        direction=CashFlowDirection.UT,
        account_number="7140",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Satsene varierer: persontransport og overnatting har 12 %, "
            "servering 25 %, utenlandsreiser ingen norsk mva. Velg koden selv."
        ),
    ),
    CashFlowType(
        code="markedsforing",
        label="Reklame og markedsføring",
        direction=CashFlowDirection.UT,
        account_number="7320",
        default_tax_code="1",
        note="Annonsering, nettsider og profilering.",
    ),
    CashFlowType(
        code="forsikring",
        label="Forsikring",
        direction=CashFlowDirection.UT,
        account_number="7500",
        default_tax_code=NO_VAT_CODE,
        note="Forsikringstjenester er unntatt merverdiavgift.",
    ),
    CashFlowType(
        code="bankgebyr",
        label="Bank- og kortgebyr",
        direction=CashFlowDirection.UT,
        account_number="7770",
        default_tax_code=NO_VAT_CODE,
        note="Finansielle tjenester er unntatt merverdiavgift.",
    ),
    CashFlowType(
        code="rentekostnad",
        label="Renter og lånekostnad",
        direction=CashFlowDirection.UT,
        account_number="8150",
        default_tax_code=NO_VAT_CODE,
        note="Rentedelen av et avdrag. Selve avdraget er nedbetaling av gjeld, ikke kostnad.",
    ),
    CashFlowType(
        code="valutatap",
        label="Valutatap",
        direction=CashFlowDirection.UT,
        account_number="8160",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Differansen når betalingen i kroner ble større enn fakturaen i utenlandsk "
            "valuta tilsa, fordi kursen endret seg. Dette er en finanskostnad og ikke et "
            "kjøp: det gir ingen fradragsrett for mva og skal ikke inn i mva-meldingen."
        ),
    ),
    CashFlowType(
        code="avdrag_lan",
        label="Avdrag på lån",
        direction=CashFlowDirection.UT,
        # Gjeld, not kostnad. The money left, but nothing was consumed: the debt
        # went down. Booked as a cost it would deduct the same money twice, once
        # when the loan bought something and again when it was repaid.
        account_number="2220",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Nedbetaling av selve lånet. Dette er ikke en kostnad og gir ikke fradrag: "
            "gjelden går ned og resultatet er uberørt. Rentedelen av terminbeløpet føres "
            "for seg som «Renter og lånekostnad»."
        ),
    ),
    CashFlowType(
        code="mva_oppgjor",
        # "Betalt merverdiavgift" was the first label and it was a trap. The
        # classifier is shown labels and nothing else, and that one reads as
        # "VAT that was paid", which describes every purchase invoice in the
        # catalogue. Brønnøysundregistrene's registreringsgebyr came back as
        # this category three runs out of three: a government body, 0 % mva on
        # the face of it, and a label that seemed to fit. Booked here the 6 825
        # kroner would land on 2740 and leave the income statement entirely.
        label="Oppgjør av mva-melding til Skatteetaten",
        direction=CashFlowDirection.UT,
        account_number="2740",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Innbetaling av en mva-melding til Skatteetaten. Beløpet er avgift som alt er "
            "ført på 2700 og 2710, ikke en ny kostnad, og skal ikke med i en ny melding."
        ),
    ),
    CashFlowType(
        code="forskuddstrekk",
        label="Innbetalt forskuddstrekk",
        direction=CashFlowDirection.UT,
        account_number="2600",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Skattetrekket du holdt tilbake fra lønnen og nå betaler videre. Kostnaden ble "
            "ført med lønnen, så dette gjør bare opp gjelden. Arbeidsgiveravgiften har sin "
            "egen type."
        ),
    ),
    CashFlowType(
        code="annen_kostnad",
        label="Annen driftskostnad",
        direction=CashFlowDirection.UT,
        account_number="7790",
        default_tax_code="1",
        note="Bruk denne bare når ingen av de andre passer. Den sier lite til Skatteetaten.",
    ),
    CashFlowType(
        code="privatuttak",
        label="Uttak til eier",
        direction=CashFlowDirection.UT,
        account_number="2060",
        default_tax_code=NO_VAT_CODE,
        note=(
            "Penger tatt ut av virksomheten til privat bruk. Dette er ikke en "
            "kostnad og gir ikke fradrag."
        ),
    ),
)

#: Money-out categories that get no refund counterpart, and why.
#:
#: ``privatuttak`` is equity and already has its own money-in twin
#: (``egenkapitalinnskudd``): an owner putting money back in is a deposit, not a
#: refund of a withdrawal. ``avdrag_lan``, ``mva_oppgjor`` and ``valutatap`` are
#: out for the same reason — ``laneopptak``, ``mva_tilgode`` and
#: ``valutagevinst`` are the other side of each of them, written out by hand
#: because none of the three is a *reversal*: money borrowed is not a refund of
#: an instalment, and a krone gained on the exchange rate is not a krone given
#: back.
#:
#: ``lonn``, ``arbeidsgiveravgift`` and ``forskuddstrekk`` are left out on
#: purpose. Money coming back on payroll is almost never a reversal of the cost:
#: refusjon av sykepenger and feriepenger belong on their own income account,
#: and a category that credited 5000 would quietly understate salaries in the
#: a-melding and the næringsspesifikasjon. A real reversal of a wrongly paid
#: salary is a correction of the voucher, which the register already does
#: properly.
_NO_REFUND = frozenset(
    {
        "privatuttak",
        "lonn",
        "arbeidsgiveravgift",
        "forskuddstrekk",
        "avdrag_lan",
        "mva_oppgjor",
        "valutatap",
    }
)

#: Money-in categories that get no kreditnota counterpart, and why.
#:
#: A kreditnota reverses a *sale*, so only the sales rows have one. The rest of
#: the money-in catalogue is either not a sale at all — ``renteinntekt``,
#: ``valutagevinst``, ``egenkapitalinnskudd``, ``laneopptak``, ``mva_tilgode`` —
#: or is one whose reversal already exists under another name:
#: ``salg_driftsmiddel`` cancelled is the asset coming back, which is
#: ``refusjon_driftsmiddel``, and pointing a second category at 1250 would let
#: the same event be booked two ways.
_NO_CREDIT_NOTE = frozenset(
    {
        "renteinntekt",
        "valutagevinst",
        "egenkapitalinnskudd",
        "laneopptak",
        "mva_tilgode",
        "salg_driftsmiddel",
    }
)


def _refund_of(cost: CashFlowType) -> CashFlowType:
    """The money-in twin of one cost category.

    Derived rather than written out, so the account and the VAT code cannot
    drift from the purchase the refund reverses. That drift is the whole risk
    here: a refund booked to a different account than its purchase leaves both
    the cost and the deduction overstated, and nothing in the books says so.
    """
    reverses_vat = cost.default_tax_code != NO_VAT_CODE
    deduction = " Fradraget for inngående mva reverseres i samme føring." if reverses_vat else ""
    return CashFlowType(
        code=f"refusjon_{cost.code}",
        label=f"Refusjon: {cost.label.lower()}",
        direction=CashFlowDirection.INN,
        # The same account the purchase went to, which is what makes this a
        # reduction of that cost rather than income of its own.
        account_number=cost.account_number,
        default_tax_code=cost.default_tax_code,
        note=(
            f"Penger tilbake på noe som ble ført som «{cost.label}». Beløpet føres mot "
            f"samme konto ({cost.account_number}), så kostnaden går ned."
            f"{deduction}"
        ),
        reverses=cost.code,
    )


def _credit_note_of(sale: CashFlowType) -> CashFlowType:
    """The money-out twin of one sales category: a kreditnota to a customer.

    The mirror of :func:`_refund_of`, and derived for the same reason. What it
    adds is the other half of a rule the register could not express: bokføringsloven
    forbids editing a posted sales document, so a cancelled sale is a kreditnota
    that reverses it (see :mod:`myntos.domain.faktura`). Paying the customer back
    had to be recorded here as *something*, and every money-out category on offer
    was a cost — filing a returned sale under "Annen driftskostnad" leaves the
    turnover standing and the output VAT owed on it with it.
    """
    reverses_vat = sale.default_tax_code != NO_VAT_CODE
    tax = " Den utgående mvaen på salget reverseres i samme føring." if reverses_vat else ""
    return CashFlowType(
        code=f"kreditnota_{sale.code}",
        label=f"Kreditnota: {sale.label.lower()}",
        direction=CashFlowDirection.UT,
        account_number=sale.account_number,
        default_tax_code=sale.default_tax_code,
        note=(
            f"Penger tilbake til en kunde på noe som ble ført som «{sale.label}». Beløpet "
            f"føres mot samme konto ({sale.account_number}), så inntekten går ned."
            f"{tax}"
        ),
        reverses=sale.code,
    )


_TYPES: tuple[CashFlowType, ...] = (
    _BASE
    + tuple(
        _refund_of(entry)
        for entry in _BASE
        if entry.direction is CashFlowDirection.UT and entry.code not in _NO_REFUND
    )
    + tuple(
        _credit_note_of(entry)
        for entry in _BASE
        if entry.direction is CashFlowDirection.INN and entry.code not in _NO_CREDIT_NOTE
    )
)

CASH_FLOW_TYPES: dict[str, CashFlowType] = {entry.code: entry for entry in _TYPES}


def get_type(code: str) -> CashFlowType:
    """Look up a cash-flow type, or raise :class:`UnknownCashFlowTypeError`."""
    try:
        return CASH_FLOW_TYPES[code]
    except KeyError as error:
        raise UnknownCashFlowTypeError(f"ukjent type: {code!r}") from error


def types_for(direction: CashFlowDirection | None = None) -> list[CashFlowType]:
    """The catalogue, optionally narrowed to one direction."""
    return [t for t in _TYPES if direction is None or t.direction is direction]


def _validate_catalogue() -> None:
    """Fail at import if a type points at an account, a code or a twin we lack.

    A typo here would otherwise surface as a 500 the first time a user picks
    that particular category — long after the deployment that introduced it.

    The reversal checks are the part worth having. Both families are derived, so
    they hold today by construction; what these refuse is the shortcut of
    hand-writing one later. A reversal that has drifted from what it reverses,
    or that goes the same way as it, posts VAT to the wrong side and reads as an
    ordinary entry while doing it.
    """
    for entry in _TYPES:
        if entry.account_number not in ACCOUNTS_BY_NUMBER:
            raise AssertionError(f"{entry.code}: konto {entry.account_number} finnes ikke i chart")
        if entry.default_tax_code not in TAX_CODES_BY_CODE:
            raise AssertionError(f"{entry.code}: mva-kode {entry.default_tax_code} finnes ikke")

    by_code = {entry.code: entry for entry in _TYPES}
    for entry in _TYPES:
        if entry.reverses is None:
            continue
        original = by_code.get(entry.reverses)
        if original is None:
            raise AssertionError(f"{entry.code}: reverserer ukjent type {entry.reverses}")
        if original.reverses is not None:
            raise AssertionError(f"{entry.code}: reverserer en reversering ({original.code})")
        if original.direction is entry.direction:
            raise AssertionError(f"{entry.code}: går samme vei som {original.code}")
        if entry.account_number != original.account_number:
            raise AssertionError(f"{entry.code}: annen konto enn {original.code}")
        if entry.default_tax_code != original.default_tax_code:
            raise AssertionError(f"{entry.code}: annen mva-kode enn {original.code}")


_validate_catalogue()
