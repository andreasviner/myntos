"""SAF-T-aligned accounting core.

The table layout follows what a SAF-T Financial export must be able to emit
(docs/integrations/saf-t.md), because a schema that cannot produce the export
is a schema that has to be migrated later under audit pressure:

- **MasterFiles** → :class:`Account` (+ :class:`AccountGrouping`,
  :class:`AccountOpeningBalance`), :class:`Party`, :class:`TaxCode`
  (+ :class:`TaxRate`), :class:`DimensionType` / :class:`DimensionValue`
- **GeneralLedgerEntries** → :class:`Journal` → :class:`Transaction` →
  :class:`TransactionLine` (+ :class:`TransactionLineAnalysis`)

Three decisions are worth stating up front, because they are the ones that
are expensive to reverse:

1. **Amounts are debit/credit pairs, never signed values.** SAF-T requires it,
   and it removes a whole class of sign-convention bugs. Exactly one side of a
   line carries an amount; the other is ``0.00``.
2. **Rates are snapshotted onto the posted line.** ``tax_rate`` on
   :class:`TransactionLine` is a copy of the rate that applied on the
   transaction date, not a lookup through :class:`TaxRate`. A posted entry
   must keep saying what it said when it was posted, even after the rate
   tables change — see the 2025-07-01 water/wastewater cut, which moved one
   SAF-T code to a new rate mid-year.
3. **Only opening balances are stored; closing balances are computed.** A
   stored closing balance is a second source of truth that silently drifts
   from the postings. SAF-T wants both, so the exporter derives closing from
   opening plus the year's lines.
"""

from __future__ import annotations

import enum
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.db.base import Base
from myntos.domain.identity.models import Organization

# SAF-T v1.40 widened the monetary datatype to 38 digits. Scale 2 is øre —
# the MVA-melding reports øre and explicitly must not be rounded to kroner.
MONEY = Numeric(38, 2)

# Rates are stored as fractions, not percentages: 0.250000, 0.150000,
# 0.111100. Six decimals is enough for 11.11 % on wild marine resources.
RATE = Numeric(9, 6)

# Placeholder mandated by SAF-T for a grouping that genuinely has no
# næringsspesifikasjon post (interim/clearing accounts). An empty element
# fails validation; "NA" is the prescribed value.
GROUPING_NOT_APPLICABLE = "NA"

# Standard VAT codes that must never reach the MVA-melding. Kept here next to
# the tax tables rather than in the (not yet written) MVA aggregator, because
# it is a property of the code list itself — see docs/integrations/mva.md.
NON_REPORTABLE_STANDARD_TAX_CODES = frozenset({"0", "7", "20", "21", "22"})


class AccountType(enum.StrEnum):
    """Whether an account belongs on the balance sheet or the P&L.

    This is our own classification, not a SAF-T field. The posting engine and
    the closing-balance derivation need to know which accounts roll over into
    the next financial year and which are closed to equity.
    """

    EIENDEL = "eiendel"
    GJELD = "gjeld"
    EGENKAPITAL = "egenkapital"
    INNTEKT = "inntekt"
    KOSTNAD = "kostnad"

    @property
    def is_balance_account(self) -> bool:
        return self in {AccountType.EIENDEL, AccountType.GJELD, AccountType.EGENKAPITAL}


class PartyType(enum.StrEnum):
    """SAF-T keeps customers and suppliers in separate MasterFiles sections."""

    KUNDE = "kunde"
    LEVERANDOR = "leverandor"


