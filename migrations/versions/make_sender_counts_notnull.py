"""Make sender_reputation count columns NOT NULL with default 0

Prevents the 'NoneType += int' bug by ensuring the per-sender counter
columns can never hold NULL. Existing NULLs are backfilled to 0 first.

Revision ID: sender_counts_notnull
Revises: fix_updated_at
Create Date: 2026-07-11

"""
from alembic import op
import sqlalchemy as sa


revision = 'sender_counts_notnull'
down_revision = 'fix_updated_at'
branch_labels = None
depends_on = None

COUNT_COLUMNS = (
    'total_emails',
    'phishing_count',
    'malware_count',
    'spam_count',
    'high_urgency_count',
)


def upgrade() -> None:
    # 1) Backfill any existing NULL counters to 0 so the NOT NULL change is safe
    for col in COUNT_COLUMNS:
        op.execute(sa.text(f"UPDATE sender_reputation SET {col} = 0 WHERE {col} IS NULL"))

    # 2) Enforce non-nullable with a server-side default of 0 (SQLite-safe batch alter)
    with op.batch_alter_table('sender_reputation') as batch_op:
        for col in COUNT_COLUMNS:
            batch_op.alter_column(
                col,
                existing_type=sa.Integer(),
                nullable=False,
                server_default='0',
            )


def downgrade() -> None:
    with op.batch_alter_table('sender_reputation') as batch_op:
        for col in COUNT_COLUMNS:
            batch_op.alter_column(
                col,
                existing_type=sa.Integer(),
                nullable=True,
                server_default=None,
            )
