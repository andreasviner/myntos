"""A SAF-T file from the previous program, read into the opening balance form.

:mod:`myntos.saft.reader` says what the file holds; this says what it becomes in
*these* books: which account each balance lands on, which customer or supplier
an open item belongs to, and what this company's kontoplan and party register
are missing before it can be posted. Nothing is stored. The answer is the same
review the saldobalanse upload gives (:func:`myntos.domain.arsoppgjor.opening.review`),
plus the accounts and parties to add, which the page adds through the ordinary
kontoplan and party routes before reading the file again.

Built for the farm pilot (docs/landbruk.md), whose Tripletex file was the first
one read, and written for every company that moves: each rule below is a thing
that file did, and each one is general.

**One company, and the right year.** The file names its company by
organisasjonsnummer, and a file for any other company is refused before
anything in it is shown (CLAUDE.md rule 1). The opening balance belongs to the
company's first year in Myntos, so the file must cover that year (its opening
balances) or the year before it (its closing ones); anything else says nothing
about this balance and is refused.

**Account numbers: the first four digits.** NS 4102 is four digits and so is
Myntos. Tripletex's farm module writes NIBIO's department and product after
them, ``1451`` + ``20`` + ``512`` for cows (``145120512``), and an ordinary
Tripletex file can carry ``1920`` + ``10`` for a second bank account. The four
digits are the account; the rest is kept, as the line's text, so nothing the
accountant coded is lost, and several source accounts on one Myntos account
stay as lines of their own. A source id that does not start with four digits in
classes 1-8 is not guessed at: it stays as it is and the review refuses it.

**Accounts that are not in the kontoplan** are proposed with the file's own
name and grouping, after two checks the export would otherwise fail later: the
pair must be in Skatteetaten's list for the year, and its category must fit the
account's class. Tripletex grouped a driftskreditt (2390) as «Positiv
egenkapital»; a 23xx account is short-term debt in NS 4102, and copying the
pair would have put a bank loan among the equity in the næringsspesifikasjon.
Where the file's pair fails, Myntos's default by number is proposed instead and
a ``merknad`` says what the file had.

**Customers and suppliers.** The balance on 1500 and 2400 is split into one line
per party, but only when the file's sub-ledger adds up to the account exactly:
half a reskontro cannot be reconciled, which is the rule the review already
enforces. A party is found by organisasjonsnummer, then by name; one that is
not in Myntos is proposed with the number the old program gave it, when that is
free, because it is the number the accountant knows.

**VAT accounts.** A balance on 2700, 2710 or any account the file groups as
«Skyldig merverdiavgift» goes on 2740: last year's VAT is settled there, not
swept into this year's first return (see the review's ``mva_konto``).

**The result nobody posted.** Tripletex carries every balance-sheet account into
the new year and leaves the previous year's result off equity until the year
is closed in it. The balances then do not sum to zero, and the difference *is*
that result: a ledger that balances across all its accounts can be out of
balance across the balance sheet only by the income statement. It goes on 2050,
which is where Myntos's own årsavslutning puts an ENK's result and where its
SAF-T export places an undisposed one (``udisponert_resultat``), with a
``merknad`` saying so. That is arithmetic, not a guess; where it belongs in an
AS with a loss (2050 or 2080) is the accountant's call, and the note says that
too.

**The bank.** Myntos books every payment on 1920. A file whose own header names
the company's bank account in an account's text, on an account other than
1920, gets a ``merknad`` (Tripletex keeps a farm's driftskreditt on 2390). Not
moved: whether a credit line in the black belongs among the assets is the
accountant's to say, and the line can be edited before it is checked.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal
from typing import BinaryIO, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from myntos.domain.arsoppgjor import opening
from myntos.domain.arsoppgjor.findings import Finding, feil, merknad
from myntos.domain.ledger import kontoplan
from myntos.domain.ledger.bankkonto import format_bank_account
from myntos.domain.ledger.chart import (
    BANK_ACCOUNT,
    INPUT_VAT_ACCOUNT,
    OUTPUT_VAT_ACCOUNT,
    PAYABLES_ACCOUNT,
    RECEIVABLES_ACCOUNT,
)
from myntos.domain.ledger.errors import InvalidOrgnrError
from myntos.domain.ledger.models import (
    Account,
    AccountGrouping,
    AccountType,
    Company,
    Party,
    PartyType,
)
from myntos.domain.ledger.orgnr import validate_orgnr
from myntos.saft import codes
from myntos.saft.errors import SaftWrongCompanyError, SaftWrongYearError
from myntos.saft.reader import PartyRecord, SaftBalances, SourceAccount, SourceParty
from myntos.saft.reader import read as read_file

ZERO = Decimal("0.00")
VAT_SETTLEMENT_ACCOUNT = "2740"
EQUITY_ACCOUNT = "2050"
_VAT_GROUPING = ("kortsiktigGjeld", "2740")
_FOUR_DIGITS = re.compile(r"^([1-8]\d{3})")

#: The grouping categories an account of each type may sit under. A pair from
#: the list can still be the wrong kind of post for the account it is on.
_CATEGORIES_BY_TYPE: dict[AccountType, frozenset[str]] = {
    AccountType.EIENDEL: frozenset(
        {"balanseverdiForAnleggsmiddel", "balanseverdiForOmloepsmiddel"}
    ),
    AccountType.EGENKAPITAL: frozenset({"egenkapital"}),
    AccountType.GJELD: frozenset({"langsiktigGjeld", "kortsiktigGjeld"}),
}

Balances = Literal["inngaende", "utgaende"]


@dataclass(frozen=True)
class AccountToAdd:
    number: str
    description: str
    account_type: AccountType
    grouping_category: str
    grouping_code: str
    #: The file's own account ids that land on this number.
    source_accounts: tuple[str, ...]


@dataclass(frozen=True)
class PartyToAdd:
    party_type: PartyType
    name: str
    orgnr: str | None
    #: The old program's number for the party, when it is free here.
    party_id: str | None
    address_line: str | None
    postal_code: str | None
    city: str | None
    source_id: str


@dataclass(frozen=True)
class SaftSource:
    software: str | None
    audit_file_version: str
    orgnr: str
    company_name: str
    start: date
    end: date
    #: Which of the file's balances were read: its opening ones (the file is for
    #: the first year in Myntos) or its closing ones (the year before).
    balances: Balances


@dataclass(frozen=True)
class SaftOpening:
    source: SaftSource
    lines: list[opening.OpeningLineInput]
    review: opening.OpeningReview
    accounts_to_add: list[AccountToAdd]
    parties_to_add: list[PartyToAdd]


@dataclass
class _Line:
    """A line on its way to the form, still knowing where it came from."""

    account_number: str
    amount: Decimal
    description: str
    party_pk: int | None = None
    source: SourceAccount | None = None


def _signed(value: Decimal) -> tuple[Decimal, Decimal]:
    return (value, ZERO) if value > ZERO else (ZERO, -value)


def target_account(account_id: str) -> str | None:
    """The Myntos account a file's account id lands on: its first four digits."""
    found = _FOUR_DIGITS.match(account_id)
    return found.group(1) if found is not None else None


