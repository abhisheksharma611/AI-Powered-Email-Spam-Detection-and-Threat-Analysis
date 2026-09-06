"""Add risk_breakdown and explanation_summary to Email model

Revision ID: add_risk_breakdown
Revises: add_learned_keywords
Create Date: 2026-02-18 15:00:00.000000
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = 'add_risk_breakdown'
down_revision = 'add_learned_keywords'
branch_labels = None
depends_on = None


def upgrade():
    # Add risk_breakdown as JSON column (nullable)
    op.add_column('emails', 
        sa.Column('risk_breakdown', 
            sa.JSON(), 
            nullable=True
        )
    )
    
    # Add explanation_summary as Text column (nullable)
    op.add_column('emails',
        sa.Column('explanation_summary',
            sa.Text(),
            nullable=True
        )
    )


def downgrade():
    op.drop_column('emails', 'explanation_summary')
    op.drop_column('emails', 'risk_breakdown')
