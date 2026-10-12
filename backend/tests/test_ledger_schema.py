"""Schema-level checks for the accounting core.

These run against in-memory SQLite databases rather than PostgreSQL. The point
is not to prove PostgreSQL behaviour but to catch the two mistakes a
hand-written migration actually makes: drifting out of sync with the models,
and generating identifiers PostgreSQL will silently truncate.

The comparison is done by building the schema twice — once by running the
migrations, once from ``Base.metadata`` — and reflecting both. Comparing two
reflections of the same dialect keeps the check honest; comparing a reflection
against the metadata directly would drown in dialect rendering differences.
"""

from decimal import Decimal
from pathlib import Path
from typing import TypedDict

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.operations import Operations
from alembic.script import ScriptDirectory

from myntos.db.base import Base

# Both imported for the side effect of registering their tables on
# Base.metadata. The migration/model comparison below covers whatever is
# registered, so a new model module that is not imported here would silently
# drop out of the check.
from myntos.domain.bank import models as bank_models  # noqa: F401
from myntos.domain.bilag import models as bilag_models  # noqa: F401
from myntos.domain.identity import models as identity_models  # noqa: F401
from myntos.domain.ledger import models  # noqa: F401
from myntos.domain.mva import models as mva_models  # noqa: F401

BACKEND_ROOT = Path(__file__).resolve().parents[1]

# PostgreSQL truncates identifiers at 63 bytes. A longer constraint name still
# works, but the database then holds a different name than the model expects,
# which surfaces much later as permanent autogenerate churn.
PG_MAX_IDENTIFIER_LENGTH = 63


class TableShape(TypedDict):
    columns: set[str]
    constraints: set[str]


def _run_migrations(connection: sa.Connection) -> None:
    """Apply every migration in revision order against ``connection``."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)

    # target_metadata is what carries NAMING_CONVENTION into op.create_table,
    # exactly as migrations/env.py passes it. Without it the harness would run
    # the migration under different naming rules than production and happily
    # bless names the real database would never produce.
    context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
    with Operations.context(context):
        # walk_revisions yields head → base; upgrades run the other way.
        for revision in reversed(list(script.walk_revisions())):
            revision.module.upgrade()


def _reflect(connection: sa.Connection) -> dict[str, TableShape]:
    """Column and constraint names per table, as the database actually has them."""
    inspector = sa.inspect(connection)
    shapes: dict[str, TableShape] = {}
    for table in inspector.get_table_names():
        if table == "alembic_version":
            continue
        constraints: set[str] = set()
        primary_key = inspector.get_pk_constraint(table)
        if primary_key.get("name"):
            constraints.add(str(primary_key["name"]))
        for group in (
            inspector.get_foreign_keys(table),
            inspector.get_unique_constraints(table),
            inspector.get_check_constraints(table),
        ):
            constraints.update(str(item["name"]) for item in group if item.get("name"))
        shapes[table] = {
            "columns": {column["name"] for column in inspector.get_columns(table)},
            "constraints": constraints,
        }
    return shapes


def _shape_from_migrations() -> dict[str, TableShape]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _run_migrations(connection)
        return _reflect(connection)


def _shape_from_models() -> dict[str, TableShape]:
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        Base.metadata.create_all(connection)
        return _reflect(connection)


def test_models_produce_valid_ddl() -> None:
    """Every model can actually be turned into tables."""
    shape = _shape_from_models()
    assert "gl_transaction_line" in shape
    assert "account_grouping" in shape


def test_migration_creates_the_same_tables_and_columns_as_the_models() -> None:
    migrated = _shape_from_migrations()
    declared = _shape_from_models()

    assert migrated.keys() == declared.keys(), (
        f"tables only in migration: {migrated.keys() - declared.keys()}; "
        f"only in models: {declared.keys() - migrated.keys()}"
    )
    for table in declared:
        assert migrated[table]["columns"] == declared[table]["columns"], (
            f"column mismatch on {table}"
        )


def test_migration_names_constraints_the_same_way_as_the_models() -> None:
    """Constraint names must match, not just exist.

    The naming convention is applied to ``op.create_table`` as well as to the
    models, and the CHECK convention interpolates the name it is given. Passing
    an already-prefixed name there produces ``ck_company_ck_company_…`` in the
    database while the model expects ``ck_company_…`` — a divergence nothing
    else in the test suite would notice.
    """
    migrated = _shape_from_migrations()
    declared = _shape_from_models()

    for table in declared:
        assert migrated[table]["constraints"] == declared[table]["constraints"], (
            f"constraint name mismatch on {table}: "
            f"only in migration {migrated[table]['constraints'] - declared[table]['constraints']}, "
            f"only in models {declared[table]['constraints'] - migrated[table]['constraints']}"
        )


def test_identifiers_fit_postgresql() -> None:
    """No generated name exceeds PostgreSQL's identifier limit."""
    migrated = _shape_from_migrations()

    names = [name for table, shape in migrated.items() for name in (table, *shape["constraints"])]
    too_long = sorted(name for name in names if len(name) > PG_MAX_IDENTIFIER_LENGTH)
    assert not too_long, f"identifiers PostgreSQL would truncate: {too_long}"


