"""KIS 원장 대사(2026-08-08)로 확정된 오염 레코드 3건 보정 — one-off

KIS 일별체결(inquire-daily-ccld)과 전수 대조로 확인된 건만 외과적으로 수정:

1. KB금융(105560) 7/10 — KIS에 존재하지 않는 주문번호 3건이 ORPHAN_CLEANUP으로
   "체결된 것처럼" 손익 계상 (-11,200 × 3 = -33,600원 허위 손실)
   → CONFIRM_FAILED 마킹 + 손익 제거
2. 이랜시스(264850) 8/7 — KIS 체결 205주인데 부분체결 시점(112주)에 확정되어
   93주분 손익 누락 → 수량 205로 보정 + pnl 재계산 (entry 6,720 / exit 6,470)
3. HD현대(267250) 7/30 — 매수 레코드 유실(odno 0010961700, 7주 @211,500).
   매도(odno 0012392800, @212,000)는 기록됐으나 FIFO 매칭 실패로 손익 미계상
   → 청산된 BUY 행 신규 삽입

사용법:
    python -m scripts.fix_trade_records_20260808            # dry-run
    python -m scripts.fix_trade_records_20260808 --apply
"""
import argparse
import asyncio
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from core.database import AsyncSessionLocal
from models.trade_result import TradeResult
from util.pnl_calculator import compute_pnl
from util.time_util import KST

# KIS 원장에 없는 유령 주문 (KB금융 7/10)
GHOST_ORDER_IDS = ["0025725600", "0025375600", "0025327800"]

# 이랜시스 부분체결 보정
ELANSYS_ORDER_ID = "0012562400"
ELANSYS_ACTUAL_QTY = 205

# HD현대 유실 매수
HD_BUY = {
    "order_id": "0010961700",
    "symbol": "267250",
    "name": "HD현대",
    "qty": 7,
    "entry_price": 211500.0,
    "exit_price": 212000.0,
    "sell_order_id": "0012392800",
    "entry_at": datetime(2026, 7, 30, 10, 0, tzinfo=KST).replace(tzinfo=None),
    "exit_at": datetime(2026, 7, 30, 11, 30, tzinfo=KST).replace(tzinfo=None),
}


async def main(apply: bool) -> None:
    tag = "[apply]" if apply else "[dry-run]"
    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1. KB금융 유령 3행
            ghosts = (await session.execute(
                select(TradeResult).where(TradeResult.order_id.in_(GHOST_ORDER_IDS))
            )).scalars().all()
            for tr in ghosts:
                print(f"{tag} 유령 제거: {tr.stock_name} {tr.side} {tr.quantity}주 "
                      f"pnl {tr.pnl:+,.0f} → CONFIRM_FAILED (odno={tr.order_id})")
                if apply:
                    tr.status = "CONFIRM_FAILED"
                    tr.pnl = 0.0
                    tr.return_pct = 0.0
                    tr.is_win = False
                    tr.commission_amt = 0.0
                    tr.tax_amt = 0.0
                    tr.notes = "FIX_20260808: KIS 원장에 체결 실체 없음 — 허위 ORPHAN_CLEANUP 손익 제거"
            if len(ghosts) != len(GHOST_ORDER_IDS):
                print(f"{tag} ⚠️ 유령 행 {len(GHOST_ORDER_IDS)}건 중 {len(ghosts)}건만 발견")

            # 2. 이랜시스 수량 보정
            el = (await session.execute(
                select(TradeResult).where(
                    TradeResult.order_id == ELANSYS_ORDER_ID,
                    TradeResult.side == "BUY",
                )
            )).scalars().one_or_none()
            if el:
                br = compute_pnl(entry_price=el.entry_price, exit_price=el.exit_price,
                                 qty=ELANSYS_ACTUAL_QTY, market=el.market or "KOSPI",
                                 stock_name=el.stock_name or "")
                print(f"{tag} 수량 보정: {el.stock_name} {el.quantity} → {ELANSYS_ACTUAL_QTY}주, "
                      f"pnl {el.pnl:+,.0f} → {br.net_pnl:+,.0f}")
                if apply:
                    el.quantity = ELANSYS_ACTUAL_QTY
                    el.pnl = br.net_pnl
                    el.return_pct = br.return_pct
                    el.is_win = br.is_win
                    el.commission_amt = br.commission
                    el.tax_amt = br.tax
                    el.notes = "FIX_20260808: 부분체결(112주) 확정 후 추가 체결분 유실 — KIS 실체결 205주로 보정"
            else:
                print(f"{tag} ⚠️ 이랜시스 BUY(odno={ELANSYS_ORDER_ID}) 미발견")

            # 3. HD현대 유실 매수 삽입 (이미 있으면 스킵)
            exists = (await session.execute(
                select(TradeResult).where(
                    TradeResult.order_id == HD_BUY["order_id"],
                    TradeResult.stock_symbol == HD_BUY["symbol"],  # odno는 일 단위 유일 — 종목 필터 필수
                )
            )).scalars().first()
            if exists:
                print(f"{tag} HD현대 BUY 이미 존재 — 스킵")
            else:
                br = compute_pnl(entry_price=HD_BUY["entry_price"], exit_price=HD_BUY["exit_price"],
                                 qty=HD_BUY["qty"], market="KOSPI", stock_name=HD_BUY["name"])
                print(f"{tag} 유실 매수 삽입: {HD_BUY['name']} {HD_BUY['qty']}주 "
                      f"@{HD_BUY['entry_price']:,.0f} → @{HD_BUY['exit_price']:,.0f}, pnl {br.net_pnl:+,.0f}")
                if apply:
                    session.add(TradeResult(
                        order_id=HD_BUY["order_id"],
                        stock_symbol=HD_BUY["symbol"], stock_name=HD_BUY["name"],
                        side="BUY", strategy_type="STABLE_SHORT",
                        entry_price=HD_BUY["entry_price"], exit_price=HD_BUY["exit_price"],
                        quantity=HD_BUY["qty"],
                        pnl=br.net_pnl, return_pct=br.return_pct, is_win=br.is_win,
                        commission_amt=br.commission, tax_amt=br.tax,
                        hold_days=0, exit_reason="ANALYSIS_SELL",
                        sell_order_id=HD_BUY["sell_order_id"],
                        market="KOSPI", status="CONFIRMED",
                        entry_at=HD_BUY["entry_at"], exit_at=HD_BUY["exit_at"],
                        notes="FIX_20260808: 매수 레코드 유실 — KIS 일별체결(odno) 기준 복원",
                    ))

    if not apply:
        print("\n--apply 로 실행하면 위 보정을 DB에 반영합니다.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
