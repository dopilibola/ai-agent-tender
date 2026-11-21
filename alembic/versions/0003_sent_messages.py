"""sent_messages: Telegram feed mirrored on the website

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-18 15:00:00
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = '0003'
down_revision: str | None = '0002'
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        'sent_messages',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('kind', sa.String(length=16), nullable=False),
        sa.Column('text', sa.Text(), nullable=False),
        sa.Column('buttons', sa.JSON(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint('id', name=op.f('pk_sent_messages')),
    )
    op.create_index(op.f('ix_sent_messages_kind'), 'sent_messages', ['kind'], unique=False)
    op.create_index(op.f('ix_sent_messages_created_at'), 'sent_messages', ['created_at'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_sent_messages_created_at'), table_name='sent_messages')
    op.drop_index(op.f('ix_sent_messages_kind'), table_name='sent_messages')
    op.drop_table('sent_messages')
