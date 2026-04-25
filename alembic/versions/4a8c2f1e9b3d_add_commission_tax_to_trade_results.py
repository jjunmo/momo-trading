"""add_commission_tax_to_trade_results

Revision ID: 4a8c2f1e9b3d
Revises: 3865b132da81
Create Date: 2026-04-25 17:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '4a8c2f1e9b3d'
down_revision: Union[str, None] = '3865b132da81'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table('trade_results', schema=None) as batch_op:
        batch_op.add_column(sa.Column('commission_amt', sa.Float(), nullable=False, server_default='0.0'))
        batch_op.add_column(sa.Column('tax_amt', sa.Float(), nullable=False, server_default='0.0'))


def downgrade() -> None:
    with op.batch_alter_table('trade_results', schema=None) as batch_op:
        batch_op.drop_column('tax_amt')
        batch_op.drop_column('commission_amt')
