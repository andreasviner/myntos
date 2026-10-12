"""The starter chart of accounts and VAT-code table for a new company.

Pure data. :mod:`myntos.domain.ledger.service` turns it into rows; nothing here
touches a database, so the whole table is inspectable in a test and in a REPL.

Two things are deliberately modest:

**The chart is a starting point, not a complete kontoplan.** It holds the
accounts the cash-flow register actually posts to (:mod:`.cashflow`) plus the
handful the double entry needs on the other side — bank, cash, the two VAT
accounts, equity. A business with payroll, inventory or fixed assets will add
accounts; the point of seeding is that a brand-new company can record its first
payment without first being asked to build a kontoplan.

**The næringsspesifikasjon mapping is checked against the published list.**
Since SAF-T v1.30 every account carries a ``GroupingCategory``/``GroupingCode``
pair instead of the old ``StandardAccountID``, and the code lists are
**year-versioned** by Skatteetaten (``github.com/Skatteetaten/saf-t``, folder
"Grouping Category Code 2025-2026", vendored as
:mod:`myntos.saft.codes`). Every pair below is in that list, and a test says so.

It was not always. The first version of this table followed the documented
*shape* without the list at hand, and half the pairs did not exist: the payroll
category was spelt ``lonnskostnad`` where the list says ``loennskostnad``, 4000
pointed at a post 4000 that is 4005, and every balance-sheet account said
``NA`` although the list has a post for each of them. ``NA`` is for accounts
that genuinely have no post; using it for a bank account hides the account from
anyone reading the file by category. Migration ``b7d40e2c9a15`` rewrote the rows
already seeded. When the list for a new year is published, check this table
against it before that year is exported, not after.

**This table is the seed, not the kontoplan.** Since the kontoplan became
editable (:mod:`.kontoplan`) a company may hold accounts that are not here, and
groupings a person chose rather than the ones below. Nothing may assume an
account's number is in :data:`ACCOUNTS_BY_NUMBER`, and a migration correcting
seeded groupings must leave rows with ``chosen_at`` set alone.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from myntos.domain.ledger.models import (
    GROUPING_NOT_APPLICABLE,
    NON_REPORTABLE_STANDARD_TAX_CODES,
    AccountType,
)

# Accounts the posting logic refers to by name rather than by user choice.
BANK_ACCOUNT = "1920"
CASH_ACCOUNT = "1900"
OUTPUT_VAT_ACCOUNT = "2700"
INPUT_VAT_ACCOUNT = "2710"

# SAF-T BalanceAccountStructure: the GL account a party's sub-ledger settles
# against. Required on every customer and supplier, which is why the two are
# named here rather than chosen per party.
RECEIVABLES_ACCOUNT = "1500"
PAYABLES_ACCOUNT = "2400"

# The journal every cash-flow entry lands in. SAF-T wants entries grouped in
# journals; one is enough until there is more than one source of vouchers.
CASH_JOURNAL_ID = "KONTANT"
CASH_JOURNAL_DESCRIPTION = "Inn- og utbetalinger"
CASH_JOURNAL_TYPE = "bank"

# Issued sales documents land in their own journal rather than in the cash one.
# They are a different event: an invoice is revenue and a receivable on the day
# it is issued, while a payment is money moving, and the two are weeks apart. A
# shared journal would also make the bilagsnummer series interleave two sources.
SALES_JOURNAL_ID = "SALG"
SALES_JOURNAL_DESCRIPTION = "Utgående fakturaer"
SALES_JOURNAL_TYPE = "salg"

# Where a filed MVA-melding is settled: the period's 2700 and 2710 moved onto
# 2740 (myntos.domain.mva.service.post_settlement). A journal of its own because
# it is neither money moving nor a sale, and because the year's VAT figures have
# to be able to leave it out: summed in, a settled period reads as owing nothing
# while the amount still sits on 2740 waiting to be paid.
SETTLEMENT_JOURNAL_ID = "MVA"
SETTLEMENT_JOURNAL_DESCRIPTION = "Mva-oppgjør"
SETTLEMENT_JOURNAL_TYPE = "mva"

# The opening balance of a company whose earlier years were kept elsewhere
# (myntos.domain.arsoppgjor.opening). A journal of its own for two reasons. The
# SAF-T export reads it as the year's *opening* balance rather than as the
# year's movements, which is the only way the file's closing balance can equal
# its opening plus its transactions. And the register's year figures leave it
# out: the bank balance carried in from the old system is not money that came in
# this year.
OPENING_JOURNAL_ID = "IB"
OPENING_JOURNAL_DESCRIPTION = "Inngående balanse"
OPENING_JOURNAL_TYPE = "ib"

# Årsavslutning: the tax cost and the disposition of the year's result
# (myntos.domain.arsoppgjor.closing). Its own journal so that a reopened year's
# closing vouchers, and their reversals, read as one event in the export.
CLOSING_JOURNAL_ID = "AVS"
CLOSING_JOURNAL_DESCRIPTION = "Årsavslutning"
CLOSING_JOURNAL_TYPE = "avs"

# VAT code meaning "no VAT treatment" — the default for wages, interest,
# insurance and everything else outside the VAT system. Excluded from the
# MVA-melding by NON_REPORTABLE_STANDARD_TAX_CODES.
NO_VAT_CODE = "0"


@dataclass(frozen=True)
class SeedAccount:
    """One general ledger account, with its per-year grouping mapping."""

    number: str
    description: str
    account_type: AccountType
    # Balance-sheet accounts have no næringsspesifikasjon income post; SAF-T
    # prescribes "NA" rather than an empty element.
    grouping_category: str = GROUPING_NOT_APPLICABLE
    grouping_code: str = GROUPING_NOT_APPLICABLE


@dataclass(frozen=True)
class SeedTaxCode:
    """One internal VAT code, its standard code, and the rate it starts at."""

    code: str
    description: str
    standard_tax_code: str
    rate: Decimal
    # True for innførsel and for services bought abroad, where the VAT is not
    # *in* the amount on the invoice but computed on top of it.
    #
    # The arithmetic is genuinely different, which is why this is a flag and not
    # a rate. An ordinary 25 % purchase of 27 108,49 kroner is 21 686,79 of cost
    # and 5 421,70 of VAT carved out of the total the supplier charged. An
    # import of the same value is 27 108,49 of cost — the foreign seller charged
    # no Norwegian VAT — plus 6 777,12 the buyer both owes and (when deductible)
    # reclaims, posted to 2700 and 2710 so they cancel. Treating the second as
    # the first understates the cost by a fifth and invents a deduction nobody
    # was charged.
    reverse_charge: bool = False
    # Whether the buyer may reclaim what they just charged themselves. False for
    # imports serving something outside the VAT area, where the amount is owed
    # and not recovered, so only the 2700 side is posted.
    deductible: bool = True
    # True for codes 14 and 15: VAT paid at the border, usually on a bill from
    # the freight forwarder or Tolletaten. The whole amount on that bill *is*
    # the VAT. The goods themselves were paid to the foreign seller on another
    # voucher, so there is no basis to carve the VAT out of, and splitting the
    # bill as though it were an ordinary purchase would book four fifths of a
    # tax payment as a cost and deduct only the remaining fifth.
    vat_only: bool = False

    @property
    def is_reportable(self) -> bool:
        """False for the codes that must never reach the MVA-melding."""
        return self.standard_tax_code not in NON_REPORTABLE_STANDARD_TAX_CODES


# NS 4102-style numbering, trimmed to what the cash-flow register uses.
STANDARD_ACCOUNTS: tuple[SeedAccount, ...] = (
    # --- Balance sheet ---
    # Anskaffelser that are not costs. Over the beløpsgrense in skatteloven
    # § 14-40 a purchase with three years' life or more is an asset that is
    # written down over time, not an expense in the year it was bought, and
    # until this account existed the register had nowhere to put one.
    SeedAccount(
        "1250",
        "Inventar og utstyr",
        AccountType.EIENDEL,
        "balanseverdiForAnleggsmiddel",
        "1205",
    ),
    SeedAccount(
        "1500", "Kundefordringer", AccountType.EIENDEL, "balanseverdiForOmloepsmiddel", "1500"
    ),
    SeedAccount("1900", "Kontanter", AccountType.EIENDEL, "balanseverdiForOmloepsmiddel", "1900"),
    SeedAccount(
        "1920", "Bankinnskudd", AccountType.EIENDEL, "balanseverdiForOmloepsmiddel", "1920"
    ),
    # The share capital of an AS, which an opening balance brought in from
    # another system always carries and which nothing in the register ever
    # posts to. Without it the only place to put an aksjekapital of 30 000 was
    # 2050, which would let the year-end disposition cover a loss out of money
    # the company is not allowed to pay out.
    SeedAccount("2000", "Aksjekapital", AccountType.EGENKAPITAL, "egenkapital", "2000"),
    # "Positiv egenkapital". A company whose equity has gone negative belongs on
    # 2080 instead, and a static mapping cannot know which; the export check
    # says so when the closing balance is on the wrong side.
    SeedAccount("2050", "Annen egenkapital", AccountType.EGENKAPITAL, "egenkapital", "2050"),
    # The list has no post for an owner's drawings, because in the
    # næringsspesifikasjon they are not a balance of their own: they reduce
    # equity, which is what this account is closed against at year end.
    SeedAccount("2060", "Privatkonto", AccountType.EGENKAPITAL, "egenkapital", "2050"),
    # Udekket tap: the part of an AS's loss that the free equity on 2050 could
    # not cover. Kept apart from 2050 rather than letting 2050 go negative,
    # because the balance sheet has to show it as a deduction of its own, and
    # because a later year's profit covers it before anything reaches 2050.
    SeedAccount("2080", "Udekket tap", AccountType.EGENKAPITAL, "egenkapital", "2080"),
    SeedAccount(
        "2220", "Gjeld til kredittinstitusjoner", AccountType.GJELD, "langsiktigGjeld", "2220"
    ),
    SeedAccount("2400", "Leverandørgjeld", AccountType.GJELD, "kortsiktigGjeld", "2400"),
    # Withheld tax is not the employer's money and never was. It sits here from
    # the day the salary is booked until it is paid on, which is what makes the
    # payment a settlement of a debt rather than a second payroll cost.
    # The tax an AS owes on the year's profit, booked at årsavslutning and paid
    # the year after. "Ikke fastsatt" because at 31 December it is still the
    # company's own estimate.
    SeedAccount(
        "2500", "Betalbar skatt, ikke fastsatt", AccountType.GJELD, "kortsiktigGjeld", "2500"
    ),
    SeedAccount("2600", "Forskuddstrekk", AccountType.GJELD, "kortsiktigGjeld", "2600"),
    # All three VAT accounts map to "Skyldig merverdiavgift", input VAT included
    # although it is an asset here: the næringsspesifikasjon asks for the net
    # amount owed, and Skatteetaten's own example file maps 2700 and 2710 the
    # same way.
    SeedAccount("2700", "Utgående merverdiavgift", AccountType.GJELD, "kortsiktigGjeld", "2740"),
    SeedAccount("2710", "Inngående merverdiavgift", AccountType.EIENDEL, "kortsiktigGjeld", "2740"),
    # Where a filed MVA-melding goes to be paid or refunded. Without it the
    # register had nowhere to put the payment, and the only categories that fit
    # were a cost (inventing a deduction on tax already deducted) or a private
    # withdrawal (making the company's VAT the owner's spending money).
    SeedAccount(
        "2740", "Oppgjørskonto merverdiavgift", AccountType.GJELD, "kortsiktigGjeld", "2740"
    ),
    # --- Income ---
    SeedAccount(
        "3000", "Salgsinntekt, avgiftspliktig høy sats", AccountType.INNTEKT, "salgsinntekt", "3000"
    ),
    SeedAccount(
        "3010",
        "Salgsinntekt, avgiftspliktig middels sats",
        AccountType.INNTEKT,
        "salgsinntekt",
        "3000",
    ),
    SeedAccount(
        "3020", "Salgsinntekt, avgiftspliktig lav sats", AccountType.INNTEKT, "salgsinntekt", "3000"
    ),
    SeedAccount(
        "3100",
        "Salgsinntekt, utenfor merverdiavgiftsloven",
        AccountType.INNTEKT,
        "salgsinntekt",
        "3100",
    ),
    SeedAccount(
        "3120", "Salgsinntekt, utførsel (avgiftsfri)", AccountType.INNTEKT, "salgsinntekt", "3100"
    ),
    SeedAccount("3900", "Annen driftsinntekt", AccountType.INNTEKT, "annenDriftsinntekt", "3900"),
    SeedAccount("8050", "Renteinntekt", AccountType.INNTEKT, "finansinntekt", "8050"),
    # A post of its own ("Gevinst ved kursendring på valuta"). An earlier
    # version grouped it under 8050 on the belief that the list had no such
    # post; it does, and interest and currency gains are separate lines in the
    # næringsspesifikasjon.
    SeedAccount("8060", "Valutagevinst", AccountType.INNTEKT, "finansinntekt", "8060"),
    # --- Expenses ---
    # 4005, not 4000: the list has no post 4000, and 4005 is "Varekostnader
    # inklusiv endring i beholdningen", which is what Skatteetaten's own example
    # file maps 4000 to.
    SeedAccount("4000", "Varekjøp", AccountType.KOSTNAD, "varekostnad", "4005"),
    SeedAccount("5000", "Lønn til ansatte", AccountType.KOSTNAD, "loennskostnad", "5000"),
    SeedAccount("5400", "Arbeidsgiveravgift", AccountType.KOSTNAD, "loennskostnad", "5400"),
    SeedAccount("6300", "Leie av lokaler", AccountType.KOSTNAD, "annenDriftskostnad", "6300"),
    SeedAccount("6340", "Lys, varme og strøm", AccountType.KOSTNAD, "annenDriftskostnad", "6340"),
    SeedAccount("6500", "Verktøy og inventar", AccountType.KOSTNAD, "annenDriftskostnad", "6500"),
    SeedAccount(
        "6700",
        "Fremmed tjeneste (regnskap, revisjon, juridisk)",
        AccountType.KOSTNAD,
        "annenDriftskostnad",
        "6700",
    ),
    # 6995 is "Kontorrekvisita, elektronisk kommunikasjon, porto med mer": one
    # post for office supplies and telephone both, so 6800 and 6900 share it.
    SeedAccount("6800", "Kontorrekvisita", AccountType.KOSTNAD, "annenDriftskostnad", "6995"),
    SeedAccount(
        "6810", "Datakostnad og programvare", AccountType.KOSTNAD, "annenDriftskostnad", "6350"
    ),
    SeedAccount("6900", "Telefon og internett", AccountType.KOSTNAD, "annenDriftskostnad", "6995"),
    SeedAccount(
        "7000", "Drivstoff og bilkostnad", AccountType.KOSTNAD, "annenDriftskostnad", "7000"
    ),
    # 7165 is travel *without* opplysningsplikt. Travel paid to employees as
    # allowances is 7155 and belongs on an account of its own when payroll
    # exists; what the register books here is tickets and hotels.
    SeedAccount("7140", "Reisekostnad", AccountType.KOSTNAD, "annenDriftskostnad", "7165"),
    SeedAccount(
        "7320", "Reklame og markedsføring", AccountType.KOSTNAD, "annenDriftskostnad", "7330"
    ),
    SeedAccount("7500", "Forsikringspremie", AccountType.KOSTNAD, "annenDriftskostnad", "7500"),
    SeedAccount("7770", "Bank- og kortgebyr", AccountType.KOSTNAD, "annenDriftskostnad", "7700"),
    SeedAccount("7790", "Annen driftskostnad", AccountType.KOSTNAD, "annenDriftskostnad", "7700"),
    # Where a customer's unpaid invoice goes when it is given up on, and where
    # the MVA-melding's «tap på krav» takes its basis from
    # (myntos.domain.mva.adjustments). A company seeded before it existed gets
    # it the first time it records one, from this row.
    SeedAccount("7830", "Tap på fordringer", AccountType.KOSTNAD, "annenDriftskostnad", "7830"),
    SeedAccount("8150", "Rentekostnad", AccountType.KOSTNAD, "finanskostnad", "8150"),
    # Its own post, "Tap ved kursendring på valuta"; see 8060.
    SeedAccount("8160", "Valutatap", AccountType.KOSTNAD, "finanskostnad", "8160"),
    # --- Tax and disposition (årsavslutning) ---
    # Only an AS books this: an enkeltpersonforetak is not a taxpayer, its owner
    # is, and the owner's tax is not a cost of the business.
    SeedAccount("8300", "Betalbar skatt", AccountType.KOSTNAD, "skattekostnad", "8300"),
    # The three disposition accounts. Typed as P&L, because that is where they
    # sit: they are the lines that bring the result to zero, which is what lets
    # the next year open its income and costs at nothing while the balance
    # sheet carries the result on equity. All three share Skatteetaten's one
    # post for it, "Disponering av årets overskudd/dekning av årets underskudd".
    #
    # 8800 is the ENK's, 8960 and 8990 the AS's: an enkeltpersonforetak has no
    # disposition to decide, the result simply is the owner's, while an AS
    # transfers to annen egenkapital and carries what that cannot cover as
    # udekket tap.
    SeedAccount(
        "8800",
        "Årsresultat overført egenkapital",
        AccountType.KOSTNAD,
        "resultatDisponeringForSAF-T",
        "8800",
    ),
    SeedAccount(
        "8960",
        "Overføringer annen egenkapital",
        AccountType.KOSTNAD,
        "resultatDisponeringForSAF-T",
        "8800",
    ),
    SeedAccount("8990", "Udekket tap", AccountType.KOSTNAD, "resultatDisponeringForSAF-T", "8800"),
)

ACCOUNTS_BY_NUMBER: dict[str, SeedAccount] = {a.number: a for a in STANDARD_ACCOUNTS}

# Internal code == standard code. Keeping them equal costs nothing and removes
# a translation step that would otherwise be wrong in exactly one place.
# Rates as of 2026 (docs/integrations/mva.md); they are seeded date-effective
# from the company's first financial year, never as constants.
#
# Every code and description here is checked against Skatteetaten's own code
# list, ``kodelister/mvaKodeSAFT.xml`` in github.com/Skatteetaten/mva-meldingen,
# and not against memory. That check is why 33 is the 12 % sales code: the
# table used to call 32 "lav sats (12 %)", and 32 is fish at 11,11 %. Every
# 12 % sale booked under it would have reached the MVA-melding as a fish sale
# whose VAT did not match its rate, and failed validation with a message about
# grunnlag and sats rather than about the code.
STANDARD_TAX_CODES: tuple[SeedTaxCode, ...] = (
    SeedTaxCode("0", "Ingen merverdiavgiftsbehandling", "0", Decimal("0.000000")),
    SeedTaxCode("1", "Inngående mva, høy sats (25 %)", "1", Decimal("0.250000")),
    SeedTaxCode("11", "Inngående mva, middels sats (15 %)", "11", Decimal("0.150000")),
    SeedTaxCode(
        "12", "Inngående mva, kjøp av fisk og marine ressurser (11,11 %)", "12", Decimal("0.111100")
    ),
    SeedTaxCode("13", "Inngående mva, lav sats (12 %)", "13", Decimal("0.120000")),
    # --- Mva betalt ved innførsel (tollpass) ---
    SeedTaxCode(
        "14",
        "Mva betalt ved innførsel, høy sats (25 %)",
        "14",
        Decimal("0.250000"),
        vat_only=True,
    ),
    SeedTaxCode(
        "15",
        "Mva betalt ved innførsel, middels sats (15 %)",
        "15",
        Decimal("0.150000"),
        vat_only=True,
    ),
    SeedTaxCode("3", "Utgående mva, høy sats (25 %)", "3", Decimal("0.250000")),
    SeedTaxCode("31", "Utgående mva, middels sats (15 %)", "31", Decimal("0.150000")),
    SeedTaxCode(
        "32", "Utgående mva, salg av fisk og marine ressurser (11,11 %)", "32", Decimal("0.111100")
    ),
    SeedTaxCode("33", "Utgående mva, lav sats (12 %)", "33", Decimal("0.120000")),
    SeedTaxCode("5", "Avgiftsfri omsetning (0 %)", "5", Decimal("0.000000")),
    SeedTaxCode("6", "Omsetning utenfor merverdiavgiftsloven", "6", Decimal("0.000000")),
    # Domestic reverse charge, seller's side: klimakvoter and investment gold
    # sold to a business, which then accounts for the VAT itself under 91/92.
    SeedTaxCode(
        "51", "Salg av klimakvoter og gull til næringsdrivende (0 %)", "51", Decimal("0.000000")
    ),
    SeedTaxCode("52", "Utførsel av varer og tjenester (0 %)", "52", Decimal("0.000000")),
    # --- Innførsel og kjøp fra utlandet ---
    #
    # A Norwegian business importing goods accounts for the VAT itself rather
    # than paying it at the border (merverdiavgiftsloven § 11-1 second
    # paragraph), and the same reverse charge applies to services bought from
    # abroad. Both sides go in the MVA-melding, so a deductible import nets to
    # zero and is still reported twice.
    #
    # Without these the only honest code for an imported bilag was 0, which
    # keeps the books truthful about the invoice and silently drops the import
    # from the VAT return entirely.
    SeedTaxCode(
        "81",
        "Innførsel av varer, høy sats (25 %), med fradragsrett",
        "81",
        Decimal("0.250000"),
        reverse_charge=True,
    ),
    SeedTaxCode(
        "82",
        "Innførsel av varer, høy sats (25 %), uten fradragsrett",
        "82",
        Decimal("0.250000"),
        reverse_charge=True,
        deductible=False,
    ),
    SeedTaxCode(
        "83",
        "Innførsel av varer, middels sats (15 %), med fradragsrett",
        "83",
        Decimal("0.150000"),
        reverse_charge=True,
    ),
    SeedTaxCode(
        "84",
        "Innførsel av varer, middels sats (15 %), uten fradragsrett",
        "84",
        Decimal("0.150000"),
        reverse_charge=True,
        deductible=False,
    ),
    # Imported goods on which no VAT is calculated at all (books, for one). Still
    # reported, with its basis, which is why it is a code and not a 0.
    SeedTaxCode(
        "85",
        "Innførsel av varer som det ikke skal beregnes mva av (0 %)",
        "85",
        Decimal("0.000000"),
        reverse_charge=True,
    ),
    SeedTaxCode(
        "86",
        "Tjenester kjøpt fra utlandet, høy sats (25 %), med fradragsrett",
        "86",
        Decimal("0.250000"),
        reverse_charge=True,
    ),
    SeedTaxCode(
        "87",
        "Tjenester kjøpt fra utlandet, høy sats (25 %), uten fradragsrett",
        "87",
        Decimal("0.250000"),
        reverse_charge=True,
        deductible=False,
    ),
    SeedTaxCode(
        "88",
        "Tjenester kjøpt fra utlandet, lav sats (12 %), med fradragsrett",
        "88",
        Decimal("0.120000"),
        reverse_charge=True,
    ),
    SeedTaxCode(
        "89",
        "Tjenester kjøpt fra utlandet, lav sats (12 %), uten fradragsrett",
        "89",
        Decimal("0.120000"),
        reverse_charge=True,
        deductible=False,
    ),
    # The buyer's side of 51: the business that buys klimakvoter or gold
    # calculates the VAT itself, exactly like an import.
    SeedTaxCode(
        "91",
        "Kjøp av klimakvoter og gull, høy sats (25 %), med fradragsrett",
        "91",
        Decimal("0.250000"),
        reverse_charge=True,
    ),
    SeedTaxCode(
        "92",
        "Kjøp av klimakvoter og gull, høy sats (25 %), uten fradragsrett",
        "92",
        Decimal("0.250000"),
        reverse_charge=True,
        deductible=False,
    ),
)

TAX_CODES_BY_CODE: dict[str, SeedTaxCode] = {t.code: t for t in STANDARD_TAX_CODES}
