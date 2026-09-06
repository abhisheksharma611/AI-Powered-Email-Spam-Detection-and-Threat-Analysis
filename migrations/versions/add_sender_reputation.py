"""Add sender_reputation table

Revision ID: 001
Revises: 
Create Date: 2026-02-18

"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table('sender_reputation',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('sender_email', sa.String(length=255), nullable=False),
        sa.Column('total_emails', sa.Integer(), nullable=True, default=0),
        sa.Column('phishing_count', sa.Integer(), nullable=True, default=0),
        sa.Column('malware_count', sa.Integer(), nullable=True, default=0),
        sa.Column('spam_count', sa.Integer(), nullable=True, default=0),
        sa.Column('high_urgency_count', sa.Integer(), nullable=True, default=0),
        sa.Column('reputation_score', sa.Float(), nullable=True, default=0.0),
        sa.Column('risk_level', sa.String(length=20), nullable=True, default='Low'),
        sa.Column('last_seen', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('sender_email')
    )
    # Create index on sender_email for fast lookups
    op.create_index(op.f('ix_sender_reputation_sender_email'), 'sender_reputation', ['sender_email'], unique=True)


def downgrade() -> None:
    op.drop_index(op.f('ix_sender_reputation_sender_email'), table_name='sender_reputation')
    op.drop_table('sender_reputation')
