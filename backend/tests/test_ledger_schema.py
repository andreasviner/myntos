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
from myntos.domain.bilag import models as bilag_models  # noqa: F401
from myntos.domain.identity import models as identity_models  # noqa: F401
from myntos.domain.ledger import models  # noqa: F401

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
