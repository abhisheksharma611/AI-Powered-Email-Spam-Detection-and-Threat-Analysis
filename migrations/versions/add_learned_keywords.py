"""Add learned_keywords table

This migration creates the learned_keywords table for storing
keywords extracted from phishing and malware emails.
These learned keywords are used to boost risk scores for
new incoming emails containing dangerous terms.

Revision ID: add_learned_keywords
Revises: add_sender_reputation
Create Date: 2026-02-18
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = 'add_learned_keywords'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    # Create learned_keywords table
    op.create_table(
        'learned_keywords',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('keyword', sa.String(length=100), nullable=False),
        sa.Column('category', sa.String(length=50), nullable=True),
        sa.Column('frequency', sa.Integer(), nullable=True, default=1),
        sa.Column('weight', sa.Integer(), nullable=True, default=5),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('keyword')
    )
    
    # Create indexes
    op.create_index('ix_learned_keywords_keyword', 'learned_keywords', ['keyword'], unique=True)
    op.create_index('ix_learned_keywords_category', 'learned_keywords', ['category'])


def downgrade():
    op.drop_index('ix_learned_keywords_category', table_name='learned_keywords')
    op.drop_index('ix_learned_keywords_keyword', table_name='learned_keywords')
    op.drop_table('learned_keywords')