def test_posted_amounts_are_exact_decimals() -> None:
    """Monetary columns are NUMERIC, never floating point."""
    line = Base.metadata.tables["gl_transaction_line"]
    for column_name in ("debit", "credit", "tax_base", "debit_tax_amount", "credit_tax_amount"):
        column_type = line.columns[column_name].type
        assert isinstance(column_type, sa.Numeric)
        assert not isinstance(column_type, sa.Float)
        assert column_type.scale == 2


def test_the_backfill_migration_reaches_a_company_that_already_exists() -> None:
    """The import codes and the driftsmiddel account, given books that predate them.

    Written because the obvious check is vacuous. Running the migration against
    a freshly built database proves nothing: it has no companies, so the loop
    body never executes and every bug inside it survives. The first version of
    this migration passed exactly that check and then failed in production on
    the first row it touched.

    So a company is inserted first, with a financial year, and the assertions
    are about what the loop actually wrote.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        backfill = next(item for item in revisions if item.revision == "d4e1a83b62c7")

        with Operations.context(context):
            for revision in revisions:
                if revision is backfill:
                    # Books that exist before the migration runs, which is the
                    # only situation the migration is for.
                    connection.execute(
                        sa.text(
                            "INSERT INTO organization (id, name, slug, plan, "
                            "created_at, name_is_placeholder) VALUES (1, 'Test AS', "
                            "'test-as', 'gratis', '2026-01-01', 0)"
                        )
                    )
                    connection.execute(
                        sa.text(
                            "INSERT INTO company (id, organization_id, orgnr, name, "
                            "country, currency_code, vat_registered, "
                            "in_foretaksregisteret, ki_enabled) VALUES (1, 1, "
                            "'910000004', 'Test AS', 'NO', 'NOK', 0, 0, 1)"
                        )
                    )
                    connection.execute(
                        sa.text(
                            "INSERT INTO financial_year (id, company_id, year, "
                            "start_date, end_date) VALUES (1, 1, 2026, '2026-01-01', "
                            "'2026-12-31')"
                        )
                    )
                revision.module.upgrade()

        codes = {
            row[0]
            for row in connection.execute(sa.text("SELECT code FROM tax_code WHERE company_id = 1"))
        }
        assert {"81", "82", "83", "86", "87"} <= codes

        account = connection.execute(
            sa.text(
                "SELECT id, is_active, created_date FROM account "
                "WHERE company_id = 1 AND account_number = '1250'"
            )
        ).one()
        # is_active has a Python-side default only, so a migration that forgot
        # it would violate NOT NULL rather than quietly default.
        assert account[1]
        # Stamped from the year the books open, not from today.
        assert str(account[2]).startswith("2026-01-01")

        groupings = connection.execute(
            sa.text("SELECT financial_year FROM account_grouping WHERE account_id = :id"),
            {"id": account[0]},
        ).fetchall()
        assert [row[0] for row in groupings] == [2026]

        rate = connection.execute(
            sa.text(
                "SELECT rate FROM tax_rate WHERE tax_code_id = "
                "(SELECT id FROM tax_code WHERE company_id = 1 AND code = '81')"
            )
        ).scalar()
        assert Decimal(str(rate)) == Decimal("0.250000")

    engine.dispose()


def test_the_settlement_accounts_reach_a_company_that_already_exists() -> None:
    """2220, 2600, 2740, 8060 and 8160 on books that predate the categories.

    The same vacuous-check trap as the migration above: a freshly built database
    has no companies, so the loop body never runs. What makes this one worth
    testing separately is the grouping rows. A P&L account added after a year is
    open needs one per open year or the SAF-T export has an account it cannot
    place, and the two new P&L accounts have real næringsspesifikasjon posts.
    The balance-sheet ones were seeded "NA" here and given their real posts by
    ``b7d40e2c9a15``, which runs after, so this reads the corrected pairs.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        backfill = next(item for item in revisions if item.revision == "a2c7f31b9d64")

        with Operations.context(context):
            for revision in revisions:
                if revision is backfill:
                    connection.execute(
                        sa.text(
                            "INSERT INTO organization (id, name, slug, "
                            "created_at, name_is_placeholder) VALUES (2, 'Sen AS', "
                            "'sen-as', '2026-01-01', 0)"
                        )
                    )
                    connection.execute(
                        sa.text(
                            "INSERT INTO company (id, organization_id, orgnr, name, "
                            "country, currency_code, vat_registered, "
                            "in_foretaksregisteret, plan, ki_enabled) VALUES (2, 2, "
                            "'910000004', 'Sen AS', 'NO', 'NOK', 1, 0, 'gratis', 1)"
                        )
                    )
                    connection.execute(
                        sa.text(
                            "INSERT INTO financial_year (id, company_id, year, "
                            "start_date, end_date) VALUES (2, 2, 2026, '2026-01-01', "
                            "'2026-12-31')"
                        )
                    )
                revision.module.upgrade()

        rows = connection.execute(
            sa.text(
                "SELECT account_number, account_type, is_active, created_date FROM account "
                "WHERE company_id = 2 AND account_number IN "
                "('2220', '2600', '2740', '8060', '8160') ORDER BY account_number"
            )
        ).fetchall()
        assert [row[0] for row in rows] == ["2220", "2600", "2740", "8060", "8160"]
        assert [row[1] for row in rows] == ["gjeld", "gjeld", "gjeld", "inntekt", "kostnad"]
        # is_active has a Python-side default only, so a migration that forgot
        # it would violate NOT NULL rather than quietly default.
        assert all(row[2] for row in rows)
        # Stamped from the year the books open, not from today.
        assert all(str(row[3]).startswith("2026-01-01") for row in rows)

        grouping = {
            str(number): str(pair)
            for number, pair in connection.execute(
                sa.text(
                    "SELECT a.account_number, g.grouping_category || '/' || g.grouping_code "
                    "FROM account_grouping g "
                    "JOIN account a ON a.id = g.account_id "
                    "WHERE a.company_id = 2 AND g.financial_year = 2026 "
                    "AND a.account_number IN ('2740', '8060', '8160')"
                )
            ).fetchall()
        }
        assert grouping == {
            "2740": "kortsiktigGjeld/2740",
            "8060": "finansinntekt/8060",
            "8160": "finanskostnad/8160",
        }

    engine.dispose()


