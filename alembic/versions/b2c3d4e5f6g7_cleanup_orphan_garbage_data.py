"""cleanup orphan garbage data (exit_price=0 or entry_price=0)

Revision ID: b2c3d4e5f6g7
Revises: 9a7c21d42891
Create Date: 2026-03-25

ORPHAN_CLEANUP 로직이 exit_at만 설정하고 exit_price/pnl을 계산하지 않아
exit_price=0, pnl=0인 쓰레기 데이터가 지속 생성됨.
또한 BUY 매칭 실패로 entry_price=0인 고아 SELL 레코드도 존재.
코드 수정과 함께 기존 쓰레기 데이터를 삭제하여 통계 정확성 확보.
"""
from alembic import op

revision = "b2c3d4e5f6g7"
down_revision = "9a7c21d42891"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. exit_at이 있지만 exit_price=0인 BUY (ORPHAN_CLEANUP 쓰레기)
    op.execute(
        "DELETE FROM trade_results "
        "WHERE side = 'BUY' AND exit_at IS NOT NULL AND exit_price = 0"
    )
    # 2. entry_price=0인 고아 SELL (BUY 매칭 실패)
    op.execute(
        "DELETE FROM trade_results "
        "WHERE side = 'SELL' AND entry_price = 0"
    )
    # 3. 잘못된 통계로 생성된 일일 리포트 삭제 (재생성 가능하도록)
    op.execute(
        "DELETE FROM daily_reports "
        "WHERE buy_count = 0 AND sell_count = 0 AND total_pnl = 0"
    )


def downgrade() -> None:
    # 삭제된 데이터는 복구 불가 (원래 쓰레기 데이터)
    pass
