"""백테스팅 결과 리포트 생성"""
from dataclasses import asdict

from backtesting.metrics import BacktestMetrics, TradeRecord


class BacktestReport:
    """백테스팅 결과를 구조화된 리포트로 변환"""

    @staticmethod
    def generate(
        symbol: str,
        strategy_type: str,
        metrics: BacktestMetrics,
        trades: list[TradeRecord],
        config: dict | None = None,
    ) -> dict:
        """리포트 생성"""
        return {
            "symbol": symbol,
            "strategy_type": strategy_type,
            "config": config or {},
            "summary": BacktestReport._build_summary(metrics),
            "metrics": BacktestReport._metrics_to_dict(metrics),
            "trade_summary": BacktestReport._trade_summary(trades),
            "trades": [BacktestReport._trade_to_dict(t) for t in trades],
        }

    @staticmethod
    def _build_summary(m: BacktestMetrics) -> str:
        """한 줄 요약"""
        verdict = "수익" if m.total_return > 0 else "손실"
        return (
            f"총 {m.total_trades}거래, 승률 {m.win_rate * 100:.1f}%, "
            f"총수익률 {m.total_return:+.2f}% ({verdict}), "
            f"샤프비율 {m.sharpe_ratio:.2f}, MDD {m.max_drawdown:.2f}%"
        )

    @staticmethod
    def _metrics_to_dict(m: BacktestMetrics) -> dict:
        return {
            "total_trades": m.total_trades,
            "winning_trades": m.winning_trades,
            "losing_trades": m.losing_trades,
            "win_rate": round(m.win_rate * 100, 2),
            "total_return_pct": round(m.total_return, 2),
            "total_pnl": round(m.total_pnl, 0),
            "avg_return_per_trade": round(m.avg_return_per_trade, 2),
            "max_single_win_pct": round(m.max_single_win, 2),
            "max_single_loss_pct": round(m.max_single_loss, 2),
            "sharpe_ratio": m.sharpe_ratio,
            "max_drawdown_pct": m.max_drawdown,
            "max_drawdown_duration_days": m.max_drawdown_duration,
            "profit_factor": round(m.profit_factor, 2) if m.profit_factor != float("inf") else "INF",
            "avg_hold_days": round(m.avg_hold_days, 1),
            "avg_win_return_pct": round(m.avg_win_return, 2),
            "avg_loss_return_pct": round(m.avg_loss_return, 2),
            "initial_capital": round(m.initial_capital, 0),
            "final_capital": round(m.final_capital, 0),
        }

    @staticmethod
    def _trade_summary(trades: list[TradeRecord]) -> dict:
        """거래 요약 통계"""
        if not trades:
            return {}

        by_reason = {}
        for t in trades:
            reason = t.reason
            if reason not in by_reason:
                by_reason[reason] = {"count": 0, "total_pnl": 0}
            by_reason[reason]["count"] += 1
            by_reason[reason]["total_pnl"] += t.pnl

        return {
            "by_exit_reason": {
                k: {"count": v["count"], "total_pnl": round(v["total_pnl"], 0)}
                for k, v in by_reason.items()
            }
        }

    @staticmethod
    def _trade_to_dict(t: TradeRecord) -> dict:
        return {
            "symbol": t.symbol,
            "entry_date": t.entry_date,
            "exit_date": t.exit_date,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "quantity": t.quantity,
            "pnl": t.pnl,
            "return_pct": t.return_pct,
            "hold_days": t.hold_days,
            "reason": t.reason,
        }

    @staticmethod
    def format_text(report: dict) -> str:
        """텍스트 리포트"""
        lines = [
            f"═══ 백테스팅 리포트: {report['symbol']} ({report['strategy_type']}) ═══",
            "",
            f"  {report['summary']}",
            "",
            "── 핵심 지표 ──",
        ]

        m = report["metrics"]
        lines.extend([
            f"  총 거래: {m['total_trades']}회 (승: {m['winning_trades']}, 패: {m['losing_trades']})",
            f"  승률: {m['win_rate']}%",
            f"  총 수익률: {m['total_return_pct']:+.2f}%",
            f"  총 손익: {m['total_pnl']:+,.0f}원",
            f"  샤프비율: {m['sharpe_ratio']}",
            f"  최대 낙폭: {m['max_drawdown_pct']:.2f}% ({m['max_drawdown_duration_days']}일)",
            f"  수익 팩터: {m['profit_factor']}",
            f"  평균 보유 기간: {m['avg_hold_days']}일",
            f"  거래당 평균 수익: {m['avg_return_per_trade']:+.2f}%",
            f"  최대 수익: {m['max_single_win_pct']:+.2f}% / 최대 손실: {m['max_single_loss_pct']:+.2f}%",
            f"  초기 자본: {m['initial_capital']:,.0f}원 → 최종: {m['final_capital']:,.0f}원",
        ])

        # 청산 사유별 통계
        ts = report.get("trade_summary", {}).get("by_exit_reason", {})
        if ts:
            lines.append("")
            lines.append("── 청산 사유 ──")
            for reason, data in ts.items():
                lines.append(f"  {reason}: {data['count']}회, 손익 {data['total_pnl']:+,.0f}원")

        return "\n".join(lines)
