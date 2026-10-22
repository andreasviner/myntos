"""Writing, issuing and crediting a salgsdokument.

Everything that touches the database lives here; the API layer maps HTTP onto
these functions and their errors. Like the other services, nothing commits — the
request owns the transaction, so "assign the number, post the voucher, archive
the PDF" cannot half-happen. That matters more here than anywhere else in the
system: a number handed out without a posted voucher behind it is a gap in the
series, and a gap is what an auditor reads as a deleted invoice.

The lifecycle is deliberately short:

**Draft.** Free to edit, has no number, is not in the books, and is nobody's
document but ours. Compliance is not checked — a half-written invoice is allowed
to be half-written.

**Issue.** The one irreversible step. It checks § 5-1 in full
(:mod:`myntos.domain.faktura.compliance`), takes the next number from the
company's series, posts the double entry, freezes the EHF XML, and files the PDF
in the append-only bilag archive. After it, nothing about the document changes.

**Kreditnota.** The only correction. § 5-2-7 requires a sales document that is
being replaced to be reversed by a credit note, so this issues a mirror document
and posts the reversing voucher through the ledger's own
:func:`~myntos.domain.ledger.posting.build_reversal` — which is what keeps the
credit note visible as a reversal to the SAF-T export and to the cash-flow
listing, rather than as a second invoice that happens to have negative-looking
amounts.

Crediting is all-or-nothing. A partial credit is a different document with its
own lines, and pretending a full reversal covers it would leave the ledger and
the paper disagreeing about how much was actually credited.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from myntos.core.clock import utc_now
from myntos.domain.bilag import service as bilag_service
from myntos.domain.faktura import compliance, ehf, kid, pdf
from myntos.domain.faktura.catalogue import SALES_TAX_OPTIONS_BY_CODE, UNIT_CODES
from myntos.domain.faktura.errors import (
    AlreadyCreditedError,
    InvalidLineError,
    InvoiceNotCompliantError,
    InvoiceNotDraftError,
    InvoiceNotFoundError,
    NotACustomerError,
    NotCreditableError,
    UnsupportedCurrencyError,
)
from myntos.domain.faktura.models import (
    DEFAULT_PAYMENT_TERM_DAYS,
    DEFAULT_UNIT_CODE,
    DocumentSeries,
    DocumentType,
    InvoiceStatus,
    SalesDocument,
    SalesDocumentLine,
)
from myntos.domain.faktura.totals import DocumentTotals, line_net, totals
from myntos.domain.faktura.vat import VatCategory, category_for
from myntos.domain.ledger import parties as party_service
from myntos.domain.ledger import posting
from myntos.domain.ledger import service as ledger_service
from myntos.domain.ledger.chart import (
    OUTPUT_VAT_ACCOUNT,
    RECEIVABLES_ACCOUNT,
    SALES_JOURNAL_DESCRIPTION,
    SALES_JOURNAL_ID,
    SALES_JOURNAL_TYPE,
)
from myntos.domain.ledger.errors import ChartIncompleteError, UnknownTaxCodeError
from myntos.domain.ledger.models import (
    Account,
    Company,
    CompanyBankAccount,
    Journal,
    Party,
    PartyType,
    TaxCode,
    Transaction,
    TransactionLine,
)
from myntos.domain.ledger.posting import ZERO
from myntos.storage.base import DocumentStorage

# A quantity may be fractional but not unbounded — six decimals is what the
# column holds, and more than that is a typo rather than a measurement.
_QUANTITY_EXPONENT = -6
_PRICE_EXPONENT = -4


@dataclass(frozen=True)
class LineInput:
    """One line as the caller supplies it, before it is priced and taxed."""

    description: str
    quantity: Decimal
    unit_price: Decimal
    tax_code: str
    unit_code: str = DEFAULT_UNIT_CODE
    discount_rate: Decimal = ZERO


# --- Reading -----------------------------------------------------------------------


def _document_query() -> Select[tuple[SalesDocument]]:
    return select(SalesDocument).options(
        selectinload(SalesDocument.lines),
        selectinload(SalesDocument.party),
    )


async def get_document(db: AsyncSession, *, company: Company, document_pk: int) -> SalesDocument:
    """One document belonging to ``company``, with its lines loaded.

    Scoped to the company, so a document id from another tenant is simply not
    found — the same answer as one that never existed.
    """
    document = await db.scalar(
        _document_query().where(
            SalesDocument.id == document_pk, SalesDocument.company_id == company.id
        )
    )
    if document is None:
        raise InvoiceNotFoundError("finner ikke fakturaen")
    return document


async def list_documents(
    db: AsyncSession,
    *,
    company: Company,
    status: InvoiceStatus | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[SalesDocument]:
    """The invoice book, newest first.

    Drafts sort by creation and issued documents by number, but both come back in
    one list ordered by id — which for this table is the same as chronological,
    and avoids a sort key that is NULL for half the rows.
    """
    query = _document_query().where(SalesDocument.company_id == company.id)
    if status is not None:
        query = query.where(SalesDocument.status == status)
    result = await db.scalars(query.order_by(SalesDocument.id.desc()).limit(limit).offset(offset))
    return list(result)


async def credited_by(db: AsyncSession, document_pks: Sequence[int]) -> dict[int, int]:
    """``{invoice id: credit note id}`` for the given documents."""
    if not document_pks:
        return {}
    rows = await db.execute(
        select(SalesDocument.credits_document_pk, SalesDocument.id).where(
            SalesDocument.credits_document_pk.in_(document_pks)
        )
    )
    return {credited: note for credited, note in rows if credited is not None}


# --- Writing a draft -----------------------------------------------------------------


def _chosen_bank_account(document: SalesDocument, company: Company) -> CompanyBankAccount | None:
    """The account this document is paid into, resolved against the company now.

    The document may name one (``bank_account_id``); if it does not, or if the
    one it named has since been closed, the company's primary account answers.
    Scanned rather than queried because ``bank_accounts`` is eagerly loaded, and
    scanned against *this* company because the alternative is a lookup by
    primary key alone — the hole docs/company-isolation.md is about.
    """
    if document.bank_account_id is not None:
        for account in company.bank_accounts:
            if account.id == document.bank_account_id:
                return account
    return company.primary_bank_account


def _snapshot_seller(document: SalesDocument, company: Company) -> None:
    """Copy the seller's details onto the document.

    Called whenever the draft is written — created or changed — and again at
    issue: the draft shows a preview that has to be accurate, and the issued
    document has to reflect the company as it stood at that moment rather than as
    it stood whenever the draft was started.
    """
    document.seller_name = company.name
    document.seller_orgnr = company.orgnr
    document.seller_address_line = company.address_line
    document.seller_postal_code = company.postal_code
    document.seller_city = company.city
    document.seller_country = company.country
    document.seller_vat_registered = company.vat_registered
    document.seller_in_foretaksregisteret = company.in_foretaksregisteret
    document.seller_organisation_form = company.organisation_form
    # A choice that no longer exists is dropped rather than re-pointed, and a
    # document that never made one is left alone. Both end up in the same state
    # — following the company's primary — and that is the point: pinning a draft
    # to whichever account happened to be primary the first time it was saved
    # would make a company that later changes its primary keep invoicing on the
    # old one, silently, from a form that shows no account selected.
    account = _chosen_bank_account(document, company)
    if document.bank_account_id is not None and (
        account is None or account.id != document.bank_account_id
    ):
        document.bank_account_id = None
    document.seller_bank_account = None if account is None else account.account


def _snapshot_buyer(document: SalesDocument, party: Party) -> None:
    document.party_id = party.id
    document.buyer_name = party.name
    document.buyer_orgnr = party.orgnr
    document.buyer_address_line = party.address_line
    document.buyer_postal_code = party.postal_code
    document.buyer_city = party.city
    document.buyer_country = party.country


def _check_decimal(value: Decimal, *, field: str, min_exponent: int) -> Decimal:
    if not isinstance(value, Decimal):  # pragma: no cover - pydantic coerces first
        raise InvalidLineError(f"{field} må være et tall")
    exponent = value.as_tuple().exponent
    if not isinstance(exponent, int):
        raise InvalidLineError(f"{field} er ikke et gyldig tall")
    if exponent < min_exponent:
        raise InvalidLineError(f"{field} har for mange desimaler")
    return value


async def _rate_for(db: AsyncSession, company: Company, tax_code: str, on: date) -> Decimal:
    """The rate in force for ``tax_code`` on ``on``, from the company's own table.

    Read through the company's tax codes rather than from the seed constants: the
    rates are date-effective per company, and a hardcoded 25 % would be wrong for
    every period on the other side of a rate change.
    """
    tax = await db.scalar(
        select(TaxCode)
        .where(
            TaxCode.company_id == company.id,
            TaxCode.code == tax_code,
        )
        .options(selectinload(TaxCode.rates))
    )
    if tax is None:
        raise UnknownTaxCodeError(f"mva-koden {tax_code!r} finnes ikke for dette selskapet")
    return posting.rate_on(tax, on)


async def _build_lines(
    db: AsyncSession,
    company: Company,
    inputs: Sequence[LineInput],
    *,
    priced_on: date,
) -> list[SalesDocumentLine]:
    """Turn the caller's lines into stored lines, priced and taxed.

    ``priced_on`` decides which VAT rate is snapshotted. It is the delivery date
    when there is one, because that is when the supply happened and therefore
    which rate applies — not the date somebody happened to type the invoice.
    """
    lines: list[SalesDocumentLine] = []
    for index, item in enumerate(inputs, start=1):
        option = SALES_TAX_OPTIONS_BY_CODE.get(item.tax_code)
        if option is None:
            raise UnknownTaxCodeError(
                f"mva-koden {item.tax_code!r} kan ikke brukes på en salgsfaktura"
            )
        if item.unit_code not in UNIT_CODES:
            raise InvalidLineError(f"ukjent enhet: {item.unit_code!r}")
        quantity = _check_decimal(item.quantity, field="antall", min_exponent=_QUANTITY_EXPONENT)
        if quantity == ZERO:
            raise InvalidLineError("antallet kan ikke være null")
        unit_price = _check_decimal(item.unit_price, field="pris", min_exponent=_PRICE_EXPONENT)
        if unit_price < ZERO:
            raise InvalidLineError("prisen kan ikke være negativ")
        if not ZERO <= item.discount_rate < Decimal(1):
            raise InvalidLineError("rabatten må være mellom 0 og 100 prosent")

        rate = await _rate_for(db, company, item.tax_code, priced_on)
        net = line_net(quantity=quantity, unit_price=unit_price, discount_rate=item.discount_rate)
        lines.append(
            SalesDocumentLine(
                line_no=index,
                description=item.description.strip(),
                quantity=quantity,
                unit_code=item.unit_code,
                unit_price=unit_price,
                discount_rate=item.discount_rate,
                tax_code=item.tax_code,
                tax_rate=rate,
                income_account_number=option.income_account,
                net_amount=net,
                # Per line, for display and for the ledger's per-code grouping.
                # The document's VAT total is computed from the § 5-1-5 groups,
                # not by adding these up — see myntos.domain.faktura.totals.
                vat_amount=posting.compute_tax(net, rate),
            )
        )
    return lines


async def create_draft(
    db: AsyncSession,
    *,
    company: Company,
    party_pk: int | None = None,
    buyer_name: str | None = None,
    buyer_orgnr: str | None = None,
    buyer_address_line: str | None = None,
    buyer_postal_code: str | None = None,
    buyer_city: str | None = None,
    buyer_is_business: bool = True,
    delivery_date: date | None = None,
    delivery_place: str | None = None,
    due_date: date | None = None,
    buyer_reference: str | None = None,
    order_reference: str | None = None,
    payment_reference: str | None = None,
    payment_reference_auto: bool = True,
    bank_account_pk: int | None = None,
    note: str | None = None,
    reverse_charge: bool = False,
    lines: Sequence[LineInput] = (),
    today: date | None = None,
) -> SalesDocument:
    """Start a new faktura.

    The buyer comes either from a registered customer (``party_pk``) or typed in.
    Both are legitimate: a recurring customer belongs in the party register and
    in the SAF-T kundereskontro, while a one-off consumer sale does not, and
    forcing every walk-in into the register would fill it with names nobody
    wants to see again.

    ``bank_account_pk`` says which of the company's accounts this one is paid
    into. Omitted means the primary, which is the answer for every company that
    has one account and the sensible default for the rest.
    """
    now = today or utc_now().date()
    document = SalesDocument(
        company_id=company.id,
        document_type=DocumentType.FAKTURA,
        status=InvoiceStatus.UTKAST,
        buyer_name=(buyer_name or "").strip(),
        buyer_is_business=buyer_is_business,
        delivery_date=delivery_date,
        delivery_place=delivery_place,
        due_date=due_date or now + timedelta(days=DEFAULT_PAYMENT_TERM_DAYS),
        buyer_reference=buyer_reference,
        order_reference=order_reference,
        # A manual reference and an automatic one are mutually exclusive. The
        # flag wins here — on a new draft it was either passed deliberately or
        # left at its default, and issue would overwrite the typed one anyway.
        payment_reference=None if payment_reference_auto else (payment_reference or None),
        payment_reference_auto=payment_reference_auto,
        note=note,
        reverse_charge=reverse_charge,
        # Snapshot placeholders; overwritten immediately below.
        seller_name=company.name,
        seller_orgnr=company.orgnr,
    )
    if bank_account_pk is not None:
        # Looked up with the company, so a neighbouring company's account id in
        # the payload is a 404 rather than a number on this company's invoice.
        chosen = await ledger_service.get_bank_account(
            db, company=company, bank_account_pk=bank_account_pk
        )
        document.bank_account_id = chosen.id
    _snapshot_seller(document, company)

    if party_pk is not None:
        party = await party_service.get_party(db, company=company, party_pk=party_pk)
        if party.party_type is not PartyType.KUNDE:
            raise NotACustomerError("en faktura kan bare stiles til en kunde")
        _snapshot_buyer(document, party)
    else:
        document.buyer_orgnr = buyer_orgnr
        document.buyer_address_line = buyer_address_line
        document.buyer_postal_code = buyer_postal_code
        document.buyer_city = buyer_city

    document.lines = await _build_lines(db, company, lines, priced_on=delivery_date or now)
    db.add(document)
    await db.flush()
    return document


async def update_draft(
    db: AsyncSession,
    *,
    company: Company,
    document_pk: int,
    party_pk: int | None = None,
    buyer_name: str | None = None,
    buyer_orgnr: str | None = None,
    buyer_address_line: str | None = None,
    buyer_postal_code: str | None = None,
    buyer_city: str | None = None,
    buyer_is_business: bool | None = None,
    delivery_date: date | None = None,
    delivery_place: str | None = None,
    due_date: date | None = None,
    buyer_reference: str | None = None,
    order_reference: str | None = None,
    payment_reference: str | None = None,
    payment_reference_auto: bool | None = None,
    bank_account_pk: int | None = None,
    note: str | None = None,
    reverse_charge: bool | None = None,
    lines: Sequence[LineInput] | None = None,
    today: date | None = None,
) -> SalesDocument:
    """Change a draft. Refuses once the document has been issued.

    The seller is re-snapshotted along the way. Without it a draft would keep the
    company as it stood when the draft was started, and the compliance findings
    the caller gets back would describe that — so someone who added the missing
    kontonummer, came back to the draft and saved it would still be told the
    company has none, with no way to clear the finding. Issue re-snapshots too,
    so this only makes the answer *before* issue agree with the answer at it.
    """
    document = await get_document(db, company=company, document_pk=document_pk)
    _assert_draft(document)
    # Before the snapshot, which resolves the choice into the number that gets
    # printed. Omitted means "leave it alone" as everywhere else here, so a form
    # that does not offer the picker — a company with one account — cannot move
    # the invoice off the account it was drafted against.
    if bank_account_pk is not None:
        chosen = await ledger_service.get_bank_account(
            db, company=company, bank_account_pk=bank_account_pk
        )
        document.bank_account_id = chosen.id
    _snapshot_seller(document, company)

    if party_pk is not None:
        party = await party_service.get_party(db, company=company, party_pk=party_pk)
        if party.party_type is not PartyType.KUNDE:
            raise NotACustomerError("en faktura kan bare stiles til en kunde")
        _snapshot_buyer(document, party)
    elif buyer_name is not None:
        document.party_id = None
        document.buyer_name = buyer_name.strip()
        document.buyer_orgnr = buyer_orgnr
        document.buyer_address_line = buyer_address_line
        document.buyer_postal_code = buyer_postal_code
        document.buyer_city = buyer_city

    if buyer_is_business is not None:
        document.buyer_is_business = buyer_is_business
    if delivery_date is not None:
        document.delivery_date = delivery_date
    if delivery_place is not None:
        document.delivery_place = delivery_place
    if due_date is not None:
        document.due_date = due_date
    if buyer_reference is not None:
        document.buyer_reference = buyer_reference
    if order_reference is not None:
        document.order_reference = order_reference
    # The two KID fields are read together, and in this order. Supplying a
    # reference is by itself a decision to use that one — an empty string being
    # the way to say "none at all", since ``None`` means "leave it alone" here
    # and would otherwise make a KID impossible to remove once set. An explicit
    # ``payment_reference_auto`` is then the last word, so a caller that asks for
    # the automatic one gets it and gets the stale value cleared with it.
    if payment_reference is not None:
        document.payment_reference = payment_reference.strip() or None
        document.payment_reference_auto = False
    if payment_reference_auto is not None:
        document.payment_reference_auto = payment_reference_auto
        if payment_reference_auto:
            # Cleared rather than kept: issue is about to overwrite it, and until
            # then the form would be showing a number the invoice will not carry.
            document.payment_reference = None
    if note is not None:
        document.note = note
    if reverse_charge is not None:
        document.reverse_charge = reverse_charge

    if lines is not None:
        replacement = await _build_lines(
            db,
            company,
            lines,
            priced_on=document.delivery_date or today or utc_now().date(),
        )
        # Clear and flush *before* attaching the replacements. Line numbers are
        # unique per document and the new set starts at 1 again, so a single
        # flush would try to insert line 1 while the old line 1 is still there —
        # SQLAlchemy has no reason to order the deletes first, and the unique
        # constraint fires.
        document.lines.clear()
        await db.flush()
        document.lines = replacement
    await db.flush()
    return document


async def delete_draft(db: AsyncSession, *, company: Company, document_pk: int) -> None:
    """Discard a draft.

    Legitimate precisely because a draft has no number: nothing was issued, so
    nothing is missing from the series afterwards. The same call on an issued
    document is refused — that one is corrected with a kreditnota.
    """
    document = await get_document(db, company=company, document_pk=document_pk)
    _assert_draft(document)
    await db.delete(document)
    await db.flush()


def _assert_draft(document: SalesDocument) -> None:
    if document.is_issued:
        raise InvoiceNotDraftError(
            f"faktura {document.document_number} er utstedt og kan ikke endres; "
            f"en utstedt faktura rettes med kreditnota"
        )


# --- Issuing -------------------------------------------------------------------------


async def _next_document_number(db: AsyncSession, company: Company) -> str:
    """Take the next number from the company's series.

    ``with_for_update`` locks the counter row for the rest of the transaction, so
    two concurrent issues queue rather than both reading the same value. SQLite
    renders no locking clause and ignores it, which is fine: the unique
    constraint on ``(company_id, document_number)`` is the backstop that turns a
    race into a failed request instead of two invoices sharing a number.
    """
    series = await db.scalar(
        select(DocumentSeries).where(DocumentSeries.company_id == company.id).with_for_update()
    )
    if series is None:
        series = DocumentSeries(company_id=company.id, next_number=1)
        db.add(series)
        await db.flush()
    number = series.next_number
    series.next_number = number + 1
    await db.flush()
    return str(number)


async def peek_next_number(db: AsyncSession, *, company: Company) -> int:
    """What :func:`_next_document_number` would hand out, without taking it.

    No lock and no write, because this is a preview for a form and not a claim on
    the series. Two people looking at a new invoice at the same time see the same
    number, and whichever issues first gets it — which is the honest outcome. The
    alternative, reserving on sight, would let an abandoned draft take a number
    out of the series and leave the gap an auditor reads as a deleted invoice.

    A company that has never issued anything has no series row yet; the answer is
    then 1, which is what :func:`_next_document_number` would create.
    """
    series = await db.scalar(
        select(DocumentSeries.next_number).where(DocumentSeries.company_id == company.id)
    )
    return series if series is not None else 1


async def _sales_journal(db: AsyncSession, company: Company) -> Journal:
    journal = await db.scalar(
        select(Journal).where(
            Journal.company_id == company.id, Journal.journal_id == SALES_JOURNAL_ID
        )
    )
    if journal is None:
        journal = Journal(
            company_id=company.id,
            journal_id=SALES_JOURNAL_ID,
            description=SALES_JOURNAL_DESCRIPTION,
            journal_type=SALES_JOURNAL_TYPE,
        )
        db.add(journal)
        await db.flush()
    return journal


async def _accounts(db: AsyncSession, company: Company, numbers: set[str]) -> dict[str, Account]:
    found = await db.scalars(
        select(Account).where(Account.company_id == company.id, Account.account_number.in_(numbers))
    )
    accounts = {account.account_number: account for account in found}
    missing = numbers - accounts.keys()
    if missing:
        raise ChartIncompleteError(
            f"kontoplanen mangler konto {', '.join(sorted(missing))}, som fakturaen skal føres på"
        )
    return accounts


async def _build_voucher(
    db: AsyncSession,
    *,
    company: Company,
    document: SalesDocument,
    computed: DocumentTotals,
    journal: Journal,
    voucher_no: str,
    financial_year: int,
    created_by_user_id: int | None = None,
) -> Transaction:
    """The double entry behind an issued invoice.

    Debit kundefordringer with the gross, credit each income account with its net,
    credit utgående merverdiavgift with the tax. Tax information sits on the
    income lines — the basis lines — never on the VAT line, which is the classic
    SAF-T exporter bug (see :func:`~myntos.domain.ledger.posting.apply_tax`).

    Income lines are grouped by VAT code rather than left one-per-invoice-line.
    The code determines the account, the rate and the presentation category all
    at once, so grouping by it produces exactly the partition § 5-1-5 asks the
    document to show — and that is what makes the ledger's VAT agree with the
    document's by construction rather than by coincidence.
    """
    assert document.issue_date is not None  # noqa: S101 - set by issue() before this
    grouped: dict[str, list[SalesDocumentLine]] = {}
    for line in document.lines:
        grouped.setdefault(line.tax_code, []).append(line)

    numbers = {RECEIVABLES_ACCOUNT, OUTPUT_VAT_ACCOUNT} | {
        line.income_account_number for line in document.lines
    }
    accounts = await _accounts(db, company, numbers)
    tax_codes = await _tax_codes(db, company, set(grouped))

    party = document.party
    label = f"Faktura {document.document_number}"

    receivable = TransactionLine(
        record_id="1",
        account=accounts[RECEIVABLES_ACCOUNT],
        party=party,
        description=f"{label}, {document.buyer_name}",
        debit=computed.payable,
        credit=ZERO,
    )
    lines = [receivable]

    vat_total = ZERO
    for index, (tax_code, group) in enumerate(sorted(grouped.items()), start=2):
        net = sum((line.net_amount for line in group), start=ZERO)
        treatment = category_for(tax_code, reverse_charge=document.reverse_charge)
        # The rate actually charged: a reverse-charged or exempt supply carries
        # none even where the underlying goods would attract one.
        rate = group[0].tax_rate if treatment.category is VatCategory.STANDARD else ZERO
        vat = posting.compute_tax(net, rate)
        vat_total += vat
        income = TransactionLine(
            record_id=str(index),
            account=accounts[group[0].income_account_number],
            party=party,
            description=label,
            debit=ZERO,
            credit=net,
        )
        posting.set_tax_information(
            income, tax_code=tax_codes[tax_code], rate=rate, basis=net, amount=vat
        )
        lines.append(income)

    if vat_total != computed.tax_total:  # pragma: no cover - guards a partition bug
        raise posting.PostingError(
            f"merverdiavgiften i bilaget ({vat_total}) er ikke den samme som på "
            f"fakturaen ({computed.tax_total})"
        )
    if vat_total > ZERO:
        lines.append(
            TransactionLine(
                record_id=str(len(lines) + 1),
                account=accounts[OUTPUT_VAT_ACCOUNT],
                description=f"Utgående mva, {label}",
                debit=ZERO,
                credit=vat_total,
            )
        )

    transaction = Transaction(
        journal_id=journal.id,
        transaction_id=voucher_no,
        financial_year=financial_year,
        period=document.issue_date.month,
        transaction_date=document.issue_date,
        gl_posting_date=document.issue_date,
        description=f"{label}, {document.buyer_name}",
        voucher_type=document.document_type.value,
        # SAF-T SourceDocumentID: the invoice number this entry documents.
        source_document_id=document.document_number,
        # Whoever issued the faktura entered this voucher too — it is the same
        # button. Carried through from issue() rather than left empty, so the
        # register attributes an invoice the same way it attributes a payment.
        created_by_user_id=created_by_user_id,
    )
    transaction.lines = lines
    return transaction


async def _tax_codes(db: AsyncSession, company: Company, codes: set[str]) -> dict[str, TaxCode]:
    found = await db.scalars(
        select(TaxCode).where(
            TaxCode.company_id == company.id,
            TaxCode.code.in_(codes),
        )
    )
    resolved = {tax.code: tax for tax in found}
    missing = codes - resolved.keys()
    if missing:
        raise UnknownTaxCodeError(
            f"mva-kodene {', '.join(sorted(missing))} finnes ikke for dette selskapet"
        )
    return resolved


async def _voucher_number(db: AsyncSession, journal: Journal, year: int) -> str:
    """The next bilagsnummer in the sales journal, matching the register's format."""
    highest = await db.scalar(
        select(func.max(Transaction.transaction_id)).where(
            Transaction.journal_id == journal.id, Transaction.financial_year == year
        )
    )
    number = 1
    if highest is not None:
        _, _, suffix = str(highest).partition("-")
        if suffix.isdigit():
            number = int(suffix) + 1
    return f"{year}-{number:04d}"


