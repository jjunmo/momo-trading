"""4/24 일일 리포트 정정 — 사용자 한투 앱 ground truth 기반

KIS REST API inquire-daily-ccld가 NXT 시간대 거래를 누락하여 자동 백필이
불가능한 4건을 사용자 직접 확인 데이터로 보정 + 4/24 다른 청산 종목들의
gross_pnl을 net_pnl로 갱신.

사용법:
    python -m scripts.backfill_424_manual            # dry-run
    python -m scripts.backfill_424_manual --apply    # 실제 DB 변경
"""
import argparse
import asyncio
import sys
from datetime import datetime, date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, and_

from core.database import AsyncSessionLocal
from models.trade_result import TradeResult
from trading.enums import OrderConfirmStatus
from util.pnl_calculator import compute_pnl
from util.time_util import KST


# 사용자 한투 앱 직접 확인 데이터 (NXT 시간대 거래로 KIS API에서 누락됨)
MANUAL_TRADES = [
    {
        "symbol": "009900", "name": "명신산업", "qty": 13,
        "entry_price": 12000, "entry_at": "2026-04-23 17:32:49",
        "exit_price": 11960, "exit_at": "2026-04-24 08:03:17",
        "market": "KOSPI",
    },
    {
        "symbol": "060370", "name": "LS마린솔루션", "qty": 4,
        "entry_price": 37900, "entry_at": "2026-04-23 16:10:05",
        "exit_price": 37900, "exit_at": "2026-04-24 14:37:42",
        "market": "KOSPI",
    },
    {
        "symbol": "028050", "name": "삼성E&A", "qty": 3,
        "entry_price": 52500, "entry_at": "2026-04-24 16:21:33",
        "exit_price": 52300, "exit_at": "2026-04-24 16:27:27",
        "market": "KOSPI",
    },
    {
        "symbol": "034020", "name": "두산에너빌리티", "qty": 1,
        "entry_price": 127000, "entry_at": "2026-04-24 15:46:46",
        "exit_price": 127300, "exit_at": "2026-04-24 19:45:00",
        "market": "KOSPI",
    },
]


def parse_kst(s: str) -> datetime:
    return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)


async def step_a_manual_match(session, dry_run: bool) -> dict:
    """A. 4종목 BUY-SELL 매칭 백필"""
    print("\n[A] 4종목 BUY-SELL 매칭 백필 (사용자 한투 앱 ground truth)")
    actions = []

    for spec in MANUAL_TRADES:
        sym = spec["symbol"]
        entry_at = parse_kst(spec["entry_at"])
        exit_at = parse_kst(spec["exit_at"])

        # BUY 후보: 같은 종목, 같은 분(±60초), CONFIRM_FAILED 또는 CONFIRMED, 수량 일치
        buy_stmt = select(TradeResult).where(and_(
            TradeResult.stock_symbol == sym,
            TradeResult.side == "BUY",
            TradeResult.quantity == spec["qty"],
        )).order_by(TradeResult.entry_at.asc())
        buys = (await session.execute(buy_stmt)).scalars().all()
        # 시각 가까운 BUY 선택
        buy_match = None
        for b in buys:
            if not b.entry_at:
                continue
            ts = b.entry_at.replace(tzinfo=KST) if b.entry_at.tzinfo is None else b.entry_at
            if abs((ts - entry_at).total_seconds()) <= 120 and b.exit_at is None:
                buy_match = b
                break

        # SELL 레코드 (RECOVERED 또는 PENDING_CONFIRM)
        sell_stmt = select(TradeResult).where(and_(
            TradeResult.stock_symbol == sym,
            TradeResult.side == "SELL",
            TradeResult.quantity == spec["qty"],
        )).order_by(TradeResult.exit_at.desc())
        sells = (await session.execute(sell_stmt)).scalars().all()
        sell_match = None
        for s in sells:
            if not s.exit_at:
                continue
            ts = s.exit_at.replace(tzinfo=KST) if s.exit_at.tzinfo is None else s.exit_at
            if abs((ts - exit_at).total_seconds()) <= 1800:  # 30분 허용 (RECOVERED 시각 차이)
                sell_match = s
                break

        if not buy_match:
            print(f"  ❌ {spec['name']} BUY 매칭 실패 (수량 {spec['qty']}, 시각 {spec['entry_at']})")
            continue
        if not sell_match:
            print(f"  ⚠️ {spec['name']} SELL 매칭 실패 (수량 {spec['qty']}, 시각 {spec['exit_at']})")
            continue

        # PnL 계산
        br = compute_pnl(
            entry_price=spec["entry_price"],
            exit_price=spec["exit_price"],
            qty=spec["qty"],
            market=spec["market"],
        )

        print(
            f"  ✓ {spec['name']:15s} {spec['qty']:>2}주 "
            f"@{spec['entry_price']:>7,.0f}→{spec['exit_price']:>7,.0f} "
            f"net={br.net_pnl:>+8,.0f} (gross {br.gross_pnl:+,.0f}, "
            f"comm {br.commission:.2f}, tax {br.tax:.2f})"
        )
        print(f"     BUY id={buy_match.id} status={buy_match.status} (→ CONFIRMED)")
        print(f"     SELL id={sell_match.id} status={sell_match.status} (→ CONFIRMED)")

        if not dry_run:
            # BUY 갱신: CONFIRMED 마킹 + 청산 정보 채움
            buy_match.status = OrderConfirmStatus.CONFIRMED.value
            buy_match.entry_price = spec["entry_price"]
            buy_match.exit_price = spec["exit_price"]
            buy_match.exit_at = exit_at
            buy_match.exit_reason = buy_match.exit_reason or "BACKFILL_MANUAL"
            buy_match.pnl = br.net_pnl
            buy_match.return_pct = br.return_pct
            buy_match.is_win = br.is_win
            buy_match.commission_amt = br.commission
            buy_match.tax_amt = br.tax
            buy_match.notes = f"BACKFILL_MANUAL: NXT race recovered ({spec['entry_at']})"

            # SELL 갱신: 정확 시각으로 보정 + CONFIRMED
            sell_match.status = OrderConfirmStatus.CONFIRMED.value
            sell_match.exit_price = spec["exit_price"]
            sell_match.exit_at = exit_at
            sell_match.stock_name = spec["name"]
            sell_match.notes = f"BACKFILL_MANUAL: 한투 앱 시각 {spec['exit_at']}"

        actions.append({"buy": buy_match, "sell": sell_match, "br": br})

    return {"matched": len(actions)}


