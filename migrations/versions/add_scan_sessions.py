"""Add the scan_sessions table.

Revision ID: add_scan_sessions
Revises: add_scan_scope
Create Date: 2026-10-02

Why: `current_scan_id` lived only in `session['current_scan_id']`. Closing the
browser or opening a new tab discarded it, and both /results and /last-scan
redirected to the dashboard with "No scan has been run in this session" even
though the emails were present in the database. This table is the durable
pointer; the session stays as the fast path.

Idempotent, for the same reason as add_scan_scope: this database has been
altered out-of-band by `db.create_all()`, so the upgrade must tolerate the
table already existing.

Downgrade note: the table holds no user data beyond an id, so dropping it is
safe. Older SQLite cannot DROP TABLE inside a transaction cleanly, hence
exec_driver_sql with a plain statement.
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_scan_sessions'
down_revision = 'add_scan_scope'
branch_labels = None
depends_on = None


def _table_exists(name):
    insp = sa.inspect(op.get_bind())
    return name in insp.get_table_names()


def upgrade():
    if _table_exists('scan_sessions'):
        return
    op.create_table(
        'scan_sessions',
        sa.Column('user_email', sa.String(255), primary_key=True),
        sa.Column('scan_id', sa.String(32), nullable=False),
        sa.Column('scope_json', sa.Text, nullable=True),
        sa.Column('created_at', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, nullable=True),
    )
    # The index backs the recall-by-user lookup.
    op.execute('CREATE INDEX IF NOT EXISTS ix_scan_sessions_scan_id '
               'ON scan_sessions (scan_id)')


def downgrade():
    op.execute('DROP TABLE IF EXISTS scan_sessions')
