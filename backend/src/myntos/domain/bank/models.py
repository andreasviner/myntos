"""Imported bank statements and the lines they carry.

Three tables, one per thing that exists separately:

- :class:`BankStatementFile` is **the file**, as received. It is primary
  documentation — the bank's own account of what moved — so it is kept the
  way every other bilag is kept: the bytes in :mod:`myntos.storage`, the digest
  and the key here, and no delete path anywhere (bokføringsloven § 13).
- :class:`BankStatement` is **one statement** inside it: one account, one
  period, the opening and closing balance. A camt.053 file may hold several,
  one per account or per day.
- :class:`BankStatementLine` is **one movement** of money, and the thing the
  register is reconciled against.

Why the file is not a row in ``bilag_attachment``: an attachment documents one
voucher, and ``transaction_pk`` on it is NOT NULL for that reason. A statement
documents every voucher on the account for a period, including the ones nobody
has entered yet, so it has no single voucher to hang from. It follows the
precedent of :class:`~myntos.domain.mva.models.MvaFilingDocument` instead: the
same storage, the same digest, the same append-only rule, with the metadata
beside the thing it documents.

**Reconciling is a link, not a posting.** ``BankStatementLine.transaction_pk``
points at the voucher that books the movement. Setting it changes nothing in
the ledger, which is the point: a posted voucher is history, and confirming
that the bank agrees with it must not be able to alter it. Clearing the link is
allowed for the same reason; it was our claim, not the bank's and not the
ledger's.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from myntos.core.clock import utc_now
from myntos.db.base import Base
from myntos.domain.ledger.cashflow import CashFlowDirection
from myntos.domain.ledger.models import MONEY, CompanyBankAccount, Transaction


class BankStatementFile(Base):
    """An uploaded camt.053 file, kept whole as primary documentation."""

    __tablename__ = "bank_statement_file"
    __table_args__ = (
        # The same bytes uploaded twice are the same document, and the second
        # upload is answered with the first import rather than archived again.
        # Per company: two sets of books may both be sent the same file (the ENK
        # and the AS that took over its account), and each keeps its own copy.
        UniqueConstraint("company_id", "sha256", name="uq_bank_statement_file_sha256"),
        CheckConstraint("byte_size > 0", name="byte_size_is_positive"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(128))
    byte_size: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(512), unique=True)
    # "001.02", "001.08": which camt.053 the bank sent, for when a bank changes
    # format and somebody needs to know which files came before it.
    camt_version: Mapped[str] = mapped_column(String(16))
    message_id: Mapped[str | None] = mapped_column(String(128))
    # Python-side, as on Attachment: the response is built from the row just
    # flushed, and a server default would be an expired attribute whose refresh
    # is a lazy load inside a running request.
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    # SET NULL for the reason Attachment gives: erasing the person who uploaded
    # a statement must not erase the statement.
    uploaded_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    statements: Mapped[list[BankStatement]] = relationship(
        back_populates="file", order_by="BankStatement.id"
    )


class BankStatement(Base):
    """One ``Stmt``: one of the company's accounts over one period."""

    __tablename__ = "bank_statement"
    __table_args__ = (
        CheckConstraint("to_date >= from_date", name="ends_after_start"),
        Index("ix_bank_statement_account_period", "bank_account_id", "from_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # Denormalised, as on Attachment, so every tenancy check is one predicate.
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    file_id: Mapped[int] = mapped_column(ForeignKey("bank_statement_file.id", ondelete="CASCADE"))
    # CASCADE, and that is safe only because of where the documentation lives.
    # Deleting an account on /organisasjon/ takes its statements and lines with
    # it, which is the reconciliation work and nothing the law asks us to keep;
    # the file itself hangs off the company, not the account, so the bank's own
    # record stays in the archive and can be imported again. RESTRICT was the
    # first choice and would have turned that delete into a 500 nobody could
    # act on.
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("company_bank_account.id", ondelete="CASCADE")
    )
    # Stmt/Id as the bank wrote it. Not unique: banks reuse it across
    # re-exports of the same day, which is exactly the case dedup has to handle.
    statement_ref: Mapped[str] = mapped_column(String(128))
    from_date: Mapped[date] = mapped_column(Date)
    to_date: Mapped[date] = mapped_column(Date)
    # Signed, unlike everything else money-shaped in the ledger. A balance is a
    # position rather than a movement, and an overdrawn account is a negative
    # number on the bank's own statement; splitting it into a debit/credit pair
    # would invent a posting that does not exist.
    opening_balance: Mapped[Decimal] = mapped_column(MONEY)
    closing_balance: Mapped[Decimal] = mapped_column(MONEY)
    # How many of the file's lines for this statement were new, and how many
    # were already held from an earlier, overlapping import.
    lines_new: Mapped[int] = mapped_column(Integer, default=0)
    lines_known: Mapped[int] = mapped_column(Integer, default=0)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    file: Mapped[BankStatementFile] = relationship(back_populates="statements")
    bank_account: Mapped[CompanyBankAccount] = relationship()


class BankStatementLine(Base):
    """One movement on the account, and the voucher that books it, if any.

    ``dedup_key`` is unique per bank account, and that constraint is the whole
    of the idempotency: re-importing a file, or one whose period overlaps a file
    already imported, finds the lines it already has and adds only the rest.
    How the key is made is in :func:`myntos.domain.bank.camt.dedup_keys`.
    """

    __tablename__ = "bank_statement_line"
    __table_args__ = (
        UniqueConstraint("bank_account_id", "dedup_key", name="uq_bank_statement_line_dedup_key"),
        CheckConstraint("amount > 0", name="amount_is_positive"),
        # One movement of money is booked by one voucher. A partial index would
        # be the precise statement (NULL is "not yet"), and a plain unique one
        # says the same thing on both dialects: NULLs never collide.
        UniqueConstraint("transaction_pk", name="uq_bank_statement_line_transaction_pk"),
        Index("ix_bank_statement_line_company_date", "company_id", "booking_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    company_id: Mapped[int] = mapped_column(ForeignKey("company.id", ondelete="CASCADE"))
    # The statement that first brought the line in. A later overlapping import
    # repeats it and does not take it over.
    statement_id: Mapped[int] = mapped_column(ForeignKey("bank_statement.id", ondelete="CASCADE"))
    bank_account_id: Mapped[int] = mapped_column(
        ForeignKey("company_bank_account.id", ondelete="CASCADE")
    )
    dedup_key: Mapped[str] = mapped_column(String(160))

    booking_date: Mapped[date] = mapped_column(Date)
    value_date: Mapped[date | None] = mapped_column(Date)
    # Positive, with the direction beside it, as the register itself is shaped.
    amount: Mapped[Decimal] = mapped_column(MONEY)
    direction: Mapped[CashFlowDirection] = mapped_column(
        Enum(
            CashFlowDirection,
            name="cash_flow_direction",
            values_callable=lambda e: [m.value for m in e],
            # A CHECK rather than a PostgreSQL enum type: a new direction is
            # not coming, and a type would be one more object a migration has
            # to create and drop in the right order.
            native_enum=False,
            create_constraint=True,
        )
    )
    currency: Mapped[str] = mapped_column(String(3), default="NOK")
    counterparty_name: Mapped[str | None] = mapped_column(String(255))
    remittance: Mapped[str | None] = mapped_column(String(1000))
    kid: Mapped[str | None] = mapped_column(String(64))
    end_to_end_id: Mapped[str | None] = mapped_column(String(64))
    bank_reference: Mapped[str | None] = mapped_column(String(128))
    additional_info: Mapped[str | None] = mapped_column(String(500))

    # --- Reconciliation ------------------------------------------------------
    #
    # SET NULL rather than RESTRICT or CASCADE. Vouchers are never deleted by
    # the application, so this only fires when a whole company goes, and then
    # the order the two cascades run in must not decide whether it can.
    transaction_pk: Mapped[int | None] = mapped_column(
        ForeignKey("gl_transaction.id", ondelete="SET NULL")
    )
    # "bekreftet" when somebody confirmed an existing voucher, "opprettet" when
    # the voucher was posted from this line. Kept because the two say different
    # things to an auditor: one is a person agreeing that an entry explains a
    # movement, the other is the movement being where the entry came from.
    match_kind: Mapped[str | None] = mapped_column(String(16))
    matched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    matched_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_user.id", ondelete="SET NULL")
    )

    statement: Mapped[BankStatement] = relationship()
    transaction: Mapped[Transaction | None] = relationship()
