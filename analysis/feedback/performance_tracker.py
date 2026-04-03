"""전략별/종목별/패턴별 승률 추적"""
from dataclasses import dataclass, field

from loguru import logger
from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from models.trade_result import TradeResult


@dataclass
class PerformanceStat:
    """성과 통계"""
    total_trades: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float = 0.0
    avg_return: float = 0.0
    avg_pnl: float = 0.0
    total_pnl: float = 0.0
    avg_hold_days: float = 0.0
    best_return: float = 0.0
    worst_return: float = 0.0


class PerformanceTracker:
    """
    매매 결과를 기반으로 다양한 관점의 승률/성과를 추적.
    이 데이터는 AI 프롬프트에 포함되어 동일 패턴 반복 실수 방지에 활용.
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # ── 공통 필터: 청산 완료된 BUY 포지션만 (pnl/is_win이 정확한 레코드) ──
    # ORPHAN_CLEANUP은 실제 매매 판단이 아니므로 성과 지표에서 제외
    _CLOSED_POSITION_FILTER = and_(
        TradeResult.side == "BUY",
        TradeResult.exit_at.isnot(None),
        TradeResult.status == "CONFIRMED",
        TradeResult.exit_reason != "ORPHAN_CLEANUP",
    )

    async def get_strategy_stats(self, strategy_type: str, limit: int = 100) -> PerformanceStat:
        """전략별 성과 통계 (청산 완료 포지션만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.strategy_type == strategy_type,
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        trades = result.scalars().all()
        return self._calc_stat(trades)

    async def get_symbol_stats(self, symbol: str, limit: int = 50) -> PerformanceStat:
        """종목별 성과 통계 (청산 완료 포지션만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.stock_symbol == symbol,
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        trades = result.scalars().all()
        return self._calc_stat(trades)

    async def get_pattern_stats(self, pattern: str, limit: int = 50) -> PerformanceStat:
        """차트 패턴별 성과 통계 (청산 완료 포지션만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.entry_pattern == pattern,
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        trades = result.scalars().all()
        return self._calc_stat(trades)

    async def get_rsi_range_stats(self, rsi_low: float, rsi_high: float) -> PerformanceStat:
        """특정 RSI 구간 진입 시 성과 (청산 완료 포지션만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.entry_rsi >= rsi_low,
                TradeResult.entry_rsi < rsi_high,
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(100)
        )
        result = await self.session.execute(stmt)
        trades = result.scalars().all()
        return self._calc_stat(trades)

    async def get_market_regime_stats(self, regime: str) -> PerformanceStat:
        """시장 국면별 성과 (청산 완료 포지션만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.market_regime == regime,
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(100)
        )
        result = await self.session.execute(stmt)
        trades = result.scalars().all()
        return self._calc_stat(trades)

    async def get_recent_losses(self, limit: int = 10) -> list[TradeResult]:
        """최근 손실 거래 목록 (AI에게 실패 사례로 제공, 청산 완료 BUY만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.is_win == False,  # noqa: E712
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_recent_wins(self, limit: int = 5) -> list[TradeResult]:
        """최근 성공 거래 목록 (AI에게 성공 패턴으로 제공, 청산 완료 BUY만)"""
        stmt = (
            select(TradeResult)
            .where(and_(
                self._CLOSED_POSITION_FILTER,
                TradeResult.is_win == True,  # noqa: E712
            ))
            .order_by(TradeResult.exit_at.desc())
            .limit(limit)
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_consecutive_losses(self) -> int:
        """최근 연속 손실 횟수 (청산 완료된 BUY 포지션 기준)"""
        stmt = (
            select(TradeResult)
            .where(self._CLOSED_POSITION_FILTER)
            .order_by(TradeResult.exit_at.desc())
            .limit(20)
        )
        result = await self.session.execute(stmt)
        trades = list(result.scalars().all())
        count = 0
        for t in trades:
            if not t.is_win:
                count += 1
            else:
                break
        return count

    async def get_overall_stats(self) -> dict:
        """전체 요약 통계 (청산 완료된 BUY, CONFIRMED만)"""
        stmt = (
            select(TradeResult)
            .where(self._CLOSED_POSITION_FILTER)
            .order_by(TradeResult.exit_at.desc())
            .limit(200)
        )
        result = await self.session.execute(stmt)
        trades = list(result.scalars().all())

        overall = self._calc_stat(trades)

        # 전략별 분류
        by_strategy = {}
        for t in trades:
            if t.strategy_type not in by_strategy:
                by_strategy[t.strategy_type] = []
            by_strategy[t.strategy_type].append(t)

        strategy_stats = {k: self._calc_stat(v) for k, v in by_strategy.items()}

        return {
            "overall": overall,
            "by_strategy": strategy_stats,
            "total_records": len(trades),
        }

    @staticmethod
    def _calc_stat(trades: list) -> PerformanceStat:
        """거래 목록에서 통계 계산"""
        if not trades:
            return PerformanceStat()

        stat = PerformanceStat()
        stat.total_trades = len(trades)
        stat.wins = sum(1 for t in trades if t.is_win)
        stat.losses = stat.total_trades - stat.wins
        stat.win_rate = stat.wins / stat.total_trades if stat.total_trades > 0 else 0.0

        returns = [t.return_pct for t in trades]
        pnls = [t.pnl for t in trades]
        hold_days = [t.hold_days for t in trades if t.hold_days > 0]

        stat.avg_return = sum(returns) / len(returns) if returns else 0.0
        stat.avg_pnl = sum(pnls) / len(pnls) if pnls else 0.0
        stat.total_pnl = sum(pnls)
        stat.avg_hold_days = sum(hold_days) / len(hold_days) if hold_days else 0.0
        stat.best_return = max(returns) if returns else 0.0
        stat.worst_return = min(returns) if returns else 0.0

        return stat