def test_the_correction_link_is_recovered_for_edits_that_predate_it() -> None:
    """Three vouchers posted before the link existed, joined back together.

    The inference is not "same day, same amount". It is that ``edit_entry`` is
    the only path that posts a non-reversal inside the same database
    transaction as a reversal, so the two carry the same ``system_entry_date``
    and sit next to each other in the journal.

    Both halves are checked here: the pair that satisfies both conditions gets
    linked, and an ordinary payment posted later — same journal, same amount,
    immediately after — does not. Getting that second one wrong would claim a
    correction that never happened.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        backfill = next(item for item in revisions if item.revision == "a91b6d02e7c5")

        with Operations.context(context):
            for revision in revisions:
                if revision is backfill:
                    _seed_vouchers_from_before_the_link(connection)
                revision.module.upgrade()

        links: dict[int, int | None] = {
            row[0]: row[1]
            for row in connection.execute(
                sa.text("SELECT id, corrects_transaction_pk FROM gl_transaction ORDER BY id")
            )
        }

    engine.dispose()

    # 3 is the replacement posted in the same transaction as reversal 2, which
    # undid entry 1. 6 follows reversal 5 just as closely and is left alone,
    # because it was a separate request.
    assert links == {1: None, 2: None, 3: 1, 4: None, 5: None, 6: None}


def _seed_vouchers_from_before_the_link(connection: sa.Connection) -> None:
    """One edit and one ordinary payment, as they looked before the column existed."""
    connection.execute(
        sa.text(
            "INSERT INTO organization (id, name, slug, plan, created_at, "
            "name_is_placeholder) VALUES (1, 'Test AS', 'test-as', 'gratis', "
            "'2026-01-01', 0)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO company (id, organization_id, orgnr, name, country, "
            "currency_code, vat_registered, in_foretaksregisteret, ki_enabled) "
            "VALUES (1, 1, '910000004', 'Test AS', 'NO', 'NOK', 0, 0, 1)"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO financial_year (id, company_id, year, start_date, end_date) "
            "VALUES (1, 1, 2026, '2026-01-01', '2026-12-31')"
        )
    )
    connection.execute(
        sa.text(
            "INSERT INTO journal (id, company_id, journal_id, description, "
            "journal_type) VALUES (1, 1, 'KASSE', 'Kasse', 'cash')"
        )
    )

    # 1: the original. 2: its reversal, posted in the same transaction as 3.
    # 3: the replacement, which is what should be linked back to 1.
    #
    # 4 and 5 are the negative case: an entry reversed outright, with an
    # ordinary payment posted immediately after it. Adjacent in the journal,
    # same amount, next id — everything a date-and-sum heuristic would fall
    # for. Only the timestamp separates them, and it has to be enough.
    vouchers = (
        (1, "2026-0001", "2026-08-23 10:00:00", None),
        (2, "2026-0002", "2026-08-23 12:30:00", 1),
        (3, "2026-0003", "2026-08-23 12:30:00", None),
        (4, "2026-0004", "2026-08-24 08:00:00", None),
        (5, "2026-0005", "2026-08-24 09:00:00", 4),
        (6, "2026-0006", "2026-08-24 09:04:11", None),
    )
    for pk, voucher_no, stamped, reverses in vouchers:
        connection.execute(
            sa.text(
                "INSERT INTO gl_transaction (id, journal_id, transaction_id, "
                "financial_year, period, transaction_date, gl_posting_date, "
                "system_entry_date, description, reverses_transaction_pk) "
                "VALUES (:pk, 1, :voucher_no, 2026, 8, '2026-08-23', '2026-08-23', "
                ":stamped, 'Test', :reverses)"
            ),
            {"pk": pk, "voucher_no": voucher_no, "stamped": stamped, "reverses": reverses},
        )


def test_an_account_wearing_a_company_name_is_given_it_back() -> None:
    """The account that had been renamed after its first company.

    Vacuous against a fresh database, like the backfill above: no organizations,
    so the loop body never runs. Three accounts are therefore inserted just
    before the first of the two revisions that deal with this, one for each
    answer they have to get between them.

    - Account 1 is the case that was reported as a leak, in the shape it
      actually had: the account is called "Lindeman Technologies" and the
      company is "LINDEMAN TECHNOLOGIES AS". ``c4d7e81f2a90`` compared the two
      strings exactly and therefore missed it, which is how it survived a
      migration written for it. ``d1a7f2c6b483`` ignores case and the trailing
      organisasjonsform, and catches it.
    - Account 2 holds one company and shares its name exactly. Given back too,
      by the second revision: no screen shows an account name any more, so
      matching is hygiene rather than a judgement about what somebody reads,
      and an account that is not named after a company is the simpler
      invariant to state.
    - Account 3 holds two companies under a name of its own. Left alone,
      because renaming a firm out from under a regnskapsfører is its own bug.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        giveback = next(item for item in revisions if item.revision == "c4d7e81f2a90")

        with Operations.context(context):
            for revision in revisions:
                if revision is giveback:
                    _accounts_with_borrowed_names(connection)
                revision.module.upgrade()

        rows = connection.execute(
            sa.text("SELECT id, name, name_is_placeholder FROM organization")
        ).all()
        names = {row[0]: row[1] for row in rows}
        flags = {row[0]: bool(row[2]) for row in rows}

        # Back to what signup would have invented, from the owner's own name,
        # and marked as a question rather than as a settled fact.
        assert names[1] == "Kari Nordmann (privat)"
        assert flags[1]

        assert names[2] == "Ola Nordmann (privat)"
        assert flags[2]

        # A name somebody chose, matching none of its companies.
        assert names[3] == "Fjord Regnskap AS"
        assert not flags[3]


