"""Salgsdokument — the invoice as bokføringsforskriften § 5-1 defines it.

Three tables: the document, its lines, and the number series that feeds it.

Four decisions shape the schema, and each of them is expensive to reverse:

1. **The counterparty is snapshotted onto the document.** ``buyer_name`` and the
   seller columns are copies taken at issue, not joins through :class:`Party` and
   :class:`~myntos.domain.ledger.models.Company`. Both of those are editable —
   :func:`~myntos.domain.ledger.parties.update_party` exists precisely so a
   customer who moves can be corrected — and an issued invoice must keep saying
   what it said when the customer received it. This is the same reasoning that
   snapshots ``tax_rate`` onto a posted ledger line.

2. **Totals are derived, never stored.** Line amounts are stored because rounding
   makes them a decision rather than a recomputation; the document totals are
   plain sums over frozen lines and would only be a second source of truth. See
   :mod:`myntos.domain.faktura.totals`.

3. **The number is assigned at issue, from a counter, and only once.** § 5-1-3
   requires salgsdokumentasjon to be pre-numbered or machine-numbered in a way
   that can be checked for completeness. A draft therefore has no number at all:
   numbering drafts is how a series acquires gaps, and a gap is exactly what an
   auditor reads as a deleted invoice.

4. **The XML that was sent is kept.** ``ehf_xml`` holds the e-invoice as it was
   generated at issue. Regenerating it later from the row would produce today's
   answer to a question that was settled then, and from 2027 the XML *is* the
   sales document for a business customer — not a rendering of it.
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
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.core.clock import utc_now
from myntos.db.base import Base
from myntos.domain.ledger.models import MONEY, RATE, Company, Party, Transaction

# Unit prices carry more precision than øre — an hourly rate of 1 234.5625 is a
# legitimate thing to invoice. The *line amount* it produces is still rounded to
# øre; this is the input to that rounding, not a second currency scale.
PRICE = Numeric(38, 4)

# Quantities are fractional (7.5 hours, 0.25 tonnes) and are not money.
QUANTITY = Numeric(24, 6)

# UN/ECE Recommendation 20, which EN 16931 requires for the unit of measure.
# "EA" (each) is the honest default for "one of something".
DEFAULT_UNIT_CODE = "EA"

# Payment terms default. Not a legal figure — § 5-1-1 nr. 5 only requires that a
# forfallsdato be stated, not what it is — but a default has to be something, and
# 14 days is the ordinary Norwegian business term.
DEFAULT_PAYMENT_TERM_DAYS = 14


class DocumentType(enum.StrEnum):
    """Faktura or the kreditnota that reverses one.

    Both live in one table and one number series. § 5-2-7 makes a kreditnota the
    mirror of an invoice rather than a different kind of object, and a shared
    series is what makes completeness provable by counting.
    """

    FAKTURA = "faktura"
    KREDITNOTA = "kreditnota"

    @property
    def ubl_type_code(self) -> str:
        """UNCL 1001 document type code, as EN 16931 requires it."""
        return "380" if self is DocumentType.FAKTURA else "381"


class InvoiceStatus(enum.StrEnum):
    """Whether the document has left the building."""

    UTKAST = "utkast"
    UTSTEDT = "utstedt"


class SalesDocument(Base):
    """One faktura or kreditnota."""

    __tablename__ = "sales_document"
    __table_args__ = (
        # Per company, not globally: two organizations' books are unrelated, and
        # the series has to be complete within one set of books.
        UniqueConstraint("company_id", "document_number", name="uq_sales_document_company_id"),
        # A number belongs to an issued document and to nothing else. Stated as a
        # constraint because the alternative — trusting every code path that
        # writes a status — is how a numbered draft eventually appears.
        CheckConstraint(
            "(status = 'utkast' AND document_number IS NULL AND issued_at IS NULL) "
            "OR (status = 'utstedt' AND document_number IS NOT NULL AND issued_at IS NOT NULL)",
            name="number_iff_issued",
        ),
        # There is deliberately no constraint tying due_date to issue_date. A
        # forfallsdato in the past is unusual but lawful — an invoice issued late
        # for a March delivery, with terms counted from the delivery, is exactly
        # the case § 5-2-2 already treats as a missed deadline rather than an
        # impossibility. A constraint here would turn that warning into a crash
        # and leave the sale undocumented, which is the worse breach.
        # myntos.domain.faktura.compliance warns about it instead.
        # One kreditnota per faktura, for the same reason a voucher may be
        # reversed once: crediting twice would halve the revenue a second time.
        # An index rather than a constraint so SQLite can add it without a table
        # rebuild — see migration 5c81ea2d4f16.
        Index("uq_sales_document_credits", "credits_document_pk", unique=True),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    document_type: Mapped[DocumentType] = mapped_column(
        Enum(
            DocumentType,
            name="sales_document_type",
            values_callable=lambda e: [m.value for m in e],
        )
    )
    status: Mapped[InvoiceStatus] = mapped_column(
        Enum(
            InvoiceStatus,
            name="sales_document_status",
            values_callable=lambda e: [m.value for m in e],
        ),
        default=InvoiceStatus.UTKAST,
    )
    # § 5-1-1 nr. 1. NULL until issued; see the check constraint above.
    document_number: Mapped[str | None] = mapped_column(String(32))

    # --- Who ------------------------------------------------------------------
    # The customer this was written for. Kept as a foreign key *as well as* the
    # snapshot below, so the invoice still shows up under the right customer when
    # they change their name — the snapshot is what the document says, the key is
    # who it belongs to.
    party_id: Mapped[int | None] = mapped_column(ForeignKey("party.id", ondelete="RESTRICT"))

    # § 5-1-2 first paragraph: the buyer, by name and address or orgnr.
    buyer_name: Mapped[str] = mapped_column(String(255))
    buyer_orgnr: Mapped[str | None] = mapped_column(String(9))
    buyer_address_line: Mapped[str | None] = mapped_column(String(255))
    buyer_postal_code: Mapped[str | None] = mapped_column(String(16))
    buyer_city: Mapped[str | None] = mapped_column(String(128))
    buyer_country: Mapped[str] = mapped_column(String(2), default="NO")
    # True when the buyer is bokføringspliktig. This is what decides whether the
    # e-invoice requirement in bokføringsloven applies to this document at all —
    # a sale to a consumer is outside it. Asked rather than inferred: an orgnr is
    # evidence of a business, but its absence is not evidence of a consumer.
    buyer_is_business: Mapped[bool] = mapped_column(Boolean, default=True)

    # § 5-1-2 second paragraph: the seller. Snapshotted for the same reason.
    seller_name: Mapped[str] = mapped_column(String(255))
    seller_orgnr: Mapped[str] = mapped_column(String(9))
    seller_address_line: Mapped[str | None] = mapped_column(String(255))
    seller_postal_code: Mapped[str | None] = mapped_column(String(16))
    seller_city: Mapped[str | None] = mapped_column(String(128))
    seller_country: Mapped[str] = mapped_column(String(2), default="NO")
    # Decides the "MVA" suffix after the orgnr (§ 5-1-2 second paragraph) and
    # whether output VAT may be charged at all.
    seller_vat_registered: Mapped[bool] = mapped_column(Boolean, default=False)
    # Decides the "Foretaksregisteret" marking, which aksjeselskap and NUF must
    # carry (§ 5-1-2 second paragraph, third sentence).
    seller_in_foretaksregisteret: Mapped[bool] = mapped_column(Boolean, default=False)
    # Snapshotted alongside the flag above so the marking can be *checked* rather
    # than trusted: an AS whose flag was never set would otherwise issue a
    # document silently missing a mandatory word. See
    # myntos.domain.faktura.compliance.FORETAKSREGISTERET_FORMS.
    seller_organisation_form: Mapped[str | None] = mapped_column(String(16))
    # What the document *says* the account is: eleven digits, copied from the
    # company at issue and never read back through the key below. A closed
    # account, or one corrected after the fact, must not change what an issued
    # invoice claimed — that is the same rule the seller's name and address
    # follow.
    seller_bank_account: Mapped[str | None] = mapped_column(String(34))
    # Which of the company's accounts this document is paid into — the *choice*,
    # as opposed to the snapshot above. NULL means "whichever is primary", which
    # is what a draft nobody picked for follows, and what an invoice issued
    # before the company had a second account will always have said.
    #
    # SET NULL and not RESTRICT: closing a bank account is an ordinary thing to
    # do, and it must not be blocked by a draft from March. The draft falls back
    # to the primary the next time it is saved; the issued document beside it
    # keeps its snapshot and notices nothing.
    bank_account_id: Mapped[int | None] = mapped_column(
        ForeignKey("company_bank_account.id", ondelete="SET NULL")
    )

    # --- When and what for ------------------------------------------------------
    # § 5-1-1 nr. 1. NULL until issued, then the dokumentasjonsdato.
    issue_date: Mapped[date | None] = mapped_column(Date)
    # § 5-1-1 nr. 4: leveringstidspunkt and leveringssted.
    delivery_date: Mapped[date | None] = mapped_column(Date)
    delivery_place: Mapped[str | None] = mapped_column(String(255))
    # § 5-1-1 nr. 5: forfallsdato.
    due_date: Mapped[date | None] = mapped_column(Date)

    currency_code: Mapped[str] = mapped_column(String(3), default="NOK")
    # KID, when the seller uses one. Carried into the e-invoice as PaymentID so
    # the payment reconciles automatically at the other end.
    #
    # NULL on a draft even when one is coming: a KID carries the invoice number
    # (myntos.domain.faktura.kid), and a draft has none. Issue fills it in.
    payment_reference: Mapped[str | None] = mapped_column(String(32))
    # Whether issue should assign that KID itself. Default on — almost nobody
    # wants to invent a payment reference by hand, and one derived from the
    # invoice number is what makes an incoming payment reconcilable at all.
    # Turning it off is how a seller says either "no KID" or "this one, mine".
    payment_reference_auto: Mapped[bool] = mapped_column(Boolean, default=True)
    # "Deres referanse" (EN 16931 BT-10). Norwegian public-sector buyers reject an
    # EHF invoice without it, which is the main reason it is a column and not a
    # line in the free-text note.
    buyer_reference: Mapped[str | None] = mapped_column(String(128))
    order_reference: Mapped[str | None] = mapped_column(String(128))
    note: Mapped[str | None] = mapped_column(String(1000))

    # § 5-1-1 nr. 7. Document-level rather than per line: the marking applies to
    # the supply, and a document that mixed reverse-charged and ordinary lines
    # could not carry the prescribed marking unambiguously.
    reverse_charge: Mapped[bool] = mapped_column(Boolean, default=False)

    # --- Links out --------------------------------------------------------------
    # The posted voucher. Set at issue: an invoice is revenue and a receivable at
    # the moment it is issued, not when it is paid.
    transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="RESTRICT")
    )
    # Set on a kreditnota, naming the faktura it reverses (§ 5-2-7).
    credits_document_pk: Mapped[int | None] = mapped_column(
        ForeignKey("sales_document.id", ondelete="RESTRICT")
    )
    # The e-invoice exactly as it was generated at issue.
    ehf_xml: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    issued_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    company: Mapped[Company] = relationship()
    party: Mapped[Party | None] = relationship()
    transaction: Mapped[Transaction | None] = relationship()
    lines: Mapped[list[SalesDocumentLine]] = relationship(
        back_populates="document",
        cascade="all, delete-orphan",
        order_by="SalesDocumentLine.line_no",
    )

    @property
    def is_issued(self) -> bool:
        return self.status is InvoiceStatus.UTSTEDT

    @property
    def is_credit_note(self) -> bool:
        return self.document_type is DocumentType.KREDITNOTA


class SalesDocumentLine(Base):
    """One line — § 5-1-1 nr. 3, the art og omfang of what was sold.

    ``net_amount`` and ``vat_amount`` are stored rather than derived from
    quantity and price. Rounding to øre is a decision, and the number the
    customer was shown is the one that has to be reproducible years later; a
    recomputation would be free to reach a different one if the rounding rule
    ever changed.
    """

    __tablename__ = "sales_document_line"
    __table_args__ = (
        UniqueConstraint("document_pk", "line_no", name="uq_sales_document_line_document_pk"),
        CheckConstraint("net_amount >= 0 AND vat_amount >= 0", name="amounts_are_non_negative"),
        CheckConstraint("quantity <> 0", name="quantity_is_not_zero"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    document_pk: Mapped[int] = mapped_column(ForeignKey("sales_document.id", ondelete="CASCADE"))
    line_no: Mapped[int] = mapped_column(Integer)

    description: Mapped[str] = mapped_column(String(500))
    quantity: Mapped[Decimal] = mapped_column(QUANTITY, default=Decimal("1"))
    unit_code: Mapped[str] = mapped_column(String(8), default=DEFAULT_UNIT_CODE)
    unit_price: Mapped[Decimal] = mapped_column(PRICE)
    # A fraction, like every other rate in this system: 0.250000 is 25 %.
    discount_rate: Mapped[Decimal] = mapped_column(RATE, default=Decimal("0.000000"))

    # Snapshotted from the company's tax table at the moment the line was
    # written, for the same reason a posted ledger line snapshots its rate.
    tax_code: Mapped[str] = mapped_column(String(32))
    tax_rate: Mapped[Decimal] = mapped_column(RATE)
    # Which 3xxx account the revenue lands on, and through it which
    # næringsspesifikasjon post. Stored as the number so the line can be posted
    # without a second lookup and still reads correctly in an export.
    income_account_number: Mapped[str] = mapped_column(String(32))

    net_amount: Mapped[Decimal] = mapped_column(MONEY)
    vat_amount: Mapped[Decimal] = mapped_column(MONEY)

    document: Mapped[SalesDocument] = relationship(back_populates="lines")

    @property
    def gross_amount(self) -> Decimal:
        return self.net_amount + self.vat_amount


class DocumentSeries(Base):
    """The next number in a company's salgsdokument series.

    A counter row rather than ``MAX(document_number) + 1``: the maximum is taken
    over a column that is NULL for every draft and is a string, and it would
    happily reissue a number after the highest-numbered document was somehow
    removed. A counter only ever moves forward.

    The unique constraint on ``(company_id, document_number)`` is the real
    backstop — two concurrent issues that both read the same counter produce one
    winner and one retry, rather than two invoices sharing a number.
    """

    __tablename__ = "sales_document_series"
    __table_args__ = (
        UniqueConstraint("company_id", name="uq_sales_document_series_company_id"),
        CheckConstraint("next_number > 0", name="next_number_is_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    next_number: Mapped[int] = mapped_column(Integer, default=1)
