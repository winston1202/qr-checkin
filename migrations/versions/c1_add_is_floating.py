"""Add is_floating column to User model

Revision ID: c1_add_is_floating
Revises: 
Create Date: 2025-10-13 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision = 'c1_add_is_floating'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # SQLite requires batch operations for altering tables
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.add_column(sa.Column('is_floating', sa.Boolean(), nullable=False, server_default=sa.text('0')))


def downgrade():
    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.drop_column('is_floating')
