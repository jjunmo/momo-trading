"""delete broken trade_results where exit_price=0

Revision ID: 9a7c21d42891
Revises: a1b2c3d4e5f6
Create Date: 2026-03-19

강제 청산 시 expected_price=0 버그로 인해
exit_price=0, pnl=0으로 기록된 깨진 데이터 삭제
"""
from alembic import op
import sqlalchemy as sa

revision = "9a7c21d42891"
down_revision = "a1b2c3d4e5f6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "DELETE FROM trade_results "
        "WHERE exit_at IS NOT NULL AND exit_price = 0"
    )


def downgrade() -> None:
    # 삭제된 데이터는 복구 불가 (원래 깨진 데이터)
    pass