def test_the_access_backfill_leaves_everyone_where_they_were() -> None:
    """Per-company access, given accounts whose members predate it.

    The dangerous half of ``d1a7f2c6b483``: after it runs, a
    ``company_membership`` row is the *only* thing that opens a set of books, so
    a member it fails to copy across is a person locked out of books they were
    keeping an hour ago.

    Account 1 has two companies and two people (an owner and a viewer), which is
    the case that has to produce four rows with the roles unchanged.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        move = next(item for item in revisions if item.revision == "d1a7f2c6b483")

        with Operations.context(context):
            for revision in revisions:
                if revision is move:
                    _accounts_with_borrowed_names(connection)
                    # A second person on account 1, who could read both its
                    # companies the moment before this revision ran.
                    connection.execute(
                        sa.text(
                            "INSERT INTO organization_membership (id, organization_id, "
                            "user_id, role, created_at) VALUES (4, 1, 3, 'viewer', "
                            "'2026-01-01')"
                        )
                    )
                revision.module.upgrade()

        granted = connection.execute(
            sa.text("SELECT company_id, user_id, role FROM company_membership ORDER BY 1, 2")
        ).all()
        assert [tuple(row) for row in granted] == [
            # Account 1's two companies, its owner and its viewer, roles intact.
            (1, 1, "owner"),
            (1, 3, "viewer"),
            (2, 1, "owner"),
            (2, 3, "viewer"),
            # Account 2 and account 3, each with their own owner.
            (3, 2, "owner"),
            (4, 3, "owner"),
            (5, 3, "owner"),
        ]

        # And the subscription came down with them.
        plans = connection.execute(sa.text("SELECT id, plan FROM company ORDER BY id")).all()
        assert {row[1] for row in plans} == {"free"}


def _accounts_with_borrowed_names(connection: sa.Connection) -> None:
    """Three accounts as they stood before the giveback revision ran."""
    accounts = (
        (1, "Lindeman Technologies", "lindeman-technologies"),
        (2, "Ola Sole AS", "ola-sole-as"),
        (3, "Fjord Regnskap AS", "fjord-regnskap-as"),
    )
    for pk, name, slug in accounts:
        connection.execute(
            sa.text(
                "INSERT INTO organization (id, name, slug, plan, created_at, "
                "name_is_placeholder) VALUES (:pk, :name, :slug, 'free', "
                "'2026-01-01', 0)"
            ),
            {"pk": pk, "name": name, "slug": slug},
        )

    users = (
        (1, "Kari Nordmann", "kari@example.no"),
        (2, "Ola Nordmann", "ola@example.no"),
        (3, "Frida Fjord", "frida@example.no"),
    )
    for pk, full_name, email in users:
        connection.execute(
            sa.text(
                "INSERT INTO app_user (id, email, full_name, is_active, "
                "failed_login_attempts, created_at) VALUES (:pk, :email, "
                ":full_name, 1, 0, '2026-01-01')"
            ),
            {"pk": pk, "email": email, "full_name": full_name},
        )
        connection.execute(
            sa.text(
                "INSERT INTO organization_membership (id, organization_id, user_id, "
                "role, created_at) VALUES (:pk, :pk, :pk, 'owner', '2026-01-01')"
            ),
            {"pk": pk},
        )

    companies = (
        # The account borrowed this name before the company took its
        # registered form, which is why an exact comparison missed it.
        (1, 1, "910000004", "LINDEMAN TECHNOLOGIES AS"),
        (2, 1, "910000012", "DIALGA AS"),
        (3, 2, "915933149", "Ola Sole AS"),
        (4, 3, "910000020", "Klient En AS"),
        (5, 3, "910000039", "Klient To AS"),
    )
    for pk, organization_id, orgnr, name in companies:
        connection.execute(
            sa.text(
                "INSERT INTO company (id, organization_id, orgnr, name, country, "
                "currency_code, vat_registered, in_foretaksregisteret, ki_enabled) "
                "VALUES (:pk, :organization_id, :orgnr, :name, 'NO', 'NOK', 0, 0, 1)"
            ),
            {"pk": pk, "organization_id": organization_id, "orgnr": orgnr, "name": name},
        )


def test_the_low_rate_code_becomes_33_and_keeps_its_postings() -> None:
    """Code 32 was seeded as "lav sats (12 %)", and in Skatteetaten's list it is fish.

    The rename is in place, so the row keeps its id and every line posted under
    it keeps pointing at it: the postings were always 12 % sales, and now they
    say so. A draft invoice line naming the code by string goes with it. The
    fish code is then added beside it under the number it really has, which
    only works because the rename runs first.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        rename = next(item for item in revisions if item.revision == "e1cb264f0c44")

        with Operations.context(context):
            for revision in revisions:
                if revision is rename:
                    for statement in (
                        "INSERT INTO organization (id, name, slug, created_at, "
                        "name_is_placeholder) VALUES (3, 'Ferje AS', 'ferje-as', "
                        "'2026-01-01', 0)",
                        "INSERT INTO company (id, organization_id, orgnr, name, country, "
                        "currency_code, vat_registered, in_foretaksregisteret, plan, "
                        "ki_enabled) VALUES (3, 3, '910000004', 'Ferje AS', 'NO', 'NOK', 1, "
                        "0, 'free', 1)",
                        "INSERT INTO financial_year (id, company_id, year, start_date, "
                        "end_date) VALUES (3, 3, 2026, '2026-01-01', '2026-12-31')",
                        "INSERT INTO tax_code (id, company_id, code, description, "
                        "standard_tax_code, is_reportable) VALUES (30, 3, '32', "
                        "'Utgående mva, lav sats (12 %)', '32', 1)",
                        "INSERT INTO tax_rate (tax_code_id, valid_from, rate) "
                        "VALUES (30, '2026-01-01', 0.120000)",
                        "INSERT INTO sales_document (id, company_id, document_type, status, "
                        "buyer_name, buyer_country, buyer_is_business, seller_name, "
                        "seller_orgnr, seller_country, seller_vat_registered, "
                        "seller_in_foretaksregisteret, currency_code, payment_reference_auto, "
                        "reverse_charge, created_at) VALUES (5, 3, 'faktura', 'utkast', "
                        "'Kunde AS', 'NO', 1, 'Ferje AS', '910000004', 'NO', 1, 0, 'NOK', 1, "
                        "0, '2026-03-01')",
                        "INSERT INTO sales_document_line (document_pk, line_no, description, "
                        "quantity, unit_code, unit_price, discount_rate, tax_code, tax_rate, "
                        "income_account_number, net_amount, vat_amount) VALUES (5, 1, "
                        "'Overfart', 1, 'EA', 100.00, 0, '32', 0.120000, '3020', 100.00, 12.00)",
                    ):
                        connection.execute(sa.text(statement))
                revision.module.upgrade()

        codes: dict[str, int] = {
            str(code): int(pk)
            for code, pk in connection.execute(
                sa.text(
                    "SELECT code, id FROM tax_code WHERE company_id = 3 AND code IN ('32', '33')"
                )
            ).fetchall()
        }
        # The same row, renamed: postings that reference id 30 are 12 % sales.
        assert codes["33"] == 30
        assert codes["32"] != 30
        fish_rate = connection.execute(
            sa.text("SELECT rate FROM tax_rate WHERE tax_code_id = :id"), {"id": codes["32"]}
        ).scalar()
        assert Decimal(str(fish_rate)) == Decimal("0.111100")
        line_code = connection.execute(
            sa.text("SELECT tax_code FROM sales_document_line WHERE document_pk = 5")
        ).scalar()
        assert line_code == "33"

        added = {
            code
            for (code,) in connection.execute(
                sa.text("SELECT code FROM tax_code WHERE company_id = 3")
            ).fetchall()
        }
        assert {"12", "14", "15", "51", "84", "85", "88", "89", "91", "92"} <= added

        settings = connection.execute(
            sa.text("SELECT vat_period_type, vat_category FROM company WHERE id = 3")
        ).one()
        assert tuple(settings) == ("toMaaneder", "alminnelig")

    engine.dispose()


