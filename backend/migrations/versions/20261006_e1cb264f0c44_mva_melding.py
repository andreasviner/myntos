"""The MVA-melding: VAT settings, the official codes, and a table of filings.

Four changes, one revision, because each of them is only useful with the others.

**33 is the 12 % sales code, and 32 is fish.** The seeded table called code 32
"Utgående mva, lav sats (12 %)". In Skatteetaten's code list
(``kodelister/mvaKodeSAFT.xml``) 32 is the sale of fish and other wild marine
resources at 11,11 %, and 12 % sales are 33. Every existing company's row is
renamed in place, which keeps its id, so every voucher posted under it now reads
as what it always was: a 12 % sale. The rate snapshotted on those lines is
0.12 and is not touched. Draft and issued sales-document lines carry the code as
a string and are renamed with it; the frozen EHF and PDF of an issued invoice
never named the code and are not changed.

What it infers: that a row with code "32" and a 0.12 rate is the mislabelled 12 %
code. What it refuses to guess: a company that already has a "33" of its own, or
a "32" at some other rate. Those are left exactly as they are, and the MVA page
will say a code cannot be reported rather than report it as something.

**The rest of the official list** (12, 14, 15, 32, 51, 84, 85, 88, 89, 91, 92) is
added to every company, rated from the year its books start, like
``d4e1a83b62c7`` did for the first import codes and for the same reason.

**Two settings on the company**: how long a VAT period is (``toMaaneder``, the
law's default) and which kind of melding it files (``alminnelig``). Backfilled
with those defaults, which is what every company has been assumed to be.

**``mva_filing`` and ``mva_filing_document``**, see
:mod:`myntos.domain.mva.models`.

Written against Core tables for the reason ``d4e1a83b62c7`` gives.

Revision ID: e1cb264f0c44
Revises: a2c7f31b9d64
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

from myntos.db.base import NAMING_CONVENTION

revision: str = "e1cb264f0c44"
down_revision: str | None = "a2c7f31b9d64"
branch_labels: None = None
depends_on: None = None

LOW_RATE = Decimal("0.120000")
LOW_RATE_DESCRIPTION = "Utgående mva, lav sats (12 %)"
OLD_LOW_RATE_DESCRIPTION = "Utgående mva, lav sats (12 %)"

# (code, description, standard_tax_code, rate)
TAX_CODES: tuple[tuple[str, str, str, str], ...] = (
    ("12", "Inngående mva, kjøp av fisk og marine ressurser (11,11 %)", "12", "0.111100"),
    ("14", "Mva betalt ved innførsel, høy sats (25 %)", "14", "0.250000"),
    ("15", "Mva betalt ved innførsel, middels sats (15 %)", "15", "0.150000"),
    ("32", "Utgående mva, salg av fisk og marine ressurser (11,11 %)", "32", "0.111100"),
    ("51", "Salg av klimakvoter og gull til næringsdrivende (0 %)", "51", "0.000000"),
    ("84", "Innførsel av varer, middels sats (15 %), uten fradragsrett", "84", "0.150000"),
    ("85", "Innførsel av varer som det ikke skal beregnes mva av (0 %)", "85", "0.000000"),
    ("88", "Tjenester kjøpt fra utlandet, lav sats (12 %), med fradragsrett", "88", "0.120000"),
    ("89", "Tjenester kjøpt fra utlandet, lav sats (12 %), uten fradragsrett", "89", "0.120000"),
    ("91", "Kjøp av klimakvoter og gull, høy sats (25 %), med fradragsrett", "91", "0.250000"),
    ("92", "Kjøp av klimakvoter og gull, høy sats (25 %), uten fradragsrett", "92", "0.250000"),
)

company = sa.table("company", sa.column("id", sa.Integer))
financial_year = sa.table(
    "financial_year",
    sa.column("company_id", sa.Integer),
    sa.column("year", sa.Integer),
)
tax_code = sa.table(
    "tax_code",
    sa.column("id", sa.Integer),
    sa.column("company_id", sa.Integer),
    sa.column("code", sa.String),
    sa.column("description", sa.String),
    sa.column("standard_tax_code", sa.String),
    sa.column("is_reportable", sa.Boolean),
)
tax_rate = sa.table(
    "tax_rate",
    sa.column("tax_code_id", sa.Integer),
    sa.column("valid_from", sa.Date),
    sa.column("rate", sa.Numeric(9, 6)),
)
gl_transaction_line = sa.table(
    "gl_transaction_line",
    sa.column("tax_code_id", sa.Integer),
)
sales_document = sa.table(
    "sales_document",
    sa.column("id", sa.Integer),
    sa.column("company_id", sa.Integer),
)
sales_document_line = sa.table(
    "sales_document_line",
    sa.column("document_pk", sa.Integer),
    sa.column("tax_code", sa.String),
    sa.column("tax_rate", sa.Numeric(9, 6)),
)


def _company_table(*extra: sa.Column[object]) -> sa.Table:
    """The shape of ``company`` before this revision, plus anything passed in."""
    return sa.Table(
        "company",
        sa.MetaData(naming_convention=NAMING_CONVENTION),
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("orgnr", sa.String(length=9), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("address_line", sa.String(length=255), nullable=True),
        sa.Column("postal_code", sa.String(length=16), nullable=True),
        sa.Column("city", sa.String(length=128), nullable=True),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("vat_registered", sa.Boolean(), nullable=False),
        sa.Column("in_foretaksregisteret", sa.Boolean(), nullable=False),
        sa.Column("organisation_form", sa.String(length=16), nullable=True),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("ki_enabled", sa.Boolean(), nullable=False),
        sa.Column("industry_code", sa.String(length=16), nullable=True),
        sa.Column("industry_description", sa.String(length=255), nullable=True),
        sa.Column("website", sa.String(length=255), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("parent_orgnr", sa.String(length=9), nullable=True),
        sa.Column("register_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("verified_by_user_id", sa.Integer(), nullable=True),
        sa.Column("altinn_party_id", sa.Integer(), nullable=True),
        *extra,
        sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_company_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["verified_by_user_id"],
            ["app_user.id"],
            name="fk_company_verified_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company"),
        sa.UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
    )


def _rename_low_rate_code(bind: sa.Connection, company_id: int) -> None:
    rows = bind.execute(
        sa.select(tax_code.c.id, tax_code.c.code).where(
            tax_code.c.company_id == company_id, tax_code.c.code.in_(("32", "33"))
        )
    ).fetchall()
    codes = {code: pk for pk, code in rows}
    if "33" in codes or "32" not in codes:
        return
    rates = [
        rate
        for (rate,) in bind.execute(
            sa.select(tax_rate.c.rate).where(tax_rate.c.tax_code_id == codes["32"])
        ).fetchall()
    ]
    if not rates or any(Decimal(rate) != LOW_RATE for rate in rates):
        return
    bind.execute(
        tax_code.update()
        .where(tax_code.c.id == codes["32"])
        .values(code="33", standard_tax_code="33", description=LOW_RATE_DESCRIPTION)
    )
    bind.execute(
        sales_document_line.update()
        .where(
            sales_document_line.c.tax_code == "32",
            sales_document_line.c.tax_rate == LOW_RATE,
            sales_document_line.c.document_pk.in_(
                sa.select(sales_document.c.id).where(sales_document.c.company_id == company_id)
            ),
        )
        .values(tax_code="33")
    )


def _add_codes(bind: sa.Connection, company_id: int, opens: date) -> None:
    for code, description, standard, rate in TAX_CODES:
        exists = bind.execute(
            sa.select(tax_code.c.id).where(
                tax_code.c.company_id == company_id, tax_code.c.code == code
            )
        ).scalar()
        if exists is not None:
            continue
        bind.execute(
            tax_code.insert().values(
                company_id=company_id,
                code=code,
                description=description,
                standard_tax_code=standard,
                is_reportable=True,
            )
        )
        code_id = bind.execute(
            sa.select(tax_code.c.id).where(
                tax_code.c.company_id == company_id, tax_code.c.code == code
            )
        ).scalar()
        bind.execute(
            tax_rate.insert().values(tax_code_id=code_id, valid_from=opens, rate=Decimal(rate))
        )


def upgrade() -> None:
    with op.batch_alter_table("company", copy_from=_company_table()) as batch:
        batch.add_column(
            sa.Column(
                "vat_period_type", sa.String(length=32), nullable=False, server_default="toMaaneder"
            )
        )
        batch.add_column(
            sa.Column(
                "vat_category", sa.String(length=32), nullable=False, server_default="alminnelig"
            )
        )
    with op.batch_alter_table("company") as batch:
        batch.alter_column("vat_period_type", server_default=None)
        batch.alter_column("vat_category", server_default=None)

    bind = op.get_bind()
    for (company_id,) in bind.execute(sa.select(company.c.id)).fetchall():
        # The rename runs before the additions, so the new fish "32" can be
        # added to a company whose old "32" just became "33".
        _rename_low_rate_code(bind, company_id)
        start = bind.execute(
            sa.select(sa.func.min(financial_year.c.year)).where(
                financial_year.c.company_id == company_id
            )
        ).scalar()
        if start is None:
            continue
        _add_codes(bind, company_id, date(int(start), 1, 1))

    json_type = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
    op.create_table(
        "mva_filing",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("period_type", sa.String(length=32), nullable=False),
        sa.Column("period_index", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("reference", sa.String(length=36), nullable=False),
        sa.Column("melding_xml", sa.Text(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("fastsatt_merverdiavgift", sa.Numeric(38, 2), nullable=False),
        sa.Column("lines", json_type, nullable=False),
        sa.Column("merknad", sa.Text(), nullable=True),
        sa.Column("validation_status", sa.String(length=64), nullable=False),
        sa.Column("validation_xml", sa.Text(), nullable=False),
        sa.Column(
            "validated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("created_by_user_id", sa.Integer(), nullable=True),
        sa.Column("altinn_instance_id", sa.String(length=80), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("submitted_by_user_id", sa.Integer(), nullable=True),
        sa.Column("submitted_by_name", sa.String(length=255), nullable=True),
        sa.Column("feedback_received_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.String(length=1000), nullable=True),
        sa.Column("payment_kid", sa.String(length=64), nullable=True),
        sa.Column("payment_amount", sa.Numeric(38, 2), nullable=True),
        sa.Column("payment_due", sa.Date(), nullable=True),
        sa.Column("payment_account", sa.String(length=32), nullable=True),
        sa.Column("settlement_transaction_pk", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_mva_filing_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["created_by_user_id"],
            ["app_user.id"],
            name="fk_mva_filing_created_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["submitted_by_user_id"],
            ["app_user.id"],
            name="fk_mva_filing_submitted_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["settlement_transaction_pk"],
            ["gl_transaction.id"],
            name="fk_mva_filing_settlement_transaction_pk_gl_transaction",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mva_filing"),
        sa.UniqueConstraint("reference", name="uq_mva_filing_reference"),
    )
    op.create_index(
        "ix_mva_filing_period",
        "mva_filing",
        ["company_id", "year", "period_type", "period_index"],
    )
    op.create_table(
        "mva_filing_document",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("filing_id", sa.Integer(), nullable=False),
        sa.Column("data_type", sa.String(length=64), nullable=False),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("storage_key", sa.String(length=512), nullable=False),
        sa.Column(
            "fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.ForeignKeyConstraint(
            ["filing_id"],
            ["mva_filing.id"],
            name="fk_mva_filing_document_filing_id_mva_filing",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_mva_filing_document"),
        sa.UniqueConstraint("filing_id", "data_type", name="uq_mva_filing_document_filing_id"),
        sa.UniqueConstraint("storage_key", name="uq_mva_filing_document_storage_key"),
    )


def downgrade() -> None:
    op.drop_table("mva_filing_document")
    op.drop_index("ix_mva_filing_period", table_name="mva_filing")
    op.drop_table("mva_filing")

    bind = op.get_bind()
    codes = [code for code, *_ in TAX_CODES]
    # Only where nothing was posted with them, for the reason d4e1a83b62c7
    # gives. A company that has booked a fish sale keeps its "32", and then its
    # "33" cannot go back to being called "32" either.
    used_codes = sa.select(gl_transaction_line.c.tax_code_id).where(
        gl_transaction_line.c.tax_code_id.isnot(None)
    )
    doomed = sa.select(tax_code.c.id).where(
        tax_code.c.code.in_(codes), tax_code.c.id.notin_(used_codes)
    )
    bind.execute(tax_rate.delete().where(tax_rate.c.tax_code_id.in_(doomed)))
    bind.execute(tax_code.delete().where(tax_code.c.id.in_(doomed)))

    for (company_id,) in bind.execute(sa.select(company.c.id)).fetchall():
        taken = bind.execute(
            sa.select(tax_code.c.id).where(
                tax_code.c.company_id == company_id, tax_code.c.code == "32"
            )
        ).scalar()
        if taken is not None:
            continue
        bind.execute(
            tax_code.update()
            .where(tax_code.c.company_id == company_id, tax_code.c.code == "33")
            .values(code="32", standard_tax_code="32", description=OLD_LOW_RATE_DESCRIPTION)
        )
        bind.execute(
            sales_document_line.update()
            .where(
                sales_document_line.c.tax_code == "33",
                sales_document_line.c.document_pk.in_(
                    sa.select(sales_document.c.id).where(sales_document.c.company_id == company_id)
                ),
            )
            .values(tax_code="32")
        )

    with op.batch_alter_table(
        "company",
        copy_from=_company_table(
            sa.Column("vat_period_type", sa.String(length=32), nullable=False),
            sa.Column("vat_category", sa.String(length=32), nullable=False),
        ),
    ) as batch:
        batch.drop_column("vat_category")
        batch.drop_column("vat_period_type")