def _source_text(source: SourceAccount) -> str:
    return f"{source.account_id} {source.description}".strip()[:255]


def _shared_name(descriptions: Sequence[str]) -> str:
    """What several accounts on one number are called together.

    «Lagerbeh.: Storfe (Kyr tilført i året)» and «Lagerbeh.: Storfe (Kviger over
    12 md.)» are one Myntos account called «Lagerbeh.: Storfe»: the common
    start, cut back to a whole word and stripped of the punctuation that opened
    the part that differs.
    """
    if len(set(descriptions)) == 1:
        return descriptions[0]
    shared = os.path.commonprefix(list(descriptions))
    if not shared.endswith(" ") and " " in shared:
        shared = shared[: shared.rfind(" ")]
    shared = shared.rstrip(" (:-,.;/")
    return shared if len(shared) >= 4 else descriptions[0]


def _fits(account_type: AccountType, category: str) -> bool:
    allowed = _CATEGORIES_BY_TYPE.get(account_type)
    return allowed is None or category in allowed


def _usable(year: int, account_type: AccountType, pair: tuple[str, str]) -> bool:
    """Whether the file's grouping may be taken over as it is.

    In the year's list, of a kind the account can be, and not a post for IFRS
    reporters. That last one is Tripletex again: it grouped an ENK's «Innskudd
    kontanter» (2055) on the post numbered 2055, which in the list is «Avsatt
    utbytte - IFRS». A number that matches is not a meaning that matches.
    """
    category, code = pair
    if category == "NA" or not _fits(account_type, category):
        return False
    if not codes.is_choosable_grouping(year, category, code):
        return False
    described = next(
        (
            entry.description
            for entry in codes.grouping_entries(year)
            if (entry.category, entry.code) == pair
        ),
        "",
    )
    return "ifrs" not in described.lower()