class Company(Base):
    """The reporting entity — SAF-T ``Header`` / ``Company``.

    One row per bookkeeping-obligated business, owned by the
    :class:`~myntos.domain.identity.models.Organization` whose members keep its
    books. A regnskapsfører is one organization with many companies.

    ``orgnr`` is unique **per organization**, not globally: two accounting firms
    may each keep a set of books for the same client, and a global constraint
    would make the first one to add that orgnr block the second — a tenancy leak
    disguised as a data-quality rule.
    """

    __tablename__ = "company"
    __table_args__ = (
        CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    organization_id: Mapped[int] = mapped_column(ForeignKey("organization.id", ondelete="CASCADE"))
    orgnr: Mapped[str] = mapped_column(String(9))
    name: Mapped[str] = mapped_column(String(255))
    address_line: Mapped[str | None] = mapped_column(String(255))
    postal_code: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(2), default="NO")
    # SAF-T Header/DefaultCurrencyCode. Kept configurable, but everything in
    # the ledger is carried in NOK regardless — see TransactionLine.
    currency_code: Mapped[str] = mapped_column(String(3), default="NOK")

    # --- What a salgsdokument has to be able to state about the seller ---
    # bokføringsforskriften § 5-1-2 requires the seller's orgnr to be followed by
    # the letters "MVA" when registered, and merverdiavgiftsloven forbids
    # charging output VAT when not. Defaulting to False is the safe direction:
    # an unregistered business that accidentally invoices VAT still owes it.
    vat_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    # Decides the "Foretaksregisteret" marking and the head-office address that
    # must accompany it (§ 5-1-2 second paragraph). Aksjeselskap,
    # allmennaksjeselskap and NUF are always registered there.
    in_foretaksregisteret: Mapped[bool] = mapped_column(Boolean, default=False)
    # Enhetsregisteret's organisasjonsform code ("AS", "ENK", "NUF" …). Kept so
    # the marking above can be checked against what the register says rather than
    # against what somebody ticked.
    organisation_form: Mapped[str | None] = mapped_column(String(16))
    # Where invoices are paid. Carried into the e-invoice as
    # PayeeFinancialAccount, and printed on the PDF.
    bank_account: Mapped[str | None] = mapped_column(String(34))

    organization: Mapped[Organization] = relationship()
    financial_years: Mapped[list[FinancialYear]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    accounts: Mapped[list[Account]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )


class FinancialYear(Base):
    """A bookkeeping year (regnskapsår).

    SAF-T allows exactly one financial year per exported file, and the
    account-grouping code lists are year-versioned, so the year is a
    first-class entity rather than a derived ``EXTRACT(year FROM date)``.
    """

    __tablename__ = "financial_year"
    __table_args__ = (
        UniqueConstraint("company_id", "year", name="uq_financial_year_company_id"),
        CheckConstraint("end_date > start_date", name="ends_after_start"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    year: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date] = mapped_column(Date)
    # Set when the year is locked. Nothing may be posted into a closed year.
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship(back_populates="financial_years")


class Account(Base):
    """A general ledger account — SAF-T ``MasterFiles/GeneralLedgerAccounts``."""

    __tablename__ = "account"
    __table_args__ = (
        UniqueConstraint("company_id", "account_number", name="uq_account_company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # SAF-T AccountID. A string, not an integer: kontoplaner in the wild use
    # leading zeros and suffixed accounts.
    account_number: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(255))
    account_type: Mapped[AccountType] = mapped_column(
        # values_callable stores "eiendel" rather than SQLAlchemy's default of
        # the member *name*, "EIENDEL" — the values are what the rest of the
        # system and any SQL an accountant reads will use.
        Enum(AccountType, name="account_type", values_callable=lambda e: [m.value for m in e])
    )
    created_date: Mapped[date | None] = mapped_column(Date)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    company: Mapped[Company] = relationship(back_populates="accounts")
    groupings: Mapped[list[AccountGrouping]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )
    opening_balances: Mapped[list[AccountOpeningBalance]] = relationship(
        back_populates="account", cascade="all, delete-orphan"
    )


class AccountGrouping(Base):
    """Per-year næringsspesifikasjon mapping for one account.

    Since SAF-T v1.30 the old 2-/4-digit ``StandardAccountID`` is gone: every
    account carries a ``GroupingCategory`` + ``GroupingCode`` instead, the code
    lists are versioned per year, and history must be retained. That is why
    this is a child table and not two columns on :class:`Account` — an account
    remapped for 2027 must still export 2026 with its 2026 mapping.
    """

    __tablename__ = "account_grouping"
    __table_args__ = (
        UniqueConstraint("account_id", "financial_year", name="uq_account_grouping_account_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    financial_year: Mapped[int] = mapped_column(Integer)
    # e.g. "salgsinntekt" / "3000", or GROUPING_NOT_APPLICABLE for accounts
    # with no næringsspesifikasjon post.
    grouping_category: Mapped[str] = mapped_column(String(64))
    grouping_code: Mapped[str] = mapped_column(String(64))

    account: Mapped[Account] = relationship(back_populates="groupings")


class AccountOpeningBalance(Base):
    """Opening debit/credit balance per account and year.

    Closing balances are intentionally absent: they are derived from this row
    plus the year's posted lines, so the two can never disagree.
    """

    __tablename__ = "account_opening_balance"
    __table_args__ = (
        UniqueConstraint(
            "account_id", "financial_year", name="uq_account_opening_balance_account_id"
        ),
        CheckConstraint(
            "opening_debit >= 0 AND opening_credit >= 0", name="balances_are_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="CASCADE"))
    financial_year: Mapped[int] = mapped_column(Integer)
    opening_debit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    opening_credit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))

    account: Mapped[Account] = relationship(back_populates="opening_balances")


class TaxCode(Base):
    """An internal VAT code mapped to a Norwegian ``StandardTaxCode``.

    Internal codes are free-form, but SAF-T has required the mapping to the
    standard code since v1.30 — and it is the same code dimension that drives
    the MVA-melding, so the mapping is load-bearing in two places.
    """

    __tablename__ = "tax_code"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_tax_code_company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(255))
    standard_tax_code: Mapped[str] = mapped_column(String(8))
    # False for standard codes 0, 7, 20, 21 and 22, which must be excluded
    # from the MVA-melding. Denormalised from the standard code so the
    # aggregator filters in SQL rather than in Python.
    is_reportable: Mapped[bool] = mapped_column(Boolean, default=True)

    rates: Mapped[list[TaxRate]] = relationship(
        back_populates="tax_code", cascade="all, delete-orphan"
    )


class TaxRate(Base):
    """A date-effective rate for one tax code.

    Rates change mid-year and mid-code: the 15 % cut for water and wastewater
    on 2025-07-01 landed on SAF-T code 31, which food already used at 15 %.
    A single ``rate`` column on :class:`TaxCode` misvalidates every period that
    straddles such a change, so validity intervals are the storage model.

    ``valid_to`` is exclusive and ``NULL`` means "still in force".
    """

    __tablename__ = "tax_rate"
    __table_args__ = (
        UniqueConstraint("tax_code_id", "valid_from", name="uq_tax_rate_tax_code_id"),
        CheckConstraint("valid_to IS NULL OR valid_to > valid_from", name="valid_to_after_from"),
        CheckConstraint("rate >= 0", name="rate_is_non_negative"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    tax_code_id: Mapped[int] = mapped_column(ForeignKey("tax_code.id", ondelete="CASCADE"))
    valid_from: Mapped[date] = mapped_column(Date)
    valid_to: Mapped[date | None] = mapped_column(Date)
    rate: Mapped[Decimal] = mapped_column(RATE)

    tax_code: Mapped[TaxCode] = relationship(back_populates="rates")


class Party(Base):
    """A customer or supplier — SAF-T ``Customers`` / ``Suppliers``.

    ``balance_account_id`` is SAF-T's ``BalanceAccountStructure``: the GL
    account this party's sub-ledger settles against (kundefordringer /
    leverandørgjeld).
    """

    __tablename__ = "party"
    __table_args__ = (
        UniqueConstraint("company_id", "party_type", "party_id", name="uq_party_company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    party_type: Mapped[PartyType] = mapped_column(
        Enum(PartyType, name="party_type", values_callable=lambda e: [m.value for m in e])
    )
    # SAF-T CustomerID / SupplierID — the number the user knows the party by.
    party_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    orgnr: Mapped[str | None] = mapped_column(String(9))
    address_line: Mapped[str | None] = mapped_column(String(255))
    postal_code: Mapped[str | None] = mapped_column(String(16))
    city: Mapped[str | None] = mapped_column(String(128))
    country: Mapped[str] = mapped_column(String(2), default="NO")
    balance_account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="RESTRICT"))

    balance_account: Mapped[Account] = relationship()
    opening_balances: Mapped[list[PartyOpeningBalance]] = relationship(
        back_populates="party", cascade="all, delete-orphan"
    )


class PartyOpeningBalance(Base):
    """Opening balance per party and year (SAF-T requires it per customer)."""

    __tablename__ = "party_opening_balance"
    __table_args__ = (
        UniqueConstraint("party_id", "financial_year", name="uq_party_opening_balance_party_id"),
        CheckConstraint(
            "opening_debit >= 0 AND opening_credit >= 0", name="balances_are_non_negative"
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    party_id: Mapped[int] = mapped_column(ForeignKey("party.id", ondelete="CASCADE"))
    financial_year: Mapped[int] = mapped_column(Integer)
    opening_debit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    opening_credit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))

    party: Mapped[Party] = relationship(back_populates="opening_balances")


class DimensionType(Base):
    """An analysis dimension — SAF-T ``AnalysisType`` (avdeling, prosjekt …)."""

    __tablename__ = "dimension_type"
    __table_args__ = (UniqueConstraint("company_id", "code", name="uq_dimension_type_company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(255))

    values: Mapped[list[DimensionValue]] = relationship(
        back_populates="dimension_type", cascade="all, delete-orphan"
    )


class DimensionValue(Base):
    """One value within a dimension — SAF-T ``AnalysisID``."""

    __tablename__ = "dimension_value"
    __table_args__ = (
        UniqueConstraint("dimension_type_id", "value", name="uq_dimension_value_dimension_type_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    dimension_type_id: Mapped[int] = mapped_column(
        ForeignKey("dimension_type.id", ondelete="CASCADE")
    )
    value: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))

    dimension_type: Mapped[DimensionType] = relationship(back_populates="values")


class Journal(Base):
    """A journal (bilagsjournal) — SAF-T ``GeneralLedgerEntries/Journal``."""

    __tablename__ = "journal"
    __table_args__ = (UniqueConstraint("company_id", "journal_id", name="uq_journal_company_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    journal_id: Mapped[str] = mapped_column(String(64))
    description: Mapped[str] = mapped_column(String(255))
    # SAF-T Journal/Type — free-form ("salg", "kjøp", "bank", "diverse").
    journal_type: Mapped[str] = mapped_column(String(64))

    transactions: Mapped[list[Transaction]] = relationship(
        back_populates="journal", cascade="all, delete-orphan"
    )


class Transaction(Base):
    """A voucher (bilag) — SAF-T ``Journal/Transaction``.

    ``posted_at`` is the immutability switch. Before it is set the transaction
    is a draft and may be edited freely; once set, bokføringsloven's
    requirement that a posted entry not be altered takes over and the posting
    engine refuses further changes. Corrections are made by posting a reversing
    transaction, never by editing history.

    ``reverses_transaction_pk`` is what makes that correction legible. Without
    it a reversal is just another voucher that happens to carry mirrored
    amounts, and neither a user interface nor an auditor can tell which entry
    it undoes — or whether one has already been undone twice. The unique index
    enforces at most one reversal per voucher.
    """

    __tablename__ = "gl_transaction"
    __table_args__ = (
        UniqueConstraint("journal_id", "transaction_id", name="uq_gl_transaction_journal_id"),
        CheckConstraint("period BETWEEN 1 AND 12", name="period_is_a_month"),
        # An index rather than a UNIQUE constraint: SQLite can add a unique
        # index to an existing table, while a unique constraint would force a
        # full table rebuild in the migration.
        Index("uq_gl_transaction_reverses", "reverses_transaction_pk", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    journal_id: Mapped[int] = mapped_column(ForeignKey("journal.id", ondelete="CASCADE"))
    # SAF-T TransactionID — the bilagsnummer the user sees.
    transaction_id: Mapped[str] = mapped_column(String(64))
    financial_year: Mapped[int] = mapped_column(Integer)
    period: Mapped[int] = mapped_column(Integer)
    # SAF-T keeps three distinct dates and they genuinely differ: the document
    # date, the date the system recorded it, and the date it hits the ledger.
    transaction_date: Mapped[date] = mapped_column(Date)
    gl_posting_date: Mapped[date] = mapped_column(Date)
    system_entry_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    description: Mapped[str] = mapped_column(String(255))
    # SAF-T TransactionType — bilagstype.
    voucher_type: Mapped[str | None] = mapped_column(String(64))
    # SAF-T SourceDocumentID. A plain reference for now; roadmap phase 2 turns
    # this into a foreign key once the bilag archive exists.
    source_document_id: Mapped[str | None] = mapped_column(String(128))
    posted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Set on a reversing voucher, pointing at the one it undoes.
    reverses_transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="CASCADE")
    )

    journal: Mapped[Journal] = relationship(back_populates="transactions")
    lines: Mapped[list[TransactionLine]] = relationship(
        back_populates="transaction", cascade="all, delete-orphan"
    )

    @property
    def is_posted(self) -> bool:
        return self.posted_at is not None


class TransactionLine(Base):
    """One ledger line — SAF-T ``Transaction/Line``.

    Amounts are a debit/credit pair in NOK. Foreign-currency amounts sit
    alongside in ``currency_amount`` / ``exchange_rate``; the NOK columns are
    always populated, which is what SAF-T v1.40 requires for tax amounts when
    the transaction currency is not NOK (VAT is fixed in NOK,
    bokføringsforskriften § 4-2).

    ``TaxInformation`` belongs on the **basis** line, not on the VAT account
    line — the classic exporter bug. This model does not prevent it, but the
    posting engine's :func:`~myntos.domain.ledger.posting.apply_tax` puts it in
    the right place.
    """

    __tablename__ = "gl_transaction_line"
    __table_args__ = (
        UniqueConstraint(
            "transaction_pk", "record_id", name="uq_gl_transaction_line_transaction_pk"
        ),
        CheckConstraint("debit >= 0 AND credit >= 0", name="amounts_are_non_negative"),
        CheckConstraint("NOT (debit > 0 AND credit > 0)", name="one_side_only"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    transaction_pk: Mapped[int] = mapped_column(ForeignKey("gl_transaction.id", ondelete="CASCADE"))
    # SAF-T RecordID — unique within the transaction.
    record_id: Mapped[str] = mapped_column(String(64))
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id", ondelete="RESTRICT"))
    party_id: Mapped[int | None] = mapped_column(ForeignKey("party.id", ondelete="RESTRICT"))
    description: Mapped[str] = mapped_column(String(255))

    debit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))
    credit: Mapped[Decimal] = mapped_column(MONEY, default=Decimal("0.00"))

    currency_code: Mapped[str] = mapped_column(String(3), default="NOK")
    currency_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    exchange_rate: Mapped[Decimal | None] = mapped_column(Numeric(18, 8))

    tax_code_id: Mapped[int | None] = mapped_column(ForeignKey("tax_code.id", ondelete="RESTRICT"))
    # Snapshot of the rate that applied on the transaction date — see the
    # module docstring on why this is not a lookup.
    tax_rate: Mapped[Decimal | None] = mapped_column(RATE)
    tax_base: Mapped[Decimal | None] = mapped_column(MONEY)
    # Mandatory whenever TaxInformation is present; 0.00 is a valid value and
    # an empty element fails SAF-T validation.
    debit_tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)
    credit_tax_amount: Mapped[Decimal | None] = mapped_column(MONEY)

    transaction: Mapped[Transaction] = relationship(back_populates="lines")
    account: Mapped[Account] = relationship()
    # SAF-T CustomerID / SupplierID on the line: who the trade was with.
    party: Mapped[Party | None] = relationship()
    tax_code: Mapped[TaxCode | None] = relationship()
    analysis: Mapped[list[TransactionLineAnalysis]] = relationship(
        back_populates="line", cascade="all, delete-orphan"
    )


class TransactionLineAnalysis(Base):
    """A dimension allocation on a line — SAF-T ``Line/Analysis`` (repeatable).

    The table is ``gl_line_analysis`` rather than the more symmetrical
    ``gl_transaction_line_analysis``: the latter pushes the generated foreign
    key name to 66 characters, and PostgreSQL truncates identifiers at 63,
    which makes the database disagree with the model in a way that only
    surfaces as autogenerate churn much later.
    """

    __tablename__ = "gl_line_analysis"
    __table_args__ = (
        UniqueConstraint("line_id", "dimension_value_id", name="uq_gl_line_analysis_line_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("gl_transaction_line.id", ondelete="CASCADE"))
    dimension_value_id: Mapped[int] = mapped_column(
        ForeignKey("dimension_value.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)

    line: Mapped[TransactionLine] = relationship(back_populates="analysis")
    dimension_value: Mapped[DimensionValue] = relationship()
