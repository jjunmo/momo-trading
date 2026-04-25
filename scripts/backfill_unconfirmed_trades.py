"""4/22~4/24 누락 거래 백필 — KIS 일별 체결 → DB 보정

체결 확인 race condition으로 CONFIRM_FAILED 마킹된 레코드 중 실제로는
KIS에 체결된 것을 복구한다. 또 SELL 레코드 자체가 누락된 매도(예: 명신산업,
LS마린솔루션)도 KIS 응답을 기준으로 신규 등록한다.

사용법:
    python -m scripts.backfill_unconfirmed_trades            # dry-run
    python -m scripts.backfill_unconfirmed_trades --apply    # 실제 DB 변경
    python -m scripts.backfill_unconfirmed_trades --start=20260422 --end=20260424
"""
import argparse
import asyncio
import sys
from datetime import datetime, date as date_cls
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, and_

from core.database import AsyncSessionLocal
from models.trade_result import TradeResult
from trading.enums import OrderConfirmStatus
from trading.kis_api import get_daily_ccld_direct
from util.pnl_calculator import compute_pnl
from util.time_util import KST


def _parse_kis_trade(t: dict) -> dict:
    """KIS inquire-daily-ccld output1 항목 파싱"""
    odno = str(t.get("odno") or "").strip()
    pdno = str(t.get("pdno") or "").strip()
    name = str(t.get("prdt_name") or pdno).strip()
    side_code = str(t.get("sll_buy_dvsn_cd") or "").strip()
    side = "BUY" if side_code == "02" else ("SELL" if side_code == "01" else "")
    qty = int(float(t.get("tot_ccld_qty") or 0))
    avg = float(t.get("avg_prvs") or 0)
    cncl = (t.get("cncl_yn") or "N").upper() == "Y"
    ord_dt = str(t.get("ord_dt") or "").strip()
    ord_tmd = str(t.get("ord_tmd") or "000000").strip()
    return {
        "odno": odno, "pdno": pdno, "name": name, "side": side,
        "qty": qty, "avg": avg, "cncl": cncl,
        "ord_dt": ord_dt, "ord_tmd": ord_tmd,
    }


def _parse_kis_datetime(ord_dt: str, ord_tmd: str) -> datetime | None:
    """KIS ord_dt(YYYYMMDD) + ord_tmd(HHMMSS) → KST datetime"""
    try:
        dt = datetime.strptime(f"{ord_dt}{ord_tmd}", "%Y%m%d%H%M%S")
        return dt.replace(tzinfo=KST)
    except (ValueError, TypeError):
        return None


async def fetch_kis_trades(start_date: str, end_date: str) -> list[dict]:
    """KIS 일별 체결 전체 조회"""
    print(f"\n[1/3] KIS 일별 체결 조회: {start_date} ~ {end_date}")
    result = await get_daily_ccld_direct(start_date=start_date, end_date=end_date)
    if not result.get("success"):
        print(f"  ❌ 조회 실패: {result.get('error')}")
        return []

    trades = [_parse_kis_trade(t) for t in (result.get("trades") or [])]
    confirmed = [t for t in trades if t["qty"] > 0 and not t["cncl"] and t["side"] in ("BUY", "SELL")]
    print(f"  ✓ 응답 총 {len(trades)}건, 체결 확정 {len(confirmed)}건")
    return confirmed