def _kroner(value: Decimal) -> str:
    """``85 329,76``, the way the rest of the page writes an amount."""
    return f"{value:,.2f}".replace(",", "\u00a0").replace(".", ",")


def folds_into_vat_settlement(number: str | None, source: SourceAccount) -> bool:
    """Whether the opening balance puts this account's balance on 2740."""
    return number is not None and (
        number in {OUTPUT_VAT_ACCOUNT, INPUT_VAT_ACCOUNT}
        or (
            number != VAT_SETTLEMENT_ACCOUNT
            and (source.grouping_category, source.grouping_code) == _VAT_GROUPING
        )
    )


def _normalise(name: str) -> str:
    return re.sub(r"[^0-9a-zæøå]+", "", name.lower())


def match_party(
    parties: Sequence[Party], party_type: PartyType, orgnr: str | None, name: str
) -> Party | None:
    """This company's party for one of the file's: by organisasjonsnummer, then by name."""
    return next(
        (
            party
            for party in parties
            if party.party_type == party_type and orgnr is not None and party.orgnr == orgnr
        ),
        None,
    ) or next(
        (
            party
            for party in parties
            if party.party_type == party_type and _normalise(party.name) == _normalise(name)
        ),
        None,
    )


def party_to_add(
    item: SourceParty | PartyRecord, party_type: PartyType, taken: set[tuple[PartyType, str]]
) -> PartyToAdd:
    """The party to create for one the file names, under the old program's number if free.

    ``taken`` is updated, so two proposals never ask for the same number.
    """
    try:
        orgnr = validate_orgnr(item.orgnr) if item.orgnr else None
    except InvalidOrgnrError:
        orgnr = None
    wanted = item.party_id if item.party_id.isdigit() else None
    if wanted is not None and (party_type, wanted) in taken:
        wanted = None
    if wanted is not None:
        taken.add((party_type, wanted))
    return PartyToAdd(
        party_type=party_type,
        name=item.name,
        orgnr=orgnr,
        party_id=wanted,
        address_line=item.address_line,
        postal_code=item.postal_code,
        city=item.city,
        source_id=item.party_id,
    )


