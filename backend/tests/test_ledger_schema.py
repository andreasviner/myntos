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