async def issue(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    document_pk: int,
    user_id: int | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> SalesDocument:
    """Issue a draft: check it, number it, post it, and file it.

    The order is not negotiable. Compliance runs **first**, on the document as it
    stands, because everything after it is irreversible — there is no way to take
    a number back out of the series, and no edit path to fix a posted voucher.

    ``today`` and ``now`` are parameters rather than clock reads so a caller — a
    test, or a future backdated-issue feature — decides what the document's date
    is, and so the e-invoice rule's cutover can be exercised from both sides.
    """
    document = await get_document(db, company=company, document_pk=document_pk)
    _assert_draft(document)
    if document.currency_code != "NOK":
        raise UnsupportedCurrencyError("bare fakturaer i norske kroner støttes foreløpig")

    issue_date = today or utc_now().date()
    # Re-snapshot: the company may have been corrected since the draft started,
    # and the document has to say what is true when it is issued.
    _snapshot_seller(document, company)

    violations = compliance.check(document, today=issue_date)
    blocking = compliance.blocking(violations)
    if blocking:
        raise InvoiceNotCompliantError(blocking)

    financial_year = await ledger_service.ensure_financial_year(
        db, company, issue_date.year, on=issue_date
    )
    document.document_number = await _next_document_number(db, company)
    document.issue_date = issue_date
    document.issued_at = now or utc_now()
    document.status = InvoiceStatus.UTSTEDT

    # The KID, now that there is a number for it to carry. Here rather than in
    # the API layer because it has to be set before the EHF and the PDF are
    # rendered below — a payment reference the printed invoice does not show is
    # a payment nobody can reconcile.
    #
    # ``document.party`` is eagerly loaded by _document_query, so reading it here
    # is not the lazy-relationship trap that _archive_pdf works around.
    if document.payment_reference_auto:
        document.payment_reference = kid.for_document_number(
            document.document_number,
            customer_number=document.party.party_id if document.party is not None else None,
        )

    computed = totals(document)
    journal = await _sales_journal(db, company)
    transaction = await _build_voucher(
        db,
        company=company,
        document=document,
        computed=computed,
        journal=journal,
        voucher_no=await _voucher_number(db, journal, financial_year.year),
        financial_year=financial_year.year,
        created_by_user_id=user_id,
    )
    posting.post(transaction, now=document.issued_at)
    db.add(transaction)
    await db.flush()
    document.transaction_pk = transaction.id

    # Frozen here, not regenerated on download: from 2027 this XML *is* the sales
    # document for a business customer, and a document that is rebuilt on every
    # request is one that can quietly change after it was sent.
    document.ehf_xml = ehf.render(document)
    await _archive_pdf(
        db, storage, company=company, document=document, transaction=transaction, user_id=user_id
    )
    await db.flush()
    return document


async def _archive_pdf(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    document: SalesDocument,
    transaction: Transaction,
    user_id: int | None,
) -> None:
    """File the rendered PDF in the append-only bilag archive.

    The sales document has to be kept for five years in a form that can be read
    (bokføringsloven § 13). The EHF XML is kept on the row, but XML is not what a
    person opens in 2031 — so the readable copy goes into the same archive as
    every uploaded bilag, attached to the voucher it documents.

    The voucher is passed in rather than reached through
    ``document.transaction``: that relationship is lazy, and touching a lazy
    relationship on an async session raises rather than loading it.
    """
    await bilag_service.attach_document(
        db,
        storage,
        company=company,
        transaction=transaction,
        content=pdf.render(document),
        filename=pdf.filename_for(document),
        uploaded_by_user_id=user_id,
    )


# --- Crediting -------------------------------------------------------------------------


async def credit_note(
    db: AsyncSession,
    storage: DocumentStorage,
    *,
    company: Company,
    document_pk: int,
    note: str | None = None,
    user_id: int | None = None,
    today: date | None = None,
    now: datetime | None = None,
) -> SalesDocument:
    """Reverse an issued faktura with a kreditnota (§ 5-2-7).

    The credit note is issued immediately — it is not a draft. A half-written
    correction sitting next to the invoice it corrects would leave the books
    showing revenue the seller has already agreed is not owed.

    The ledger side goes through
    :func:`~myntos.domain.ledger.posting.build_reversal` rather than being
    assembled again from the lines. That keeps ``reverses_transaction_pk`` set,
    which is what makes the correction legible to an auditor and stops the same
    invoice being credited twice at the ledger level as well as at this one.
    """
    original = await get_document(db, company=company, document_pk=document_pk)
    if original.is_credit_note:
        raise NotCreditableError("en kreditnota kan ikke krediteres")
    if not original.is_issued:
        raise NotCreditableError(
            "fakturaen er ikke utstedt ennå; et utkast slettes i stedet for å krediteres"
        )
    existing = await db.scalar(
        select(SalesDocument.id).where(SalesDocument.credits_document_pk == original.id)
    )
    if existing is not None:
        raise AlreadyCreditedError("fakturaen er allerede kreditert")

    issue_date = today or utc_now().date()
    issued_at = now or utc_now()

    credit = SalesDocument(
        company_id=company.id,
        document_type=DocumentType.KREDITNOTA,
        status=InvoiceStatus.UTSTEDT,
        document_number=await _next_document_number(db, company),
        issue_date=issue_date,
        issued_at=issued_at,
        credits_document_pk=original.id,
        party_id=original.party_id,
        buyer_name=original.buyer_name,
        buyer_orgnr=original.buyer_orgnr,
        buyer_address_line=original.buyer_address_line,
        buyer_postal_code=original.buyer_postal_code,
        buyer_city=original.buyer_city,
        buyer_country=original.buyer_country,
        buyer_is_business=original.buyer_is_business,
        seller_name=original.seller_name,
        seller_orgnr=original.seller_orgnr,
        seller_address_line=original.seller_address_line,
        seller_postal_code=original.seller_postal_code,
        seller_city=original.seller_city,
        seller_country=original.seller_country,
        seller_vat_registered=original.seller_vat_registered,
        seller_in_foretaksregisteret=original.seller_in_foretaksregisteret,
        seller_organisation_form=original.seller_organisation_form,
        seller_bank_account=original.seller_bank_account,
        bank_account_id=original.bank_account_id,
        delivery_date=original.delivery_date,
        delivery_place=original.delivery_place,
        currency_code=original.currency_code,
        buyer_reference=original.buyer_reference,
        order_reference=original.order_reference,
        # No KID, and none assigned. A kreditnota is not paid — it reverses
        # something that was — so a payment reference on it would invite the
        # customer's bank to reconcile a payment against a reversal. The invoice
        # it credits keeps its own.
        payment_reference_auto=False,
        reverse_charge=original.reverse_charge,
        note=note or f"Kreditering av faktura {original.document_number}",
    )
    # The same lines, at the same prices and rates. A credit note that recomputed
    # them would use today's rate on a supply that was taxed at last year's.
    credit.lines = [
        SalesDocumentLine(
            line_no=line.line_no,
            description=line.description,
            quantity=line.quantity,
            unit_code=line.unit_code,
            unit_price=line.unit_price,
            discount_rate=line.discount_rate,
            tax_code=line.tax_code,
            tax_rate=line.tax_rate,
            income_account_number=line.income_account_number,
            net_amount=line.net_amount,
            vat_amount=line.vat_amount,
        )
        for line in original.lines
    ]
    db.add(credit)
    await db.flush()

    # Always set in practice — a document only reaches "utstedt" through issue(),
    # which posts a voucher — but the column is nullable, so the credit note is
    # still produced rather than crashing if it somehow is not.
    if original.transaction_pk is not None:
        reversal = await _reverse_voucher(
            db,
            company=company,
            original_pk=original.transaction_pk,
            on=issue_date,
            created_by_user_id=user_id,
        )
        posting.post(reversal, now=issued_at)
        db.add(reversal)
        await db.flush()
        credit.transaction_pk = reversal.id
        credit.ehf_xml = ehf.render(credit, original)
        await _archive_pdf(
            db, storage, company=company, document=credit, transaction=reversal, user_id=user_id
        )
    else:  # pragma: no cover - unreachable through the public API
        credit.ehf_xml = ehf.render(credit, original)
    await db.flush()
    return credit


async def _reverse_voucher(
    db: AsyncSession,
    *,
    company: Company,
    original_pk: int,
    on: date,
    created_by_user_id: int | None = None,
) -> Transaction:
    """Build the reversing voucher for the invoice's ledger entry.

    Dated on the credit note's own date rather than the invoice's: unlike a
    mistyped payment, a credited invoice is not an error being erased but a
    change that happened later, and backdating it into a period that may already
    have been reported would restate an MVA-melding that was correct when filed.
    """
    original = await ledger_service.get_voucher(db, company=company, voucher_pk=original_pk)
    financial_year = await ledger_service.ensure_financial_year(db, company, on.year, on=on)
    journal = await _sales_journal(db, company)
    reversal = posting.build_reversal(
        original,
        transaction_id=await _voucher_number(db, journal, financial_year.year),
        gl_posting_date=on,
        period=on.month,
        financial_year=financial_year.year,
    )
    reversal.transaction_date = on
    reversal.reverses_transaction_pk = original.id
    # The person crediting, not the person who invoiced. Those differ often
    # enough — a colleague credits what somebody else sent — that copying the
    # original's author would misattribute the correction.
    reversal.created_by_user_id = created_by_user_id
    return reversal


# --- Presentation helpers ---------------------------------------------------------------


def check_now(document: SalesDocument, *, today: date | None = None) -> list[compliance.Violation]:
    """What still stands between this draft and a lawful issue."""
    return compliance.check(document, today=today or utc_now().date())