def propose_accounts(
    year: int,
    by_number: dict[str, list[SourceAccount]],
    accounts: dict[str, Account],
    groupings: dict[int, tuple[str, str]],
    *,
    compare_existing: bool,
) -> tuple[list[AccountToAdd], list[Finding]]:
    """The accounts to add for the file's, and what to say about their groupings.

    ``compare_existing`` adds a ``gruppering_avviker`` for an account Myntos
    has and the file groups differently; the opening balance says it once, and
    the year's vouchers on the same accounts need not say it again.
    """
    findings: list[Finding] = []
    to_add: list[AccountToAdd] = []
    for number, sources in by_number.items():
        account = accounts.get(number)
        file_pair = next(
            (
                (s.grouping_category, s.grouping_code)
                for s in sources
                if s.grouping_category and s.grouping_code
            ),
            None,
        )
        if account is not None:
            ours = groupings.get(account.id)
            # Only a difference the accountant could reasonably resolve the
            # file's way: a pair Myntos would itself have refused to take over is
            # already explained when the account was proposed.
            if (
                compare_existing
                and file_pair is not None
                and ours is not None
                and file_pair != ours
                and _usable(year, account.account_type, file_pair)
            ):
                findings.append(
                    merknad(
                        "gruppering_avviker",
                        f"Konto {number} {account.description} er gruppert som {ours[0]} "
                        f"{ours[1]} i Myntos og som {file_pair[0]} {file_pair[1]} i filen. "
                        "Grupperingen bestemmer posten i næringsspesifikasjonen; velg den på "
                        "Kontoplan hvis filens er riktig.",
                    )
                )
            continue
        account_type = kontoplan.default_type(number)
        default = kontoplan.default_grouping(number, year)
        pair = default
        if file_pair is not None:
            if _usable(year, account_type, file_pair):
                pair = file_pair
            else:
                findings.append(
                    merknad(
                        "gruppering_fra_filen_brukes_ikke",
                        f"Filen grupperer {', '.join(s.account_id for s in sources)} som "
                        f"{file_pair[0]} {file_pair[1]}, som ikke passer en konto i "
                        f"{number[:2]}xx, gjelder IFRS eller ikke er en post i listen for "
                        f"{year}. Ny konto {number} får {default[0]} {default[1]}; "
                        "regnskapsføreren bør se på den.",
                    )
                )
        to_add.append(
            AccountToAdd(
                number=number,
                description=_shared_name([s.description for s in sources])[:255]
                or f"Konto {number}",
                account_type=account_type,
                grouping_category=pair[0],
                grouping_code=pair[1],
                source_accounts=tuple(s.account_id for s in sources),
            )
        )
    to_add.sort(key=lambda add: add.number)
    return to_add, findings


async def chart_of(
    db: AsyncSession, company: Company, year: int
) -> tuple[dict[str, Account], dict[int, tuple[str, str]]]:
    """The company's accounts by number, and each one's grouping for ``year``."""
    accounts: dict[str, Account] = {
        account.account_number: account
        for account in await db.scalars(select(Account).where(Account.company_id == company.id))
    }
    groupings: dict[int, tuple[str, str]] = {}
    if accounts:
        for row in await db.scalars(
            select(AccountGrouping).where(
                AccountGrouping.account_id.in_([a.id for a in accounts.values()]),
                AccountGrouping.financial_year == year,
            )
        ):
            groupings[row.account_id] = (row.grouping_category, row.grouping_code)
    return accounts, groupings


def check_company(company: Company, orgnr: str, name: str) -> None:
    """Refuse another company's file before anything in it is shown (CLAUDE.md rule 1)."""
    if orgnr != company.orgnr:
        raise SaftWrongCompanyError(
            f"filen er regnskapet til org.nr. {orgnr} ({name}), ikke til "
            f"{company.name}. Velg riktig selskap, eller last ned filen for dette selskapet"
        )


async def read_opening(db: AsyncSession, *, company: Company, upload: BinaryIO) -> SaftOpening:
    """Read ``upload`` into opening-balance lines for ``company``. Posts nothing."""
    found = read_file(upload)
    return await from_balances(db, company=company, found=found)


