"""KI reading the books: the vocabulary, the per-voucher analysis, the runs, the reports.

docs/analyse.md «The tables». Six tables, every one with ``company_id`` to
``company`` (``CASCADE``), so the tenancy check is one predicate and a tag a
model made from one company's books can never be read as another's:

- ``ki_run``: one piece of background KI work (reading vouchers, a deep
  analysis, a review, a consolidation), polled by the page like
  ``saft_import``. A partial unique index allows one active run (``queued``
  or ``running``) per company and kind, on both databases, because two
  requests can both look for one and both find none.
- ``ki_tag``: a company's vocabulary, seeds copied in and tags a model added.
- ``ki_voucher_analysis``: one current summary and stamp per voucher.
- ``ki_line_tag``: the tags on each income or cost line.
- ``ki_report`` and ``ki_finding_dismissal``: what a deep analysis or a review
  produced, and «Dette er riktig» on a finding (written by later packages).

Anything about a voucher or a line cascades from it, because the purge at the
end of a deleted company's retention deletes the vouchers and a ``RESTRICT``
would stop it. The two columns that point at a person
(``ki_run.requested_by_user_id``, ``ki_finding_dismissal.dismissed_by_user_id``)
are ``SET NULL`` and in docs/gdpr.md's erasure table: erasing a person must
not take a run or a dismissal with it. ``ki_run.via_agent_key_id`` points at
``agent_key``, as every other "via key" column does.

**What it infers:** nothing. The tables start empty.

**What it refuses to guess:** any analysis. It reads no voucher, tags no line
and copies no seed into any company's vocabulary: no company is analysed by a
migration. The seeds are written the first time a run reads a company's books
(``myntos.domain.analyse.vocabulary.ensure_seeds``), and a voucher is read
only when the company has KI switched on and somebody, or a visit to the page,
starts a run.

Revision ID: 659e80f0c3f5
Revises: f5c2a8d19e74
Create Date: 2026-11-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "659e80f0c3f5"
down_revision: str | None = "f5c2a8d19e74"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# The same text as myntos.domain.analyse.models.ACTIVE_RUN_WHERE, repeated
# rather than imported so this revision keeps meaning what it meant when the
# models move on.
ACTIVE_RUN_WHERE = "status IN ('queued', 'running')"


# JSON on SQLite (the test suite), JSONB on PostgreSQL, as the models declare.
JSON_DOCUMENT = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "ki_run",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("requested_level", sa.String(length=10), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=True),
        sa.Column("period_to", sa.Date(), nullable=True),
        sa.Column("step", sa.String(length=32), nullable=True),
        sa.Column("done", sa.Integer(), nullable=False),
        sa.Column("total", sa.Integer(), nullable=False),
        sa.Column("tokens_charged", sa.BigInteger(), nullable=False),
        sa.Column("tokens_total", sa.BigInteger(), nullable=False),
        sa.Column("ki_period_id", sa.Integer(), nullable=True),
        sa.Column("notices", JSON_DOCUMENT, nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("requested_by_user_id", sa.Integer(), nullable=True),
        sa.Column("via_agent_key_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error_code", sa.String(length=48), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["ki_period_id"], ["ki_period.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["requested_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["via_agent_key_id"], ["agent_key.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_ki_run_company_id", "ki_run", ["company_id"])
    op.create_index(
        "uq_ki_run_active",
        "ki_run",
        ["company_id", "kind"],
        unique=True,
        postgresql_where=sa.text(ACTIVE_RUN_WHERE),
        sqlite_where=sa.text(ACTIVE_RUN_WHERE),
    )

    op.create_table(
        "ki_tag",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("facet", sa.String(length=16), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=12), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_by_run_id", sa.Integer(), nullable=True),
        sa.Column("merged_into_id", sa.Integer(), nullable=True),
        sa.Column("retired_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by_run_id"], ["ki_run.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["merged_into_id"], ["ki_tag.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "facet", "slug"),
    )
    op.create_index("ix_ki_tag_company_id", "ki_tag", ["company_id"])

    op.create_table(
        "ki_voucher_analysis",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=8), nullable=False),
        sa.Column("source", sa.String(length=10), nullable=False),
        sa.Column("summary", sa.String(length=200), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.SmallInteger(), nullable=False),
        sa.Column("seed_version", sa.SmallInteger(), nullable=False),
        sa.Column("requested_level", sa.String(length=10), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("model_revision", sa.String(length=80), nullable=True),
        sa.Column("location", sa.String(length=12), nullable=True),
        sa.Column("analysed_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("failures", sa.SmallInteger(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["ki_run.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("transaction_pk"),
    )
    op.create_index(
        "ix_ki_voucher_analysis_company_status",
        "ki_voucher_analysis",
        ["company_id", "status"],
    )
    op.create_index(
        "ix_ki_voucher_analysis_company_prompt",
        "ki_voucher_analysis",
        ["company_id", "prompt_version"],
    )

    op.create_table(
        "ki_line_tag",
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("line_pk", sa.Integer(), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=False),
        sa.Column("analysis_id", sa.Integer(), nullable=False),
        sa.Column("tag_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["line_pk"], ["gl_transaction_line.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["analysis_id"], ["ki_voucher_analysis.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["tag_id"], ["ki_tag.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("line_pk", "tag_id"),
    )
    op.create_index("ix_ki_line_tag_company_tag", "ki_line_tag", ["company_id", "tag_id"])
    op.create_index("ix_ki_line_tag_transaction_pk", "ki_line_tag", ["transaction_pk"])
    op.create_index("ix_ki_line_tag_analysis_id", "ki_line_tag", ["analysis_id"])

    op.create_table(
        "ki_report",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("period_from", sa.Date(), nullable=False),
        sa.Column("period_to", sa.Date(), nullable=False),
        sa.Column("input_hash", sa.String(length=64), nullable=False),
        sa.Column("prompt_version", sa.SmallInteger(), nullable=False),
        sa.Column("schema_version", sa.SmallInteger(), nullable=False),
        sa.Column("requested_level", sa.String(length=10), nullable=False),
        sa.Column("level", sa.String(length=10), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("model_revision", sa.String(length=80), nullable=True),
        sa.Column("location", sa.String(length=12), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("body", JSON_DOCUMENT, nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["run_id"], ["ki_run.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id"),
    )
    op.create_index(
        "ix_ki_report_company_kind_created",
        "ki_report",
        ["company_id", "kind", "created_at"],
    )

    op.create_table(
        "ki_finding_dismissal",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(length=48), nullable=False),
        sa.Column("subject", sa.String(length=128), nullable=False),
        sa.Column("transaction_pk", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("dismissed_by_user_id", sa.Integer(), nullable=True),
        sa.Column("dismissed_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["company.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["transaction_pk"], ["gl_transaction.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["dismissed_by_user_id"], ["app_user.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("company_id", "code", "subject"),
    )
    op.create_index(
        "ix_ki_finding_dismissal_transaction_pk", "ki_finding_dismissal", ["transaction_pk"]
    )


def downgrade() -> None:
    op.drop_index("ix_ki_finding_dismissal_transaction_pk", table_name="ki_finding_dismissal")
    op.drop_table("ki_finding_dismissal")
    op.drop_index("ix_ki_report_company_kind_created", table_name="ki_report")
    op.drop_table("ki_report")
    op.drop_index("ix_ki_line_tag_analysis_id", table_name="ki_line_tag")
    op.drop_index("ix_ki_line_tag_transaction_pk", table_name="ki_line_tag")
    op.drop_index("ix_ki_line_tag_company_tag", table_name="ki_line_tag")
    op.drop_table("ki_line_tag")
    op.drop_index("ix_ki_voucher_analysis_company_prompt", table_name="ki_voucher_analysis")
    op.drop_index("ix_ki_voucher_analysis_company_status", table_name="ki_voucher_analysis")
    op.drop_table("ki_voucher_analysis")
    op.drop_index("ix_ki_tag_company_id", table_name="ki_tag")
    op.drop_table("ki_tag")
    op.drop_index("uq_ki_run_active", table_name="ki_run")
    op.drop_index("ix_ki_run_company_id", table_name="ki_run")
    op.drop_table("ki_run")