async def step_b_recompute_others(session, dry_run: bool) -> dict:
    """B. 4/24 다른 청산 종목들 net_pnl 갱신"""
    print("\n[B] 4/24 기타 청산 종목 net_pnl 재계산 (gross→net)")
    target_date = date(2026, 4, 24)
    start = datetime.combine(target_date, datetime.min.time(), tzinfo=KST)
    end = datetime.combine(target_date, datetime.max.time(), tzinfo=KST)

    stmt = select(TradeResult).where(and_(
        TradeResult.side == "BUY",
        TradeResult.exit_at.isnot(None),
        TradeResult.exit_at >= start,
        TradeResult.exit_at <= end,
        TradeResult.status == OrderConfirmStatus.CONFIRMED.value,
        TradeResult.commission_amt == 0.0,  # 이미 갱신된 건은 제외
    ))
    rows = (await session.execute(stmt)).scalars().all()

    updated = 0
    for r in rows:
        # A에서 처리한 종목은 commission_amt가 이미 채워졌으므로 자동 제외됨
        br = compute_pnl(
            entry_price=r.entry_price, exit_price=r.exit_price,
            qty=r.quantity, market=r.market or "KOSPI",
        )
        old_pnl = r.pnl
        print(
            f"  • {r.stock_symbol} {r.stock_name or '':10s} {r.quantity:>3}주 "
            f"@{r.entry_price:>7,.0f}→{r.exit_price:>7,.0f}: "
            f"{old_pnl:>+8,.0f} → net {br.net_pnl:>+8,.0f} (Δ {br.net_pnl - old_pnl:+,.0f})"
        )
        if not dry_run:
            r.pnl = br.net_pnl
            r.return_pct = br.return_pct
            r.is_win = br.is_win
            r.commission_amt = br.commission
            r.tax_amt = br.tax
        updated += 1
    return {"updated": updated}


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제 DB에 적용")
    args = parser.parse_args()

    print("=" * 70)
    print(f"4/24 리포트 정정 — {'APPLY' if args.apply else 'DRY-RUN'}")
    print("=" * 70)

    async with AsyncSessionLocal() as session:
        async with session.begin():
            a = await step_a_manual_match(session, dry_run=not args.apply)
            b = await step_b_recompute_others(session, dry_run=not args.apply)

            if not args.apply:
                # dry-run은 트랜잭션 롤백
                await session.rollback()
                print(f"\n[DRY-RUN] A 매칭 {a['matched']}건, B 재계산 {b['updated']}건")
                print("실제 적용은 --apply 옵션으로 재실행")
            else:
                print(f"\n[APPLY] A 매칭 {a['matched']}건, B 재계산 {b['updated']}건 — DB 저장 완료")
                print("\n다음: 4/24 일일 리포트 재생성 (별도 명령)")


if __name__ == "__main__":
    asyncio.run(main())