async def from_balances(db: AsyncSession, *, company: Company, found: SaftBalances) -> SaftOpening:
    check_company(company, found.orgnr, found.company_name)

    year = await opening.first_year(db, company)
    first = year.year if year is not None else found.start.year
    balances: Balances
    if found.start == date(first, 1, 1):
        balances = "inngaende"
    elif found.start == date(first - 1, 1, 1) and found.end == date(first - 1, 12, 31):
        balances = "utgaende"
    else:
        raise SaftWrongYearError(
            f"filen gjelder {found.start.isoformat()} til {found.end.isoformat()}. Inngående "
            f"balanse i Myntos er per 1. januar {first}: last opp SAF-T-filen for {first} (fra "
            f"1. januar) eller for hele {first - 1}"
        )
    source = SaftSource(
        software=found.software,
        audit_file_version=found.audit_file_version,
        orgnr=found.orgnr,
        company_name=found.company_name,
        start=found.start,
        end=found.end,
        balances=balances,
    )

    def balance_of(item: SourceAccount | SourceParty) -> Decimal:
        return item.opening if balances == "inngaende" else item.closing

    accounts, groupings = await chart_of(db, company, first)
    parties = list(await db.scalars(select(Party).where(Party.company_id == company.id)))

    findings: list[Finding] = []
    lines: list[_Line] = []
    result = ZERO
    for source_account in found.accounts:
        amount = balance_of(source_account)
        if amount == ZERO:
            continue
        number = target_account(source_account.account_id)
        if balances == "utgaende" and number is not None and number[0] not in "12":
            # The closing year's income statement: what it adds up to is the
            # result, and that is placed on equity below, not posted account by
            # account into a year it does not belong to.
            result += amount
            continue
        if folds_into_vat_settlement(number, source_account):
            findings.append(
                merknad(
                    "mva_flyttet_til_2740",
                    f"{_source_text(source_account)} er mva som ikke var gjort opp ved årsskiftet. "
                    "Den står på 2740 Oppgjørskonto merverdiavgift, der betalingen avregner den.",
                )
            )
            number = VAT_SETTLEMENT_ACCOUNT
        lines.append(
            _Line(
                # Cut to what a posting request takes, so the review is what
                # refuses an id with no account in it, not request validation.
                account_number=number or source_account.account_id[:32],
                amount=amount,
                description=_source_text(source_account),
                source=source_account,
            )
        )

    # --- Customers and suppliers -----------------------------------------------
    parties_to_add: list[PartyToAdd] = []
    taken = {(party.party_type, party.party_id) for party in parties}
    for target, kind in ((RECEIVABLES_ACCOUNT, "kunde"), (PAYABLES_ACCOUNT, "leverandor")):
        on_account = [line for line in lines if line.account_number == target]
        if not on_account:
            continue
        total = sum((line.amount for line in on_account), ZERO)
        open_items = [
            party
            for party in found.parties
            if party.kind == kind
            and target_account(party.account_id) == target
            and balance_of(party)
        ]
        if not open_items:
            continue
        if sum((balance_of(party) for party in open_items), ZERO) != total:
            findings.append(
                merknad(
                    "reskontro_avviker",
                    f"Kunde- og leverandørsaldoene i filen summerer ikke til saldoen på {target}, "
                    "så kontoen står som én linje uten fordeling på åpne poster.",
                )
            )
            continue
        party_type = PartyType(kind)
        replaced: list[_Line] = []
        for item in open_items:
            matched = match_party(parties, party_type, item.orgnr, item.name)
            if matched is None:
                parties_to_add.append(party_to_add(item, party_type, taken))
            replaced.append(
                _Line(
                    account_number=target,
                    amount=balance_of(item),
                    description=f"{item.party_id} {item.name}".strip()[:255],
                    party_pk=matched.id if matched is not None else None,
                )
            )
        if any(line.party_pk is None for line in replaced):
            # Some open items name a party Myntos does not have yet: until it is
            # added the line cannot carry it, and a sub-ledger with some parties
            # and not others is the review's ``reskontro_ufullstendig``. One
            # finding says what to do about it instead of one per line.
            for line in replaced:
                line.party_pk = None
        index = lines.index(on_account[0])
        lines = [line for line in lines if line.account_number != target]
        lines[index:index] = replaced
    if parties_to_add:
        findings.append(
            feil(
                "parter_mangler",
                f"{len(parties_to_add)} kunder og leverandører med åpne poster i filen finnes ikke "
                "i Myntos ennå. Legg dem til, så fordeles 1500 og 2400 på dem.",
            )
        )

    # --- The result nobody posted ------------------------------------------------
    # Only when the file proves that is what the difference is. In a file of the
    # first year, its own movements must balance (all closings less all openings
    # is nothing), so the gap was there before the year began; in a file of the
    # year before, the gap must be exactly that year's income statement. A file
    # that fails either is out of balance for some other reason, and the
    # review's «ubalanse» says so rather than a line here hiding it.
    difference = sum((line.amount for line in lines), ZERO)
    if balances == "inngaende":
        explained = sum((a.closing - a.opening for a in found.accounts), ZERO) == ZERO and all(
            line.source is None or (target_account(line.source.account_id) or "9")[0] in "12"
            for line in lines
        )
    else:
        explained = difference == -result
    if difference != ZERO and explained:
        previous = first - 1
        lines.append(
            _Line(
                account_number=EQUITY_ACCOUNT,
                amount=-difference,
                description=f"Udisponert resultat {previous} fra SAF-T-filen",
            )
        )
        # Equity is a credit. A result left off it leaves the balance sheet
        # short of credit by a profit and long by a loss, so a balance sheet
        # that sums to a credit is carrying a loss nobody posted.
        outcome = "underskudd" if difference < ZERO else "overskudd"
        findings.append(
            merknad(
                "udisponert_resultat",
                f"Saldoene i filen går ikke i null: differansen {_kroner(abs(difference))} kr "
                f"er {outcome} for {previous}, som det gamle systemet ikke hadde ført mot "
                "egenkapitalen. Det står på 2050 Annen egenkapital, slik Myntos sin "
                "årsavslutning fører et resultat. For et AS med underskudd kan deler av det "
                "høre hjemme på 2080 Udekket tap: avklar det med regnskapsføreren.",
            )
        )

    # --- What the kontoplan is missing -------------------------------------------
    by_number: dict[str, list[SourceAccount]] = defaultdict(list)
    for line in lines:
        if (
            line.source is not None
            and target_account(line.source.account_id) == line.account_number
        ):
            by_number[line.account_number].append(line.source)
    merged = [number for number, sources in by_number.items() if len(sources) > 1]
    if merged:
        findings.append(
            merknad(
                "kontoer_slatt_sammen",
                "Filen fører samme konto på flere avdelinger eller produktkoder ("
                + ", ".join(
                    f"{number}: " + ", ".join(s.account_id for s in by_number[number])
                    for number in merged[:4]
                )
                + (" …" if len(merged) > 4 else "")
                + "). Myntos har fire siffer og ingen avdelinger ennå, så de står som egne "
                "linjer på samme konto, med koden fra filen i teksten.",
            )
        )
    accounts_to_add, notes = propose_accounts(
        first, by_number, accounts, groupings, compare_existing=True
    )
    findings.extend(notes)
    if accounts_to_add:
        findings.append(
            feil(
                "kontoer_mangler",
                f"{len(accounts_to_add)} kontoer i filen finnes ikke i kontoplanen ennå. Legg dem "
                "til med navnet og grupperingen fra filen, eller før saldoene på kontoer du "
                "allerede har.",
            )
        )

    # --- The bank ----------------------------------------------------------------
    for line in lines:
        if line.source is None or line.account_number == BANK_ACCOUNT:
            continue
        digits = re.sub(r"\D", "", line.source.description)
        for bank in found.bank_accounts:
            if len(bank) == 11 and bank in digits:
                findings.append(
                    merknad(
                        "bankkonto_utenfor_1920",
                        f"{line.description} er bankkontoen {format_bank_account(bank)}. Myntos "
                        "fører inn- og utbetalinger på 1920 Bankinnskudd, så for at banken skal "
                        "stemme bør saldoen stå der: endre kontoen på linja til 1920 hvis "
                        "regnskapsføreren er enig.",
                    )
                )
                break

    as_input = [
        opening.OpeningLineInput(
            account_number=line.account_number,
            debit=_signed(line.amount)[0],
            credit=_signed(line.amount)[1],
            party_pk=line.party_pk,
            description=line.description,
        )
        for line in lines
    ]
    checked = await opening.review(db, company=company, lines=as_input)
    # The file's own findings first: «add these accounts» explains the review's
    # «ukjent_konto» lines below it, and is the one thing to act on.
    review = replace(checked, findings=findings + checked.findings)
    return SaftOpening(
        source=source,
        lines=as_input,
        review=review,
        accounts_to_add=accounts_to_add,
        parties_to_add=parties_to_add,
    )


__all__ = [
    "AccountToAdd",
    "PartyToAdd",
    "SaftOpening",
    "SaftSource",
    "chart_of",
    "check_company",
    "folds_into_vat_settlement",
    "from_balances",
    "match_party",
    "party_to_add",
    "propose_accounts",
    "read_opening",
    "target_account",
]
