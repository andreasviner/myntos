"""Access, invitations and the subscription move from the account to the company.

Until this revision, one membership row let a person into every set of books on
an account. That is right for a sole trader with one company and wrong for
everybody else. A regnskapsfører keeps one company per client, so "let my
colleague help with this client" was not expressible: the invitation handed them
every client at once, and the form that sent it sat under the account, where
nothing on screen said so.

Three moves, one idea. What a person may open, who they were invited by, and
what it costs all belong to the *books* rather than to the account.

**``company_membership``** is new and is now the only thing that grants access.
Backfilled from ``organization_membership``: every existing member of an account
gets a row, with the same role, on every company that account holds. That
reproduces exactly what those people could reach the moment before this ran,
which is the only backfill that is not either a demotion or a gift. New
companies from here on grant their creator alone (see ``create_company``).

**``organization_invitation.company_id``** is new and nullable. Nullable because
old rows have no honest value: an invitation written under account-wide rules
names no company, and there is no way to tell which of an account's companies
the sender had in mind. Where the account holds exactly one company there *is*
only one answer, so those are filled in; the rest keep NULL and are refused on
acceptance rather than guessed at.

**``company.plan``** is new, and ``organization.plan`` is dropped. Copied down
from the account so nobody's tier changes today. Dropped rather than left
behind because two copies of a billing tier is one copy too many: the day
somebody charges for this, the column it bills from must be the only one there
is.

And one piece of hygiene that belongs with the same idea: an account whose name
is a company's name is renamed back to the placeholder signup would have made.
``c4d7e81f2a90`` did this for exact matches, which missed the case that prompted
it, because the account had been named before the company was renamed to its
registered form. Matching now ignores case and a trailing organisasjonsform
("Lindeman Technologies" against "LINDEMAN TECHNOLOGIES AS"), which is safe in a
way it was not before: no screen displays an account name any more, so the value
is hygiene rather than something a person reads.

Revision ID: d1a7f2c6b483
Revises: c4d7e81f2a90
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "d1a7f2c6b483"
down_revision: str | None = "c4d7e81f2a90"
branch_labels: None = None
depends_on: None = None

# Spelled out rather than imported from the models, because a migration
# describes the database at one moment in history and has to keep doing the same
# thing after the models move on. Declared exactly as ``3d7c15ba90e4`` declared
# it for organization_membership: same name, same length, and not a native
# enum, or the two tables would disagree about what a role is.
ORGANIZATION_ROLE = sa.Enum(
    "owner",
    "admin",
    "member",
    "viewer",
    name="organization_role",
    native_enum=False,
    length=32,
)

organization = sa.table(
    "organization",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("plan", sa.String),
    sa.column("name_is_placeholder", sa.Boolean),
)

company = sa.table(
    "company",
    sa.column("id", sa.Integer),
    sa.column("organization_id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("plan", sa.String),
)

membership = sa.table(
    "organization_membership",
    sa.column("organization_id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("role", sa.String),
)

company_membership = sa.table(
    "company_membership",
    sa.column("company_id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("role", sa.String),
    sa.column("created_at", sa.DateTime(timezone=True)),
)

invitation = sa.table(
    "organization_invitation",
    sa.column("id", sa.Integer),
    sa.column("organization_id", sa.Integer),
    sa.column("company_id", sa.Integer),
)

app_user = sa.table(
    "app_user",
    sa.column("id", sa.Integer),
    sa.column("full_name", sa.String),
    sa.column("email", sa.String),
)

# Every organisasjonsform Enhetsregisteret uses as a suffix in a name, lowercased.
# A name is compared with its trailing one removed, so an account called after a
# company before that company took its registered form still matches.
LEGAL_SUFFIXES = frozenset(
    {
        "as",
        "asa",
        "ans",
        "da",
        "ba",
        "nuf",
        "enk",
        "sa",
        "kf",
        "if",
        "iks",
        "fli",
        "sti",
        "bbl",
        "bri",
        "ks",
    }
)


def _normalise(name: str) -> str:
    """A name with its case, spacing and legal-form suffix taken off."""
    words = name.casefold().split()
    if words and words[-1].strip(".,") in LEGAL_SUFFIXES:
        words = words[:-1]
    return " ".join(words)


def _placeholder_name(full_name: str | None, email: str) -> str:
    """The name signup would have invented, spelled the way it spells it."""
    stem = (full_name or "").strip() or email.split("@", maxsplit=1)[0]
    return f"{stem} (privat)"


def upgrade() -> None:
    op.create_table(
        "company_membership",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("company_id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("role", ORGANIZATION_ROLE, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["company.id"],
            name="fk_company_membership_company_id_company",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["app_user.id"],
            name="fk_company_membership_user_id_app_user",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_company_membership"),
        # One row per person per company: a second would be two answers to one
        # question, and nothing would say which of them applies.
        sa.UniqueConstraint("company_id", "user_id", name="uq_company_membership_company_id"),
    )

    op.add_column(
        "organization_invitation",
        sa.Column("company_id", sa.Integer(), nullable=True),
    )
    # Named explicitly for the same reason every other constraint here is: the
    # naming convention has to produce the name the models expect, or
    # autogenerate churns forever.
    with op.batch_alter_table("organization_invitation") as batch:
        batch.create_foreign_key(
            "fk_organization_invitation_company_id_company",
            "company",
            ["company_id"],
            ["id"],
            ondelete="CASCADE",
        )

    # The plan lands with a server default so the existing rows are filled in,
    # and the default is dropped again: the model declares it Python-side, and a
    # server default left behind is a phantom diff on every autogenerate.
    op.add_column(
        "company",
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
    )
    with op.batch_alter_table("company") as batch:
        batch.alter_column("plan", server_default=None)

    bind = op.get_bind()
    now = sa.func.now()

    # --- Access: everyone who could reach these books, keeps reaching them ----
    rows = bind.execute(
        sa.select(company.c.id, membership.c.user_id, membership.c.role).join_from(
            company, membership, membership.c.organization_id == company.c.organization_id
        )
    ).all()
    for company_id, user_id, role in rows:
        bind.execute(
            company_membership.insert().values(
                company_id=company_id,
                user_id=user_id,
                role=role,
                created_at=now,
            )
        )

    # --- The subscription follows the books ----------------------------------
    bind.execute(
        company.update().values(
            plan=sa.select(organization.c.plan)
            .where(organization.c.id == company.c.organization_id)
            .scalar_subquery()
        )
    )
    with op.batch_alter_table("organization") as batch:
        batch.drop_column("plan")

    # --- Invitations that can still be honoured ------------------------------
    # Only where the account holds exactly one company, which is the only case
    # with one possible answer.
    single = (
        sa.select(company.c.organization_id, sa.func.min(company.c.id).label("company_id"))
        .group_by(company.c.organization_id)
        .having(sa.func.count(company.c.id) == 1)
        .subquery()
    )
    for organization_id, company_id in bind.execute(sa.select(single)).all():
        bind.execute(
            invitation.update()
            .where(invitation.c.organization_id == organization_id)
            .values(company_id=company_id)
        )

    _give_back_borrowed_names(bind)


def _give_back_borrowed_names(bind: sa.Connection) -> None:
    """Rename accounts that are still wearing one of their companies' names."""
    owners: dict[int, tuple[str | None, str]] = {}
    for organization_id, full_name, email in bind.execute(
        sa.select(membership.c.organization_id, app_user.c.full_name, app_user.c.email)
        .select_from(membership.join(app_user, membership.c.user_id == app_user.c.id))
        .where(membership.c.role == "owner")
        .order_by(membership.c.organization_id, membership.c.user_id)
    ):
        owners.setdefault(organization_id, (full_name, email))

    held: dict[int, set[str]] = {}
    for organization_id, company_name in bind.execute(
        sa.select(company.c.organization_id, company.c.name)
    ):
        held.setdefault(organization_id, set()).add(_normalise(company_name))

    for organization_id, current in bind.execute(sa.select(organization.c.id, organization.c.name)):
        if _normalise(current) not in held.get(organization_id, set()):
            continue
        owner = owners.get(organization_id)
        if owner is None:
            continue
        full_name, email = owner
        replacement = _placeholder_name(full_name, email)
        if replacement == current:
            continue
        bind.execute(
            organization.update()
            .where(organization.c.id == organization_id)
            .values(name=replacement, name_is_placeholder=True)
        )


def downgrade() -> None:
    # The account gets its plan column back, at "free" for everybody. Reading a
    # tier back off the companies would be a guess the moment an account holds
    # two of them on different tiers, and a wrong tier is worse than an obvious
    # default: one of them gets noticed.
    op.add_column(
        "organization",
        sa.Column("plan", sa.String(length=32), nullable=False, server_default="free"),
    )
    with op.batch_alter_table("organization") as batch:
        batch.alter_column("plan", server_default=None)

    op.drop_column("company", "plan")
    with op.batch_alter_table("organization_invitation") as batch:
        batch.drop_constraint("fk_organization_invitation_company_id_company", type_="foreignkey")
        batch.drop_column("company_id")
    op.drop_table("company_membership")
    # The names this revision gave back are not recoverable, for the reason
    # c4d7e81f2a90 gives: which company each was copied from is recorded
    # nowhere.
