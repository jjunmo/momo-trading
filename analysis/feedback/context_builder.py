"""과거 성과 데이터 → AI 프롬프트 컨텍스트 변환 — 구조화된 승/패 패턴"""
from loguru import logger
from sqlalchemy.ext.asyncio import AsyncSession

from analysis.feedback.performance_tracker import PerformanceTracker, PerformanceStat
from models.trade_result import TradeResult


class FeedbackContextBuilder:
    """
    과거 매매 성과 데이터를 AI 프롬프트에 삽입할 구조화된 컨텍스트로 변환.

    주요 기능:
    - 전략별 최근 성과 요약 → AI가 전략 특성 이해
    - 종목별 과거 거래 이력 → 동일 종목 반복 실수 방지
    - 성공/실패 패턴 분리 → AI가 좋은 패턴은 반복, 나쁜 패턴은 회피
    - 연속 손실 경고 → 과도한 매매 방지
    - 시장 국면별 성과 → 현재 시장에 적합한 판단 유도
    """

    def __init__(self, session: AsyncSession):
        self.tracker = PerformanceTracker(session)

    async def build_strategy_context(self, strategy_type: str) -> str:
        """전략별 성과 컨텍스트"""
        stat = await self.tracker.get_strategy_stats(strategy_type)
        if stat.total_trades == 0:
            return f"[{strategy_type}] 아직 매매 이력 없음 (신규 전략)"

        return (
            f"[{strategy_type} 전략 성과] "
            f"최근 {stat.total_trades}거래, 승률 {stat.win_rate * 100:.1f}%, "
            f"평균 수익률 {stat.avg_return:+.2f}%, "
            f"최고 {stat.best_return:+.2f}% / 최저 {stat.worst_return:+.2f}%, "
            f"총 손익 {stat.total_pnl:+,.0f}원, "
            f"평균 보유 {stat.avg_hold_days:.1f}일"
        )

    async def build_symbol_context(self, symbol: str) -> str:
        """종목별 과거 거래 이력 컨텍스트"""
        stat = await self.tracker.get_symbol_stats(symbol)
        if stat.total_trades == 0:
            return f"[{symbol}] 과거 매매 이력 없음 (처음 분석하는 종목)"

        return (
            f"[{symbol} 과거 이력] "
            f"{stat.total_trades}거래, 승률 {stat.win_rate * 100:.1f}%, "
            f"평균 수익률 {stat.avg_return:+.2f}%, "
            f"총 손익 {stat.total_pnl:+,.0f}원"
        )

    async def build_loss_context(self, limit: int = 5) -> str:
        """최근 실패 사례 컨텍스트 (AI 실수 반복 방지)"""
        losses = await self.tracker.get_recent_losses(limit=limit)
        if not losses:
            return "[최근 손실 거래] 없음"

        lines = ["[최근 손실 거래 — 동일 패턴 주의]"]
        for t in losses:
            pattern_info = f", 패턴: {t.entry_pattern}" if t.entry_pattern else ""
            rsi_info = f", RSI={t.entry_rsi:.0f}" if t.entry_rsi else ""
            lines.append(
                f"  - {t.stock_name}({t.stock_symbol}): {t.return_pct:+.2f}% 손실, "
                f"보유 {t.hold_days}일, 사유: {t.exit_reason}"
                f"{rsi_info}{pattern_info}"
            )
        return "\n".join(lines)

    async def build_win_context(self, limit: int = 5) -> str:
        """최근 성공 사례 컨텍스트 (AI 성공 패턴 강화)"""
        wins = await self.tracker.get_recent_wins(limit=limit)
        if not wins:
            return "[최근 성공 거래] 없음"

        lines = ["[최근 성공 거래 — 이런 패턴을 반복하세요]"]
        for t in wins:
            pattern_info = f", 패턴: {t.entry_pattern}" if t.entry_pattern else ""
            rsi_info = f", RSI={t.entry_rsi:.0f}" if t.entry_rsi else ""
            lines.append(
                f"  - {t.stock_name}({t.stock_symbol}): {t.return_pct:+.2f}% 수익, "
                f"보유 {t.hold_days}일, 전략: {t.strategy_type}"
                f"{rsi_info}{pattern_info}"
            )
        return "\n".join(lines)

    async def build_consecutive_loss_warning(self) -> str:
        """연속 손실 경고 (과매매 방지)"""
        count = await self.tracker.get_consecutive_losses()
        if count == 0:
            return ""
        if count >= 5:
            return (
                f"[! 연속 {count}회 손실 주의] "
                f"최근 매매에서 손실이 이어지고 있습니다. "
                f"진입 근거를 더 철저히 확인하고, 포지션 크기를 줄이는 것을 고려하세요. "
                f"손절가를 타이트하게 설정하세요."
            )
        if count >= 3:
            return (
                f"[연속 {count}회 손실 참고] "
                f"최근 손실이 이어지고 있습니다. 진입 시 리스크:보상 비율을 더 꼼꼼히 확인하세요."
            )
        return ""

    async def build_regime_context(self, current_regime: str) -> str:
        """현재 시장 국면 기반 성과 컨텍스트"""
        stat = await self.tracker.get_market_regime_stats(current_regime)
        if stat.total_trades == 0:
            return f"[{current_regime} 시장에서의 이력] 데이터 없음"

        return (
            f"[{current_regime} 시장 매매 성과] "
            f"{stat.total_trades}거래, 승률 {stat.win_rate * 100:.1f}%, "
            f"평균 수익률 {stat.avg_return:+.2f}%"
        )

    async def build_rsi_context(self, current_rsi: float | None) -> str:
        """현재 RSI 구간 기반 과거 성과"""
        if current_rsi is None:
            return ""

        # RSI를 10 단위 구간으로
        rsi_low = (int(current_rsi) // 10) * 10
        rsi_high = rsi_low + 10
        stat = await self.tracker.get_rsi_range_stats(float(rsi_low), float(rsi_high))
        if stat.total_trades == 0:
            return ""

        return (
            f"[RSI {rsi_low}~{rsi_high} 구간 매수 성과] "
            f"{stat.total_trades}거래, 승률 {stat.win_rate * 100:.1f}%, "
            f"평균 수익률 {stat.avg_return:+.2f}%"
        )

    async def build_full_context(
        self,
        strategy_type: str,
        symbol: str,
        current_regime: str = "",
        current_rsi: float | None = None,
    ) -> str:
        """모든 컨텍스트를 통합하여 프롬프트에 삽입할 텍스트 생성"""
        parts = []

        # 연속 손실 경고 (최상단 배치 — 가장 중요)
        consecutive_warning = await self.build_consecutive_loss_warning()
        if consecutive_warning:
            parts.append(consecutive_warning)

        # 전략 성과
        strategy_ctx = await self.build_strategy_context(strategy_type)
        parts.append(strategy_ctx)

        # 종목 이력
        symbol_ctx = await self.build_symbol_context(symbol)
        parts.append(symbol_ctx)

        # 최근 성공 패턴 (AI가 좋은 패턴 반복하도록)
        win_ctx = await self.build_win_context()
        parts.append(win_ctx)

        # 최근 손실 패턴 (AI가 나쁜 패턴 회피하도록)
        loss_ctx = await self.build_loss_context()
        parts.append(loss_ctx)

        # 시장 국면
        if current_regime:
            regime_ctx = await self.build_regime_context(current_regime)
            parts.append(regime_ctx)

        # RSI 구간
        rsi_ctx = await self.build_rsi_context(current_rsi)
        if rsi_ctx:
            parts.append(rsi_ctx)

        return "\n\n".join(parts)