def test_seeded_groupings_are_corrected_and_hand_set_ones_left_alone() -> None:
    """What ``b7d40e2c9a15`` infers and what it refuses to guess.

    A row holding exactly the pair the old seed wrote was written by the seed,
    and is corrected in every year. A row holding anything else was put there
    by somebody, and stays as it is even when it is also wrong.
    """
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        config = Config(str(BACKEND_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
        script = ScriptDirectory.from_config(config)
        context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
        revisions = list(reversed(list(script.walk_revisions())))
        correction = next(item for item in revisions if item.revision == "b7d40e2c9a15")

        with Operations.context(context):
            for revision in revisions:
                if revision is correction:
                    for statement in (
                        "INSERT INTO organization (id, name, slug, created_at, "
                        "name_is_placeholder) VALUES (4, 'Gammel AS', 'gammel-as', "
                        "'2025-01-01', 0)",
                        "INSERT INTO company (id, organization_id, orgnr, name, country, "
                        "currency_code, vat_registered, in_foretaksregisteret, plan, "
                        "ki_enabled, vat_period_type, vat_category) VALUES (4, 4, '910000004', "
                        "'Gammel AS', 'NO', 'NOK', 1, 0, 'free', 1, 'toMaaneder', 'alminnelig')",
                        "INSERT INTO account (id, company_id, account_number, description, "
                        "account_type, is_active) VALUES (40, 4, '5000', 'Lønn', 'kostnad', 1), "
                        "(41, 4, '1920', 'Bank', 'eiendel', 1), "
                        "(42, 4, '6800', 'Kontor', 'kostnad', 1)",
                        "INSERT INTO account_grouping (account_id, financial_year, "
                        "grouping_category, grouping_code) VALUES "
                        "(40, 2025, 'lonnskostnad', '5000'), (40, 2026, 'lonnskostnad', '5000'), "
                        "(41, 2026, 'NA', 'NA'), "
                        "(42, 2026, 'annenDriftskostnad', '6500')",
                    ):
                        connection.execute(sa.text(statement))
                revision.module.upgrade()

        rows = connection.execute(
            sa.text(
                "SELECT a.account_number, g.financial_year, "
                "g.grouping_category || '/' || g.grouping_code FROM account_grouping g "
                "JOIN account a ON a.id = g.account_id WHERE a.company_id = 4 "
                "ORDER BY a.account_number, g.financial_year"
            )
        ).fetchall()
        assert [tuple(row) for row in rows] == [
            ("1920", 2026, "balanseverdiForOmloepsmiddel/1920"),
            ("5000", 2025, "loennskostnad/5000"),
            ("5000", 2026, "loennskostnad/5000"),
            # Not the old seed's pair, so somebody chose it: left exactly as is.
            ("6800", 2026, "annenDriftskostnad/6500"),
        ]

    engine.dispose()


def _run_until(connection: sa.Connection, target: str, before: list[str]) -> None:
    """Every revision in order, running ``before`` just ahead of ``target``."""
    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_ROOT / "migrations"))
    script = ScriptDirectory.from_config(config)
    context = MigrationContext.configure(connection, opts={"target_metadata": Base.metadata})
    with Operations.context(context):
        for revision in reversed(list(script.walk_revisions())):
            if revision.revision == target:
                for statement in before:
                    connection.execute(sa.text(statement))
            revision.module.upgrade()


