"""Application accounts, organizations, sessions; companies become organization-owned.

Two things happen here. The identity tables are created, and ``company`` gains
its owning organization.

That second part rewrites a constraint: ``orgnr`` was globally unique and
becomes unique *per organization*, because two accounting firms may each keep a
set of books for the same client and a global constraint lets whichever added it
first block the other.

``organization_id`` is added ``NOT NULL`` without a default, which only works
against an empty ``company`` table. That is the case here — revision
9f2a1c4b7e30 is pre-release and has never held production data. A deployment
that does have companies must instead add the column nullable, backfill it with
the organization that should own each company, and only then set NOT NULL.

``batch_alter_table`` with an explicit ``copy_from`` is used rather than plain
``op.alter_column``: SQLite cannot drop a constraint or add a foreign key in
place, so Alembic recreates the table there. ``copy_from`` supplies the current
shape instead of letting Alembic reflect it, which is what keeps the recreated
table's constraint names identical to the ones the models declare — a reflected
CHECK name run back through ``NAMING_CONVENTION`` would come out as
``ck_company_ck_company_…``.

Role and provider columns are non-native enums (VARCHAR + Python-side
validation). Extending a PostgreSQL enum needs an ``ALTER TYPE`` in a migration,
and both of those lists are expected to grow.

Revision ID: 3d7c15ba90e4
Revises: 9f2a1c4b7e30
Create Date: 2026-08-12
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

from myntos.db.base import NAMING_CONVENTION

revision: str = "3d7c15ba90e4"
down_revision: str | None = "9f2a1c4b7e30"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

EMAIL = sa.String(length=320)
TOKEN_HASH = sa.String(length=64)

ORGANIZATION_ROLE = sa.Enum(
    "owner",
    "admin",
    "member",
    "viewer",
    name="organization_role",
    native_enum=False,
    length=32,
)
IDENTITY_PROVIDER = sa.Enum(
    "google",
    name="identity_provider",
    native_enum=False,
    length=32,
)

# The shape of ``company`` as revision 9f2a1c4b7e30 left it. Passed to
# batch_alter_table so SQLite's table rebuild does not depend on reflection.
COMPANY_BEFORE = sa.Table(
    "company",
    sa.MetaData(naming_convention=NAMING_CONVENTION),
    sa.Column("id", sa.Integer(), nullable=False),
    sa.Column("orgnr", sa.String(length=9), nullable=False),
    sa.Column("name", sa.String(length=255), nullable=False),
    sa.Column("address_line", sa.String(length=255), nullable=True),
    sa.Column("postal_code", sa.String(length=16), nullable=True),
    sa.Column("city", sa.String(length=128), nullable=True),
    sa.Column("country", sa.String(length=2), nullable=False),
    sa.Column("currency_code", sa.String(length=3), nullable=False),
    sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
    sa.PrimaryKeyConstraint("id", name="pk_company"),
    sa.UniqueConstraint("orgnr", name="uq_company_orgnr"),
)

# The same table after this revision — the downgrade needs it for the same
# reason the upgrade needs COMPANY_BEFORE.
COMPANY_AFTER = sa.Table(
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
    sa.CheckConstraint("length(orgnr) = 9", name="orgnr_is_nine_digits"),
    sa.PrimaryKeyConstraint("id", name="pk_company"),
    sa.UniqueConstraint("organization_id", "orgnr", name="uq_company_organization_id"),
    sa.ForeignKeyConstraint(
        ["organization_id"],
        ["organization.id"],
        name="fk_company_organization_id_organization",
        ondelete="CASCADE",
    ),
)


def upgrade() -> None:
    op.create_table(
        "organization",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("plan", sa.String(length=32), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_organization"),
        sa.UniqueConstraint("slug", name="uq_organization_slug"),
    )

    op.create_table(
        "app_user",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("email", EMAIL, nullable=False),
        sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("full_name", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("email = lower(email)", name="email_is_lowercase"),
        sa.PrimaryKeyConstraint("id", name="pk_app_user"),
        sa.UniqueConstraint("email", name="uq_app_user_email"),
    )

    op.create_table(
        "user_identity",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("provider", IDENTITY_PROVIDER, nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("email_at_provider", EMAIL, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_user_identity_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_identity"),
        sa.UniqueConstraint("provider", "subject", name="uq_user_identity_provider"),
        sa.UniqueConstraint("user_id", "provider", name="uq_user_identity_user_id"),
    )

    op.create_table(
        "organization_membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", ORGANIZATION_ROLE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_membership_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_organization_membership_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_membership"),
        sa.UniqueConstraint(
            "organization_id", "user_id", name="uq_organization_membership_organization_id"
        ),
    )

    op.create_table(
        "organization_invitation",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("organization_id", sa.Integer(), nullable=False),
        sa.Column("email", EMAIL, nullable=False),
        sa.Column("role", ORGANIZATION_ROLE, nullable=False),
        sa.Column("token_hash", TOKEN_HASH, nullable=False),
        sa.Column("invited_by_user_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accepted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("accepted_by_user_id", sa.Integer(), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.ForeignKeyConstraint(
            ["accepted_by_user_id"],
            ["app_user.id"],
            name="fk_organization_invitation_accepted_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["invited_by_user_id"],
            ["app_user.id"],
            name="fk_organization_invitation_invited_by_user_id_app_user",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["organization_id"],
            ["organization.id"],
            name="fk_organization_invitation_organization_id_organization",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_organization_invitation"),
        sa.UniqueConstraint("token_hash", name="uq_organization_invitation_token_hash"),
    )

    op.create_table(
        "user_session",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("token_hash", TOKEN_HASH, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("user_agent", sa.String(length=255), nullable=True),
        sa.Column("ip_address", sa.String(length=45), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_user_session_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_user_session"),
        sa.UniqueConstraint("token_hash", name="uq_user_session_token_hash"),
    )

    op.create_table(
        "oauth_login_attempt",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider", IDENTITY_PROVIDER, nullable=False),
        sa.Column("state", sa.String(length=64), nullable=False),
        sa.Column("nonce", sa.String(length=64), nullable=False),
        sa.Column("code_verifier", sa.String(length=128), nullable=False),
        sa.Column("redirect_to", sa.String(length=512), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("expires_at > created_at", name="expires_after_creation"),
        sa.PrimaryKeyConstraint("id", name="pk_oauth_login_attempt"),
        sa.UniqueConstraint("state", name="uq_oauth_login_attempt_state"),
    )

    with op.batch_alter_table("company", copy_from=COMPANY_BEFORE) as batch:
        batch.add_column(sa.Column("organization_id", sa.Integer(), nullable=False))
        batch.drop_constraint("uq_company_orgnr", type_="unique")
        batch.create_unique_constraint("uq_company_organization_id", ["organization_id", "orgnr"])
        batch.create_foreign_key(
            "fk_company_organization_id_organization",
            "organization",
            ["organization_id"],
            ["id"],
            ondelete="CASCADE",
        )


def downgrade() -> None:
    with op.batch_alter_table("company", copy_from=COMPANY_AFTER) as batch:
        batch.drop_constraint("fk_company_organization_id_organization", type_="foreignkey")
        batch.drop_constraint("uq_company_organization_id", type_="unique")
        batch.create_unique_constraint("uq_company_orgnr", ["orgnr"])
        batch.drop_column("organization_id")

    op.drop_table("oauth_login_attempt")
    op.drop_table("user_session")
    op.drop_table("organization_invitation")
    op.drop_table("organization_membership")
    op.drop_table("user_identity")
    op.drop_table("app_user")
    op.drop_table("organization")
