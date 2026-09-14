"""Add ai_explanation cache columns to Email model

Stores the AI-generated explanation so each email's explanation is
generated exactly once and served from the DB on subsequent opens.

Also cleans up a stray legacy column 'ai_explanation_generated' if
present (created by an out-of-band schema edit, referenced nowhere).

Revision ID: add_ai_explanation_cache
Revises: add_risk_breakdown
Create Date: 2026-09-09 20:30:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_ai_explanation_cache'
down_revision = 'add_risk_breakdown'
branch_labels = None
depends_on = None


def _existing_columns(table):
    insp = sa.inspect(op.get_bind())
    return {c['name'] for c in insp.get_columns(table)}


def upgrade():
    existing = _existing_columns('emails')

    # Cached AI explanation text (null = not generated yet)
    if 'ai_explanation' not in existing:
        op.add_column('emails',
            sa.Column('ai_explanation',
                sa.Text(),
                nullable=True
            )
        )

    # When the cached explanation was generated
    if 'ai_explanation_generated_at' not in existing:
        op.add_column('emails',
            sa.Column('ai_explanation_generated_at',
                sa.DateTime(),
                nullable=True
            )
        )

    # Cleanup: remove stray legacy column if it exists (SQLite >= 3.35)
    existing = _existing_columns('emails')
    if 'ai_explanation_generated' in existing:
        op.execute('ALTER TABLE emails DROP COLUMN ai_explanation_generated')


def downgrade():
    existing = _existing_columns('emails')
    if 'ai_explanation_generated_at' in existing:
        op.drop_column('emails', 'ai_explanation_generated_at')
    if 'ai_explanation' in existing:
        op.drop_column('emails', 'ai_explanation')