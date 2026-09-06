from logging.config import fileConfig
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from alembic import context
import os
import sys

# Add the parent directory to sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

# Import the Flask app and db
from app import app, db
from models.email_model import Email, SenderReputation

# this is the Alembic Config object
config = context.config

# Interpret the config file for Python logging
# Fix: Point to the correct alembic.ini location in the root directory
import os
config_file = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'alembic.ini')
if os.path.exists(config_file):
    fileConfig(config_file)
else:
    # Fallback: use the config from the config object
    if config.config_file_name is not None:
        fileConfig(config.config_file_name)

# Set the target metadata
target_metadata = db.metadata

def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode."""
    url = app.config.get('SQLALCHEMY_DATABASE_URI')
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()

def run_migrations_online() -> None:
    """Run migrations in 'online' mode."""
    # Get the database URL from the Flask app config
    url = app.config.get('SQLALCHEMY_DATABASE_URI')
    
    # Create engine directly from the URL
    from sqlalchemy import create_engine
    connectable = create_engine(url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
