"""Alembic migration environment configuration."""

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.config import settings

# Get the config from alembic
config = context.config

# Interpret the config file for Python logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Use the raw PostgreSQL URL (not the SQLAlchemy URL) for Alembic
if context.is_offline_mode():
    sqlalchemy_url = settings.get_raw_database_url()
    context.configure(
        url=sqlalchemy_url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()
else:
    configuration = config.get_section(config.config_ini_section)
    configuration["sqlalchemy.url"] = settings.get_raw_database_url()

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=None,
        )

        with context.begin_transaction():
            context.run_migrations()