def test_the_bankid_migration_refuses_a_vipps_identity_rather_than_deleting_it() -> None:
    """Vipps Login is gone, and its rows could no longer be read.

    The column is a VARCHAR without a CHECK, so the database would keep one
    forever and the first /auth/me of that account would fail in the ORM.
    Production never configured Vipps; if a row exists anyway it is somebody's
    only way into their books, and the migration stops instead of guessing.
    """
    engine = sa.create_engine("sqlite://")
    with engine.connect() as connection, pytest.raises(RuntimeError, match="vipps"):
        _run_until(
            connection,
            "7a3e5c1d9b24",
            [
                "INSERT INTO user_identity (id, user_id, provider, subject, created_at) "
                "VALUES (1, 1, 'vipps', 'vipps-sub', '2026-09-01 12:00:00')"
            ],
        )
    engine.dispose()


def test_an_attempt_from_before_the_bankid_migration_was_a_sign_in() -> None:
    """Nothing but a sign-in could start a flow before ``intent`` existed."""
    engine = sa.create_engine("sqlite://")
    with engine.begin() as connection:
        _run_until(
            connection,
            "7a3e5c1d9b24",
            [
                "INSERT INTO oauth_login_attempt (id, provider, state, nonce, code_verifier, "
                "created_at, expires_at) VALUES (1, 'google', 's', 'n', 'v', "
                "'2026-10-09 12:00:00', '2026-10-09 12:15:00')"
            ],
        )
        row = connection.execute(
            sa.text("SELECT intent, user_id FROM oauth_login_attempt WHERE id = 1")
        ).one()
        assert tuple(row) == ("login", None)
    engine.dispose()
