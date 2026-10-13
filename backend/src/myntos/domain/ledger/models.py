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
    JSON,
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
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.db.base import Base
from myntos.domain.identity.models import EMAIL_LENGTH, Organization

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
# the tax tables rather than in the MVA aggregator (myntos.domain.mva.melding),
# because it is a property of the code list itself; see docs/integrations/mva.md.
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

    One row per bookkeeping-obligated business. ``organization_id`` says which
    account set it up and is what decides where a *new* company may be added;
    who may read and write *this* one is
    :class:`~myntos.domain.identity.models.CompanyMembership`, one row per
    person per company. A regnskapsfører is one account with many companies and
    a different set of colleagues on each.

    ``orgnr`` is unique **per organization**, not globally: two accounting firms
    may each keep a set of books for the same client, and a global constraint
    would make the first one to add that orgnr block the second — a tenancy leak
    disguised as a data-quality rule.

    A *confirmed* claim is the exception. ``verified_at`` is stamped when
    somebody proved through Altinn that they may act for the orgnr, and only one
    such row may exist per orgnr across the whole installation — that is what
    ``uq_company_verified_orgnr`` says. Unconfirmed rows stay unconstrained, so
    the second accounting firm can still set up its own books; what it cannot do
    is claim the same company as confirmed while the first claim stands.
    """

    __tablename__ = "company"
    __table_args__ = (
        CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
        # Partial, so it binds confirmed claims and leaves the rest alone. The
        # domain checks the same thing first and answers with a message people
        # can act on; this is the backstop for two confirmations racing, where a
        # check-then-write would let both through.
        Index(
            "uq_company_verified_orgnr",
            "orgnr",
            unique=True,
            sqlite_where=text("verified_at IS NOT NULL"),
            postgresql_where=text("verified_at IS NOT NULL"),
        ),
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
    # The day the registration in Merverdiavgiftsregisteret takes effect, when
    # it is later than the start of these books. Registration is not a property
    # of a company so much as of a date: a business crosses the threshold in
    # March, is registered from 1 May, and the purchases it made in April carry
    # no deduction however the flag reads today. NULL means "for as long as
    # these books go back", which is the only reading of the rows that existed
    # before the column did. Read through
    # :func:`myntos.domain.ledger.vatstatus.registered_on` and nowhere else.
    #
    # It decides *defaults and permissions for new entries only*. A posted
    # voucher keeps the code it was posted with whatever this says later; see
    # docs/integrations/mva.md, "Registration status".
    vat_registered_from: Mapped[date | None] = mapped_column(Date)
    # How long one MVA-melding period is, as the XSD names it ("toMaaneder",
    # "maaned", "aar"). Two months is the law's default; a month or a year is
    # something Skatteetaten grants on application, so it is a setting rather
    # than something the books could work out. See myntos.domain.mva.periods.
    vat_period_type: Mapped[str] = mapped_column(String(32), default="toMaaneder")
    # Which kind of melding the company files ("alminnelig", "primaernaering").
    # Primærnæring is farming, forestry and fishing, which file once a year on a
    # later deadline and are told apart from an ordinary yearly filer only by
    # this.
    vat_category: Mapped[str] = mapped_column(String(32), default="alminnelig")
    # Decides the "Foretaksregisteret" marking and the head-office address that
    # must accompany it (§ 5-1-2 second paragraph). Aksjeselskap,
    # allmennaksjeselskap and NUF are always registered there.
    in_foretaksregisteret: Mapped[bool] = mapped_column(Boolean, default=False)
    # Enhetsregisteret's organisasjonsform code ("AS", "ENK", "NUF" …). Kept so
    # the marking above can be checked against what the register says rather than
    # against what somebody ticked.
    organisation_form: Mapped[str | None] = mapped_column(String(16))
    # Where invoices are paid is a table of its own — see
    # :class:`CompanyBankAccount`. It used to be a column here, and one column
    # is one account: a business with a driftskonto and a skattetrekkskonto had
    # to keep the second one somewhere else, which in practice meant on a
    # sticky note beside the screen.

    # What this set of books costs, and therefore what it may use.
    #
    # Per company rather than per account, because a set of books is the thing
    # that has a size: a regnskapsfører with twenty clients is not one
    # subscription, and a private person with one company should not be priced
    # as though they might have twenty. It also puts the price next to the thing
    # being paid for, which is where somebody looks for it.
    #
    # A plain string, and nothing enforces it yet: there is no billing. It says
    # what tier these books are on so that the day there is one, the column it
    # bills from is already the right shape.
    plan: Mapped[str] = mapped_column(String(32), default="free")

    # Whether this company's documents may be shown to a model at all.
    #
    # Per company rather than per user, because it is a statement about a set of
    # books and not about a preference: the person who decides whether these
    # bilag may leave the building is the one responsible for them, and the
    # answer has to hold whoever happens to be logged in. A regnskapsfører with
    # twenty clients can say yes for nineteen and no for the one whose
    # personvernerklæring says no.
    #
    # Defaults to True, which is what the register has always done, and the
    # switch is what makes that a choice rather than a fact. Off means off:
    # no classification, no field extraction, and no upload to Løynd's
    # extractor either — the local PDF reader does the work and the proposal is
    # whatever the parser can manage on its own.
    # A Python-side default only, like every other boolean on this table: the
    # migration adds a server default to backfill the existing rows and then
    # drops it again, so one left declared here would be a phantom diff on
    # every autogenerate.
    ki_enabled: Mapped[bool] = mapped_column(Boolean, default=True)

    # Whether a key may be made for these books at all, and whether any key
    # works in them (docs/agents-and-api.md rule 3).
    #
    # Off by default, the opposite of ``ki_enabled`` above, and the difference is
    # who sends what. KI is Myntos sending a bilag to Løynd, which runs in Norway
    # and keeps nothing. A key is the company letting a tool of its own choosing
    # read the books, and that tool's model may be anywhere. Only the people
    # responsible for the books can decide that, so it starts as a no, and an
    # owner or admin turns it on after being told what it means. Turning it off
    # revokes every key rather than pausing them, so turning it on again later
    # cannot quietly bring back a key somebody had forgotten.
    agent_access_enabled: Mapped[bool] = mapped_column(Boolean, default=False)

    # Whether these books get the farming add-on: the accounts from NIBIO's
    # Landbrukets kodesystem, the farm categories in the register, and a model
    # told that a settlement from Tine is a sale (docs/landbruk.md).
    #
    # Three states, because two questions hide in one switch. NULL is "nobody
    # has said", and then the næringskode decides: 01 (jordbruk) and 02
    # (skogbruk) get it, which is right for nearly every farm and asks nothing
    # of them. True and False are a person's answer and outrank the register,
    # because a farm registered under another code exists, and so does a 01
    # company that would rather not see thirty farm categories. Read through
    # :func:`myntos.domain.ledger.landbruk.is_enabled` and nowhere else.
    #
    # An add-on and not a kind of company: Myntos is for every business, and
    # nothing here may change what a company without it sees.
    landbruk: Mapped[bool | None] = mapped_column(Boolean)

    # --- What Enhetsregisteret says ------------------------------------------
    #
    # Filled from the register rather than typed, and refreshed whenever the
    # company is confirmed or the user asks — see
    # :func:`myntos.domain.ledger.service.refresh_company_from_register`. The
    # register is the authority for every one of them, so a copy here is a cache
    # with a timestamp rather than a second opinion.
    #
    # These are the fields the system *uses*: an invoice repeats them, a filing
    # asks for them, and code reads them by name. Everything else the register
    # holds is kept too, but whole and off to one side, in
    # :class:`CompanyRegisterSnapshot` — a column per field would mean a
    # migration every time Brønnøysund adds one.

    # SN2007. The næringsspesifikasjon asks for it, which is why this one is
    # stored rather than merely shown. It also decides whether the farming
    # add-on is on when nobody has said (``landbruk`` below).
    industry_code: Mapped[str | None] = mapped_column(String(16))
    industry_description: Mapped[str | None] = mapped_column(String(255))
    # Seller contact details a salgsdokument may carry beside the address.
    website: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(64))
    email: Mapped[str | None] = mapped_column(String(EMAIL_LENGTH))
    # Set when this company is a subunit: the orgnr of the main unit it belongs
    # to. Roles are registered on the main unit only, so it is also the key a
    # role lookup follows.
    parent_orgnr: Mapped[str | None] = mapped_column(String(9))
    # When the four fields above, and the address and registration flags, were
    # last read from the register. NULL for books created before this existed,
    # and for one whose orgnr the register has never answered for.
    register_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- Confirmed ownership -------------------------------------------------
    #
    # NULL means nobody has proved they may act for this orgnr yet. The books
    # still work: bookkeeping is not gated on a government login, and a person
    # setting up on a Sunday evening should not be stopped by BankID. What the
    # confirmation settles is *whose* company this is, and it is the same
    # evidence a submission will later rest on — Altinn's own answer to "which
    # parties may this person report for".
    #
    # Stamped by myntos.domain.ledger.service.confirm_company_ownership and
    # never by a form: a column a user could tick would confirm nothing.
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who did the confirming. SET NULL rather than CASCADE: the person may leave
    # the organization, and the company stays confirmed — it was confirmed.
    verified_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # Altinn's own key for the party, as it stood at confirmation. Kept because
    # an instance is later created under it and an orgnr is not the same key.
    altinn_party_id: Mapped[int | None] = mapped_column(Integer)

    @property
    def is_verified(self) -> bool:
        return self.verified_at is not None

    @property
    def primary_bank_account(self) -> CompanyBankAccount | None:
        """The account an invoice is paid to unless it says otherwise.

        ``None`` when the company has no account at all, which is the ordinary
        state of books somebody set up ten minutes ago and the reason a
        salgsdokument cannot be issued yet. Never raises: ``bank_accounts`` is
        eagerly loaded, so this is a list scan and not a lazy query firing
        inside whatever happens to be reading it.
        """
        for account in self.bank_accounts:
            if account.is_primary:
                return account
        return None

    organization: Mapped[Organization] = relationship()
    # Lazy on purpose, and never joined into a listing: see the class docstring.
    register_snapshot: Mapped[CompanyRegisterSnapshot | None] = relationship(
        back_populates="company", cascade="all, delete-orphan", uselist=False
    )
    financial_years: Mapped[list[FinancialYear]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    accounts: Mapped[list[Account]] = relationship(
        back_populates="company", cascade="all, delete-orphan"
    )
    # Eager, unlike every other collection on this table, and the exception is
    # deliberate: the primary account decides whether an invoice can be issued
    # at all, so every response that carries a company carries its accounts,
    # and a lazy load would be a MissingGreenlet inside a running request.
    # Ordered primary-first so the interface and the invoice agree on which one
    # is "the" account without either of them having to sort.
    bank_accounts: Mapped[list[CompanyBankAccount]] = relationship(
        back_populates="company",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="(CompanyBankAccount.is_primary.desc(), CompanyBankAccount.id)",
    )


class CompanyBankAccount(Base):
    """One kontonummer the company is paid into.

    A table and not a column, because a company has as many accounts as its bank
    gave it: a driftskonto to invoice against, a skattetrekkskonto that
    skattebetalingsloven § 5-12 requires be kept apart from the operating money,
    a currency account, a savings account. The column that used to be here
    (``company.bank_account``) could hold exactly one, so everyone with two put
    the second one nowhere.

    Three rules, each one load-bearing:

    1. **The number is stored normalised**, eleven digits with no separators,
       checked by :func:`myntos.domain.ledger.bankkonto.validate_bank_account`
       before it is written. It reaches the outside world on every invoice and
       an issued salgsdokument cannot be corrected.
    2. **Unique per company, not globally.** Two sets of books may legitimately
       name the same account — a sole trader's ENK and the AS that took it over
       is the ordinary case — and a global constraint would let whoever typed it
       first lock everybody else out. Same reasoning as ``orgnr`` on
       :class:`Company`.
    3. **At most one primary**, enforced by a partial unique index rather than
       by hoping the service always clears the old one first. ``is_primary`` is
       what an invoice falls back to when it names no account of its own, so two
       of them would make "where does this get paid" depend on row order.

    Deleting one is allowed and does not rewrite history: an issued
    salgsdokument carries its own copy of the number
    (``SalesDocument.seller_bank_account``), and the foreign key from the
    document is ``SET NULL`` so a closed account takes nothing with it.
    """

    __tablename__ = "company_bank_account"
    __table_args__ = (
        UniqueConstraint("company_id", "account", name="uq_company_bank_account_number"),
        # Partial, so it binds the primary and leaves the rest alone. Being
        # checked statement by statement is what shapes the service: it demotes
        # in one flush and promotes in the next, because a single flush is free
        # to write the two updates in either order. This index is the backstop
        # for two requests promoting different rows at once, where a
        # check-then-write would let both through and leave the company with two
        # answers to "which account is this invoice paid to".
        Index(
            "uq_company_bank_account_primary",
            "company_id",
            unique=True,
            sqlite_where=text("is_primary"),
            postgresql_where=text("is_primary"),
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # Eleven digits, no separators. String and not an integer: a kontonummer is
    # a written identifier that may begin with a zero, and arithmetic on one is
    # never the right thing to do.
    account: Mapped[str] = mapped_column(String(11))
    # What the person calls it — "Drift", "Skattetrekk", "Valuta". Optional,
    # because a company with one account does not need to name it, and required
    # naming would be a form field to skip rather than a fact to record.
    label: Mapped[str | None] = mapped_column(String(64))
    # The one an invoice is paid to when it names none itself. A Python-side
    # default only, like the booleans on Company: the migration carries the
    # server default that backfills the existing rows and then drops it.
    is_primary: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    company: Mapped[Company] = relationship(back_populates="bank_accounts")


class CompanyRegisterSnapshot(Base):
    """Everything Enhetsregisteret answered about a company, kept whole.

    The columns on :class:`Company` are the handful the system reads by name —
    the næringskode a næringsspesifikasjon asks for, the address and flags an
    invoice repeats. This is the rest, and the reason it is stored rather than
    shown and discarded is that the alternative is asking Brønnøysund again for
    something we already had. Styre and revisor, kapital and formål, tidligere
    navn, stiftelsesdato: none of it is used today, and all of it is one answer
    away from being usable without a second round trip.

    Kept as JSON rather than as columns on purpose. Nothing in here is queried
    by field, the register adds fields without asking, and a column per field
    would make every one of those additions a migration. What *is* queried —
    which company, and how old the answer is — is a column.

    One row per company, replaced rather than appended: this is the current
    picture, not a history. A register answer that has been superseded says
    nothing useful about a business, only about our fetching.

    Held in its own table rather than on ``company`` because every page load
    lists companies and none of them wants a document-sized blob dragged along
    for the ride.

    It is public data — Enhetsregisteret publishes all of it — but it does name
    people, and a copy that is never refreshed is a copy that quietly goes
    wrong. ``fetched_at`` is what keeps it honest, and it is shown next to the
    data wherever the data is shown.
    """

    __tablename__ = "company_register_snapshot"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_company_register_snapshot_company_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # The orgnr the answer is *about*. Not derived from the company at read
    # time: an unconfirmed company's orgnr can still be corrected, and a
    # snapshot describing the number that was there before must be recognisable
    # as stale rather than silently reread as the new one's.
    orgnr: Mapped[str] = mapped_column(String(9))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # The unit as :class:`myntos.integrations.brreg.RegisteredUnit` renders it,
    # and the roles as the same module returns them. JSONB where we have it,
    # because a JSON column on PostgreSQL reparses on every read for nothing.
    unit: Mapped[dict[str, object]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))
    roles: Mapped[list[dict[str, object]]] = mapped_column(JSON().with_variant(JSONB, "postgresql"))

    company: Mapped[Company] = relationship(back_populates="register_snapshot")


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
    # False hides the account from *new* entries and nothing else. It is still
    # in the SAF-T file, still in every report, and every voucher already
    # posted to it still says it was; deactivating is how a kontoplan sheds an
    # account it no longer uses without rewriting the years that did use it.
    # See myntos.domain.ledger.kontoplan for which accounts may not be switched
    # off at all.
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
    # Set when a person chose this pair on the kontoplan page, and carried
    # forward with it into the next year. NULL means the seed (or a migration
    # correcting the seed) wrote it.
    #
    # The column exists for the next migration more than for the interface.
    # ``b7d40e2c9a15`` could correct every row holding the old seed's exact pair
    # because nothing then let a person edit a grouping, so "holds the seed's
    # pair" meant "was written by the seed". That stopped being true the day the
    # kontoplan became editable: a person may now choose any listed pair,
    # including one a later seed correction would want to move. A migration
    # rewriting groupings must therefore leave ``chosen_at IS NOT NULL`` rows
    # alone, which is a fact it can test rather than a guess it has to make.
    chosen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Who chose it. SET NULL for the reason every author column here gives: the
    # person may be erased, and the choice stays a choice.
    chosen_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

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
    # False hides the whole dimension from new entries, as on an account, and
    # nothing else: every line already allocated keeps its value, and the
    # report and the SAF-T file still show it. See myntos.domain.ledger.dimensions.
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

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
    # False: a driftsgren the farm has given up, a project that is over. Not
    # offered for new entries; still on every line that used it, still in the
    # report and exported with SAF-T's Status «Closed». A value that is in use
    # is never deleted (the foreign key from gl_line_analysis is RESTRICT).
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

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
        # Not unique: a replacement voucher can itself be corrected later, and
        # each link in that chain points at the entry before it. The listing
        # reads the register newest-first and then asks which of those vouchers
        # correct something, so the lookup goes through this column.
        Index("ix_gl_transaction_corrects", "corrects_transaction_pk"),
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
    # Who entered it. ``system_entry_date`` says when the system recorded the
    # voucher and said nothing about by whom, which is fine for one person and
    # useless the moment a second one has a CompanyMembership on the same books:
    # a regnskapsfører and the owner both post to the same journal, and "who
    # booked this as a private withdrawal" had no answer in the data at all.
    #
    # Nullable, and not because a voucher may lack an author. Two reasons it
    # can be NULL: rows posted before this column existed, and a voucher some
    # future job posts with nobody logged in. GDPR erasure is *not* one of them
    # any more: an account a voucher names is kept as a tombstone with its id
    # and name, precisely so this column goes on answering (docs/gdpr.md). A
    # reader must therefore treat NULL as "not recorded" and never as "nobody":
    # the register shows «ukjent» for it rather than inventing a name or quietly
    # leaving the field out.
    #
    # SET NULL rather than CASCADE, for the same reason as the bilag archive:
    # deleting the person must never delete the bookkeeping they did. A posted
    # voucher is history (bokføringsloven § 13) and outlives every account.
    #
    # Exported as SAF-T ``Transaction/SourceID`` ("details of person or
    # application that entered the transaction"), as the user's id. An earlier
    # version of this comment said the standard had no such field; it does, and
    # it is the one an auditor reads to ask who booked something.
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )
    # The key the author worked through, when they did (docs/agents-and-api.md
    # rule 6). The person above is still the one who registered the entry, as
    # bokføringsforskriften needs somebody to be; this says how. The register
    # shows "Kari Nordmann via «Claude»".
    #
    # Never set by the code that builds a voucher. It is stamped on every new
    # voucher flushed during a request a key made (myntos.api.deps), so a new
    # way of posting cannot forget it, and a voucher posted in a browser
    # session can never claim it.
    #
    # SET NULL for the same reason as the author: the voucher outlives
    # everything that made it.
    created_via_agent_key_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_key.id", ondelete="SET NULL")
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
    # Set on the replacement voucher a correction posts, pointing at the entry
    # it was written to replace.
    #
    # Correcting a posted entry produces three vouchers: the original, the
    # reversal that cancels it, and this one. ``reverses_transaction_pk`` joins
    # the first two and stops there, which left the third indistinguishable
    # from an unrelated entry posted the same day — for the register, and for
    # anyone auditing it. Without the link the only way to pair them is to
    # guess from dates and amounts, and a guess that is usually right is worse
    # here than no answer at all.
    #
    # SET NULL rather than CASCADE: this voucher is the one that is still true.
    # Losing the entry it replaced must not take the replacement with it.
    corrects_transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
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
    # The MVA-melding's ``spesifikasjon`` for this line: ``uttak``,
    # ``tapPåKrav``, ``justering`` or ``tilbakeføringAvInngåendeMerverdiavgift``,
    # spelled exactly as Skatteetaten's code list spells them
    # (:class:`myntos.domain.mva.codes.Spesifikasjon`). NULL on every ordinary
    # line, which is nearly all of them.
    #
    # On the line rather than derived from the voucher type, because the melding
    # groups lines and not vouchers (``GROUP BY (mvaKode, spesifikasjon,
    # sats)``), and because a reversal copies lines: a tap på krav undone has to
    # net against the tap på krav, not against the period's ordinary deductions.
    # Only set where tax information is set, on the same line. Not a SAF-T
    # field; the export does not read it.
    vat_specification: Mapped[str | None] = mapped_column(String(64))
    # The register category this line was booked as
    # (:mod:`myntos.domain.ledger.cashflow`), on the line describing the trade.
    #
    # The voucher's ``voucher_type`` says the same for an ordinary entry, where
    # one voucher is one category. A composite entry has several: a settlement
    # from Tine is milk sold, a levy deducted and feed set off against it, all
    # behind one bank line, and reading it back as what it was entered as needs
    # the category per line. Set by the register on every trade line it
    # writes; NULL on lines written before it existed, and on the payment and
    # VAT lines, which belong to no category. Copied by a reversal like the
    # rest of the line. Not a SAF-T field.
    entry_type: Mapped[str | None] = mapped_column(String(64))

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
        # The report per avdeling reads by value, and so does the question
        # «is this value in use», which decides whether it may be deleted. The
        # unique constraint above leads with the line and helps neither.
        Index("ix_gl_line_analysis_dimension_value_id", "dimension_value_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("gl_transaction_line.id", ondelete="CASCADE"))
    dimension_value_id: Mapped[int] = mapped_column(
        ForeignKey("dimension_value.id", ondelete="RESTRICT")
    )
    amount: Mapped[Decimal] = mapped_column(MONEY)

    line: Mapped[TransactionLine] = relationship(back_populates="analysis")
    dimension_value: Mapped[DimensionValue] = relationship()