async def diff_with_db(kis_trades: list[dict], start_date: str, end_date: str) -> dict:
    """DB와 비교 — 누락/불일치 분류"""
    print(f"\n[2/3] DB 차분 분석")
    start_dt = datetime.strptime(start_date, "%Y%m%d").replace(tzinfo=KST)
    end_dt = datetime.strptime(end_date, "%Y%m%d").replace(
        hour=23, minute=59, second=59, tzinfo=KST,
    )

    # DB의 해당 기간 모든 trade_results (PENDING/FAILED 포함)
    async with AsyncSessionLocal() as session:
        stmt = select(TradeResult).where(and_(
            TradeResult.created_at >= start_dt,
            TradeResult.created_at <= end_dt,
        ))
        result = await session.execute(stmt)
        db_trades = list(result.scalars().all())

    # KIS odno → KIS 거래 매핑
    kis_by_odno = {t["odno"]: t for t in kis_trades if t["odno"]}
    db_by_odno = {tr.order_id: tr for tr in db_trades if tr.order_id}

    # 분류
    missing_in_db = []      # KIS에 체결, DB에 odno 없음 → 신규 등록 필요
    failed_but_filled = []  # DB가 CONFIRM_FAILED인데 KIS에 체결 있음 → 복구 필요
    confirmed_match = []    # DB CONFIRMED + KIS 체결 일치 (정상)

    for odno, kt in kis_by_odno.items():
        db_tr = db_by_odno.get(odno)
        if not db_tr:
            missing_in_db.append(kt)
            continue
        if db_tr.status == OrderConfirmStatus.CONFIRM_FAILED.value:
            failed_but_filled.append((db_tr, kt))
        elif db_tr.status == OrderConfirmStatus.PENDING_CONFIRM.value:
            failed_but_filled.append((db_tr, kt))
        elif db_tr.status == OrderConfirmStatus.CONFIRMED.value:
            confirmed_match.append((db_tr, kt))

    # DB 입장: CONFIRM_FAILED 인데 KIS에서도 체결 정보 없음 → 진짜 미체결 (그대로 둠)
    truly_failed = [
        tr for tr in db_trades
        if tr.status == OrderConfirmStatus.CONFIRM_FAILED.value
        and tr.order_id and tr.order_id not in kis_by_odno
    ]

    print(f"  ✓ DB 총 {len(db_trades)}건 (해당 기간)")
    print(f"  • KIS-DB 일치 (정상): {len(confirmed_match)}건")
    print(f"  • DB CONFIRM_FAILED/PENDING → 실제 체결됨 (복구 대상): {len(failed_but_filled)}건")
    print(f"  • KIS에 있으나 DB에 odno 없음 (신규 등록 대상): {len(missing_in_db)}건")
    print(f"  • DB CONFIRM_FAILED + KIS도 미체결 (진짜 실패): {len(truly_failed)}건")

    return {
        "missing_in_db": missing_in_db,
        "failed_but_filled": failed_but_filled,
        "confirmed_match": confirmed_match,
        "truly_failed": truly_failed,
    }


def _print_action_preview(diff: dict) -> None:
    """변경될 작업 미리보기"""
    print(f"\n[3/3] 적용 예정 변경사항")

    if diff["failed_but_filled"]:
        print(f"\n  [복구] CONFIRM_FAILED → CONFIRMED ({len(diff['failed_but_filled'])}건)")
        for db_tr, kt in diff["failed_but_filled"]:
            print(
                f"    {db_tr.stock_symbol} {db_tr.side} odno={db_tr.order_id} "
                f"→ {kt['qty']}주 @{kt['avg']:,.0f}원 (DB 수량 {db_tr.quantity})"
            )

    if diff["missing_in_db"]:
        print(f"\n  [신규 등록] DB 미존재 → 새 TradeResult ({len(diff['missing_in_db'])}건)")
        for kt in diff["missing_in_db"]:
            print(
                f"    {kt['pdno']} {kt['name']} {kt['side']} {kt['qty']}주 "
                f"@{kt['avg']:,.0f}원 odno={kt['odno']} ({kt['ord_dt']} {kt['ord_tmd']})"
            )

    if not diff["failed_but_filled"] and not diff["missing_in_db"]:
        print("  변경 사항 없음 (DB-KIS 완전 일치)")


