"""PEAK_SELL 판단 재채점 — 채점 기준 변경(익일 종가 → 5거래일 최고 종가)에 따른 one-off

기존 verdict는 "매도 다음날 종가 +0.5%" 기준으로 채점되어 급등주의 하루 눌림을
CORRECT로 오판했다 (가짜 적중률 → sell_agent +2% 게이트 해제 사고).
기존 PEAK_SELL 채점을 전부 삭제하고 새 기준(5거래일 지평)으로 재채점한다.

캔들이 없어 재채점 불가한 건은 미채점으로 남는다 → 표본 n<10이면
sell_agent._peak_sell_allowed()의 proven=False → +2% 게이트 자동 재잠금 (의도된 보수 동작).

사용법:
    python -m scripts.regrade_peak_sells            # dry-run (삭제 대상·재채점 예상만 출력)
    python -m scripts.regrade_peak_sells --apply    # 실제 삭제 + 재채점
"""
import argparse
import asyncio
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select

from analysis.feedback.judgment_verifier import judgment_verifier
from core.database import AsyncSessionLocal
from models import JudgmentVerification, TradeResult

LOOKBACK_DAYS = 60


async def main(apply: bool) -> None:
    async with AsyncSessionLocal() as session:
        existing = (await session.execute(
            select(JudgmentVerification)
            .where(JudgmentVerification.judgment_type == "PEAK_SELL")
        )).scalars().all()
        print(f"기존 PEAK_SELL 채점: {len(existing)}건 "
              f"(CORRECT {sum(1 for j in existing if j.verdict == 'CORRECT')} / "
              f"WRONG {sum(1 for j in existing if j.verdict == 'WRONG')})")

    if not apply:
        print("\n[dry-run] --apply 로 실행하면 위 채점을 삭제하고 5거래일 기준으로 재채점합니다.")
        return

    # 1. 기존 채점 삭제
    async with AsyncSessionLocal() as session:
        async with session.begin():
            result = await session.execute(
                delete(JudgmentVerification)
                .where(JudgmentVerification.judgment_type == "PEAK_SELL")
            )
        print(f"삭제 완료: {result.rowcount}건")

    # 2. 관여 종목 일봉 보강 (KIS)
    from datetime import timedelta
    from util.time_util import now_kst
    cutoff = now_kst() - timedelta(days=LOOKBACK_DAYS)
    async with AsyncSessionLocal() as session:
        symbols = (await session.execute(
            select(TradeResult.stock_symbol).distinct()
            .where(TradeResult.exit_at >= cutoff)
        )).scalars().all()
    stored = await judgment_verifier.store_daily_candles(list(symbols), days=LOOKBACK_DAYS)
    print(f"일봉 보강: {stored}건 저장 ({len(symbols)}종목)")

    # 3. 새 기준으로 재채점
    graded = await judgment_verifier._verify_peak_sells(lookback_days=LOOKBACK_DAYS)
    print(f"재채점 완료: {graded}건")

    # 4. 결과 리포트
    stats = await judgment_verifier.get_accuracy_stats()
    s = stats.get("PEAK_SELL")
    if s:
        print(f"\n새 적중률(28일 롤링): {s['rate']:.0f}% ({s['hit']}/{s['n']}건)")
        gate = "해제 유지" if (s["n"] >= 10 and s["rate"] >= 50.0) else "재잠금 (+2% 수익 게이트 활성)"
        print(f"→ sell_agent 게이트: {gate}")
    else:
        print("\n채점 표본 없음 (28일 내 verified 0건) → 게이트 재잠금 (+2% 수익 게이트 활성)")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="실제 삭제 + 재채점")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
