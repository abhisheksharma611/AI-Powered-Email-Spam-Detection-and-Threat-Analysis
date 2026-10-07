"""Add label_ids and scan_id to the Email model

Revision ID: add_scan_scope
Revises: add_ai_explanation_cache
Create Date: 2026-10-02

Both columns are nullable and additive, so existing rows keep working and no
data backfill is needed.

Why this migration exists at all: `db.create_all()` at startup creates missing
TABLES but never adds COLUMNS to a table that already exists. The model was
updated with these two fields while the live database already had an `emails`
table, so the model and the schema diverged silently. The symptom was:

    sqlalchemy.exc.OperationalError: no such column: emails.scan_id

on the /results query, while every per-email write was being swallowed by the
try/except in _process_single_email, so a whole scan could appear to run and
store nothing.

Same idempotent treatment as add_risk_breakdown: this database received
`label_ids` out-of-band via create_all(), so the upgrade must tolerate a column
that is already present.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_scan_scope'
down_revision = 'add_ai_explanation_cache'
branch_labels = None
depends_on = None


def _existing_columns(table):
    insp = sa.inspect(op.get_bind())
    return {c['name'] for c in insp.get_columns(table)}


def _add_if_missing(col, ddl_type):
    existing = _existing_columns('emails')
    if col in existing:
        return False
    op.execute('ALTER TABLE emails ADD COLUMN %s %s' % (col, ddl_type))
    return True


def upgrade():
    added_label = _add_if_missing('label_ids', 'VARCHAR(255)')
    added_scan = _add_if_missing('scan_id', 'VARCHAR(32)')
    # The index backs the /results and /last-scan scope filter. Index creation is
    # separate from the column because SQLite cannot add one in the same step.
    existing = _existing_columns('emails')
    if 'scan_id' in existing:
        op.execute('CREATE INDEX IF NOT EXISTS ix_emails_scan_id ON emails (scan_id)')


def downgrade():
    # SQLite before 3.35 cannot DROP COLUMN. The columns are nullable, so
    # leaving them is harmless and dropping the index is enough to undo the
    # performance characteristic this migration added.
    op.execute('DROP INDEX IF EXISTS ix_emails_scan_id')