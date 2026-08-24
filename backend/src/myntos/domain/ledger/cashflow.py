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
    # The account the net amount is booked to. Usually a P&L account; for the
    # two equity types it is a balance-sheet account, because an owner's
    # deposit or withdrawal is not income or cost and must not reach the
    # næringsspesifikasjon as either.
    account_number: str
    default_tax_code: str
    note: str

    @property
    def is_income_statement(self) -> bool:
        """False for owner deposits and withdrawals."""
        return not self.account_number.startswith("2")


_TYPES: tuple[CashFlowType, ...] = (
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
        default_tax_code="32",
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
    """Fail at import if a type points at an account or VAT code we never seed.

    A typo here would otherwise surface as a 500 the first time a user picks
    that particular category — long after the deployment that introduced it.
    """
    for entry in _TYPES:
        if entry.account_number not in ACCOUNTS_BY_NUMBER:
            raise AssertionError(f"{entry.code}: konto {entry.account_number} finnes ikke i chart")
        if entry.default_tax_code not in TAX_CODES_BY_CODE:
            raise AssertionError(f"{entry.code}: mva-kode {entry.default_tax_code} finnes ikke")


_validate_catalogue()
