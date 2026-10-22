"""Alembic migration environment (async engine)."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from myntos.core.config import get_settings
from myntos.db.base import Base
from myntos.domain.anlegg import models as _anlegg_models  # noqa: F401
from myntos.domain.arsoppgjor import landbruk_models as _landbruk_models  # noqa: F401
from myntos.domain.arsoppgjor import models as _arsoppgjor_models  # noqa: F401

# Imported for the side effect of registering tables on Base.metadata, which
# is what autogenerate diffs against. New model modules must be added here.
from myntos.domain.bank import models as _bank_models  # noqa: F401
from myntos.domain.bilag import models as _bilag_models  # noqa: F401
from myntos.domain.faktura import models as _faktura_models  # noqa: F401
from myntos.domain.feedback import models as _feedback_models  # noqa: F401
from myntos.domain.identity import models as _identity_models  # noqa: F401
from myntos.domain.ledger import models as _ledger_models  # noqa: F401
from myntos.domain.mva import models as _mva_models  # noqa: F401
from myntos.saft import models as _saft_models  # noqa: F401
from myntos.skattemelding import models as _skattemelding_models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", get_settings().database_url)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL to stdout instead of connecting ('alembic upgrade --sql')."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
