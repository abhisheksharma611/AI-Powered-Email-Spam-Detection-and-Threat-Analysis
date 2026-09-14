"""Add risk_breakdown and explanation_summary to Email model

Revision ID: add_risk_breakdown
Revises: sender_counts_notnull
Create Date: 2026-02-18 15:00:00.000000

Note: originally branched off add_learned_keywords; linearized into the
main chain and made idempotent because some databases already received
these columns out-of-band via db.create_all().
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = 'add_risk_breakdown'
down_revision = 'sender_counts_notnull'
branch_labels = None
depends_on = None


def _existing_columns(table):
    insp = sa.inspect(op.get_bind())
    return {c['name'] for c in insp.get_columns(table)}


def upgrade():
    existing = _existing_columns('emails')

    if 'risk_breakdown' not in existing:
        op.add_column('emails',
            sa.Column('risk_breakdown',
                sa.JSON(),
                nullable=True
            )
        )

    if 'explanation_summary' not in existing:
        op.add_column('emails',
            sa.Column('explanation_summary',
                sa.Text(),
                nullable=True
            )
        )


def downgrade():
    existing = _existing_columns('emails')
    if 'explanation_summary' in existing:
        op.drop_column('emails', 'explanation_summary')
    if 'risk_breakdown' in existing:
        op.drop_column('emails', 'risk_breakdown')