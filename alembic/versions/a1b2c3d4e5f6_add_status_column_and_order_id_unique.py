"""add status column and order_id unique to trade_results

Revision ID: a1b2c3d4e5f6
Revises: 506d4ef486ca
Create Date: 2026-03-16 12:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a1b2c3d4e5f6'
down_revision: Union[str, None] = '506d4ef486ca'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. 기존 중복 order_id 정리 (최신 1건만 유지, 나머지 NULL)
    conn = op.get_bind()
    # 중복 order_id 찾기
    dupes = conn.execute(sa.text(
        "SELECT order_id FROM trade_results "
        "WHERE order_id IS NOT NULL "
        "GROUP BY order_id HAVING COUNT(*) > 1"
    )).fetchall()
    for (oid,) in dupes:
        # 각 중복 그룹에서 최신 1건만 남기고 NULL 처리
        rows = conn.execute(sa.text(
            "SELECT id FROM trade_results WHERE order_id = :oid "
            "ORDER BY created_at DESC"
        ), {"oid": oid}).fetchall()
        if len(rows) > 1:
            old_ids = [r[0] for r in rows[1:]]
            for old_id in old_ids:
                conn.execute(sa.text(
                    "UPDATE trade_results SET order_id = NULL WHERE id = :id"
                ), {"id": old_id})

    # 2. status 컬럼 추가 + order_id unique 인덱스 변경
    with op.batch_alter_table('trade_results', schema=None) as batch_op:
        batch_op.add_column(
            sa.Column('status', sa.String(length=20), nullable=False, server_default='CONFIRMED')
        )
        batch_op.create_index('ix_trade_results_status', ['status'], unique=False)
        batch_op.drop_index('ix_trade_results_order_id')
        batch_op.create_index('ix_trade_results_order_id', ['order_id'], unique=True)


def downgrade() -> None:
    with op.batch_alter_table('trade_results', schema=None) as batch_op:
        batch_op.drop_index('ix_trade_results_order_id')
        batch_op.create_index('ix_trade_results_order_id', ['order_id'], unique=False)
        batch_op.drop_index('ix_trade_results_status')
        batch_op.drop_column('status')
