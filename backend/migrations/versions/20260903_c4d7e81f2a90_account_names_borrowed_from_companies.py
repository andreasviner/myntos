"""Give back the company names that accounts had borrowed.

Until this revision, setting up the first set of books under an account whose
own name was still the one signup invented ("Kari Nordmann (privat)") renamed
the account after that company. The code that did it is gone; see the note left
where ``adopt_name_if_placeholder`` was, in
``myntos/domain/identity/service.py``. This is the data it left behind.

**What it looked like from the outside.** An account holding two companies was
called after one of them. Open the settings page while working in the *other*
one and the account card said "Lindeman Technologies" under a header that said
"DIALGA AS": one company's name printed on a page about a different company,
with nothing on screen to say which of the two names meant what. It was
reported as data leaking between two companies, and from the reader's side there
was no way to tell that it was not.

**What this changes, precisely.** Only accounts that hold **two or more**
companies *and* whose name is exactly one of those companies' names. That
equality is the signature of the old adoption: it is the only code path that
ever set an account's name from a company's. The name goes back to the
placeholder signup would have made from the owner's own name, and
``name_is_placeholder`` is set, which is what makes /organisasjon/ show it as a
question waiting for an answer rather than a settled detail. One click renames
it to anything the owner likes.

An account with a single company is left alone. Its name being the same as its
only company's is not misleading, because for a sole trader they are the same
business, and for such an account the ambiguity cannot arise: there is no second
company for the name to be confused with. Going forward no account is ever named
after a company at all, so this is a one-off.

Nothing here is recoverable on the way down, and ``downgrade`` says so by doing
nothing: which of an account's companies its name had been copied from is
recorded nowhere, so putting a specific name back would mean guessing at one.

Revision ID: c4d7e81f2a90
Revises: b2f9c41d70a8
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "c4d7e81f2a90"
down_revision: str | None = "b2f9c41d70a8"
branch_labels: None = None
depends_on: None = None

organization = sa.table(
    "organization",
    sa.column("id", sa.Integer),
    sa.column("name", sa.String),
    sa.column("name_is_placeholder", sa.Boolean),
)

company = sa.table(
    "company",
    sa.column("id", sa.Integer),
    sa.column("organization_id", sa.Integer),
    sa.column("name", sa.String),
)

membership = sa.table(
    "organization_membership",
    sa.column("organization_id", sa.Integer),
    sa.column("user_id", sa.Integer),
    sa.column("role", sa.String),
)

app_user = sa.table(
    "app_user",
    sa.column("id", sa.Integer),
    sa.column("full_name", sa.String),
    sa.column("email", sa.String),
)


def _placeholder_name(full_name: str | None, email: str) -> str:
    """The name signup would have invented, spelled the way it spells it.

    Deliberately a copy of ``default_organization_name`` rather than an import
    of it: a migration describes the database at one moment in history, and must
    go on doing the same thing after that function is edited or deleted.
    """
    stem = (full_name or "").strip() or email.split("@", maxsplit=1)[0]
    return f"{stem} (privat)"


def upgrade() -> None:
    bind = op.get_bind()

    # The owner is who the placeholder is named after. An account can have more
    # than one, so the lowest user id wins: that is the one who registered and
    # created the account, and therefore the one signup itself would have used.
    owners: dict[int, tuple[str | None, str]] = {}
    for organization_id, full_name, email in bind.execute(
        sa.select(membership.c.organization_id, app_user.c.full_name, app_user.c.email)
        .select_from(membership.join(app_user, membership.c.user_id == app_user.c.id))
        .where(membership.c.role == "owner")
        .order_by(membership.c.organization_id, membership.c.user_id)
    ):
        owners.setdefault(organization_id, (full_name, email))

    # Counted, not de-duplicated: two companies may share a name (the unique
    # constraint is on the orgnr), and an account holding two of those is
    # exactly as confusing as one holding two differently named ones.
    held: dict[int, list[str]] = {}
    for organization_id, company_name in bind.execute(
        sa.select(company.c.organization_id, company.c.name)
    ):
        held.setdefault(organization_id, []).append(company_name)

    for organization_id, current in bind.execute(sa.select(organization.c.id, organization.c.name)):
        companies = held.get(organization_id, [])
        if len(companies) < 2 or current not in companies:
            continue
        owner = owners.get(organization_id)
        if owner is None:
            # An organization nobody owns cannot happen through the application
            # (the owner membership is created in the same flush as the
            # organization), and there is nothing to name it after if it did.
            continue
        full_name, email = owner
        bind.execute(
            organization.update()
            .where(organization.c.id == organization_id)
            .values(name=_placeholder_name(full_name, email), name_is_placeholder=True)
        )


def downgrade() -> None:
    # Deliberately nothing.
    #
    # Which of an account's companies its name had been copied from is recorded
    # nowhere, so there is no way to put a specific name back. Guessing at one,
    # the oldest company say, would write a name nobody ever chose and mark it
    # as chosen. An account left with a placeholder name is a question on the
    # settings page; an account handed the wrong company's name back is the bug
    # this revision exists to remove.
    pass