async def apply_recovery(diff: dict) -> dict:
    """실제 DB에 적용"""
    recovered = 0
    inserted = 0
    closed_buys = 0

    async with AsyncSessionLocal() as session:
        async with session.begin():
            # 1) FAILED/PENDING → CONFIRMED 복구
            for db_tr, kt in diff["failed_but_filled"]:
                merged = await session.merge(db_tr)
                merged.status = OrderConfirmStatus.CONFIRMED.value
                merged.quantity = kt["qty"]
                if kt["side"] == "BUY":
                    merged.entry_price = kt["avg"]
                    merged.entry_at = merged.entry_at or _parse_kis_datetime(kt["ord_dt"], kt["ord_tmd"])
                else:
                    merged.exit_price = kt["avg"]
                    merged.exit_at = _parse_kis_datetime(kt["ord_dt"], kt["ord_tmd"])
                merged.notes = f"BACKFILL_RECOVERED: {kt['ord_dt']}"
                recovered += 1

            # 2) DB 미존재 거래 신규 등록
            for kt in diff["missing_in_db"]:
                event_at = _parse_kis_datetime(kt["ord_dt"], kt["ord_tmd"])
                if kt["side"] == "BUY":
                    tr = TradeResult(
                        order_id=kt["odno"],
                        stock_symbol=kt["pdno"],
                        stock_name=kt["name"],
                        side="BUY",
                        strategy_type="EXTERNAL_RECOVERED",
                        entry_price=kt["avg"],
                        exit_price=0.0,
                        quantity=kt["qty"],
                        status=OrderConfirmStatus.CONFIRMED.value,
                        entry_at=event_at,
                        notes=f"BACKFILL_INSERTED: {kt['ord_dt']}",
                    )
                else:  # SELL
                    tr = TradeResult(
                        order_id=kt["odno"],
                        stock_symbol=kt["pdno"],
                        stock_name=kt["name"],
                        side="SELL",
                        strategy_type="EXTERNAL_RECOVERED",
                        entry_price=0.0,
                        exit_price=kt["avg"],
                        quantity=kt["qty"],
                        status=OrderConfirmStatus.CONFIRMED.value,
                        exit_at=event_at,
                        entry_at=event_at,
                        exit_reason="BACKFILL",
                        notes=f"BACKFILL_INSERTED: {kt['ord_dt']}",
                    )
                session.add(tr)
                inserted += 1

            # 3) 신규/복구된 SELL을 미청산 BUY와 매칭하여 청산
            from sqlalchemy import select
            sells_stmt = select(TradeResult).where(and_(
                TradeResult.side == "SELL",
                TradeResult.status == OrderConfirmStatus.CONFIRMED.value,
                TradeResult.notes.like("BACKFILL%"),
            ))
            sells = (await session.execute(sells_stmt)).scalars().all()
            for sell_tr in sells:
                buy_stmt = select(TradeResult).where(and_(
                    TradeResult.stock_symbol == sell_tr.stock_symbol,
                    TradeResult.side == "BUY",
                    TradeResult.exit_at.is_(None),
                    TradeResult.status == OrderConfirmStatus.CONFIRMED.value,
                ))
                open_buys = (await session.execute(buy_stmt)).scalars().all()
                if not open_buys:
                    continue
                for buy in open_buys:
                    br = compute_pnl(
                        entry_price=buy.entry_price,
                        exit_price=sell_tr.exit_price,
                        qty=buy.quantity,
                        market=buy.market or "KOSPI",
                    )
                    buy.exit_price = sell_tr.exit_price
                    buy.pnl = br.net_pnl
                    buy.return_pct = br.return_pct
                    buy.is_win = br.is_win
                    buy.commission_amt = br.commission
                    buy.tax_amt = br.tax
                    buy.exit_reason = buy.exit_reason or "BACKFILL"
                    buy.exit_at = sell_tr.exit_at
                    closed_buys += 1

    return {"recovered": recovered, "inserted": inserted, "closed_buys": closed_buys}


async def main():
    parser = argparse.ArgumentParser(description="KIS 일별체결 기반 누락 거래 백필")
    parser.add_argument("--start", default="20260422", help="조회 시작일 (YYYYMMDD)")
    parser.add_argument("--end", default="20260424", help="조회 종료일 (YYYYMMDD)")
    parser.add_argument("--apply", action="store_true", help="실제 DB에 적용")
    args = parser.parse_args()

    print("=" * 70)
    print(f"누락 거래 백필 — {'APPLY' if args.apply else 'DRY-RUN'}")
    print("=" * 70)

    kis_trades = await fetch_kis_trades(args.start, args.end)
    if not kis_trades:
        print("\nKIS 응답 없음 — 종료")
        return

    diff = await diff_with_db(kis_trades, args.start, args.end)
    _print_action_preview(diff)

    if not args.apply:
        print("\n[DRY-RUN] 변경 적용 안 함. 검토 후 --apply 옵션으로 재실행.")
        return

    print("\n[APPLY] 실제 DB에 적용 중...")
    stats = await apply_recovery(diff)
    print(f"\n  ✓ 복구된 PENDING/FAILED → CONFIRMED: {stats['recovered']}건")
    print(f"  ✓ 신규 등록: {stats['inserted']}건")
    print(f"  ✓ 매수-매도 매칭하여 청산: {stats['closed_buys']}건")
    print("\n완료. 이제 daily_report_service로 4/24 리포트 재생성하면 정확한 통계 반영됨.")


if __name__ == "__main__":
    asyncio.run(main())
