"""성과 지표 계산 - 승률, 수익률, 샤프비율, 최대낙폭 등"""
from dataclasses import dataclass, field
import math

import numpy as np


@dataclass
class BacktestMetrics:
    """백테스팅 성과 지표"""
    total_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0

    total_return: float = 0.0          # 총 수익률 (%)
    total_pnl: float = 0.0             # 총 손익 (원)
    avg_return_per_trade: float = 0.0  # 거래당 평균 수익률 (%)
    max_single_win: float = 0.0        # 최대 단일 수익 (%)
    max_single_loss: float = 0.0       # 최대 단일 손실 (%)

    sharpe_ratio: float = 0.0          # 샤프비율 (연환산)
    max_drawdown: float = 0.0          # 최대 낙폭 (%)
    max_drawdown_duration: int = 0     # 최대 낙폭 지속 기간 (일)
    profit_factor: float = 0.0         # 수익 팩터 (총이익/총손실)

    avg_hold_days: float = 0.0         # 평균 보유 일수
    avg_win_return: float = 0.0        # 승리 거래 평균 수익률
    avg_loss_return: float = 0.0       # 패배 거래 평균 손실률

    initial_capital: float = 0.0
    final_capital: float = 0.0


@dataclass
class TradeRecord:
    """개별 거래 기록"""
    symbol: str
    side: str  # BUY/SELL
    entry_date: str
    entry_price: float
    exit_date: str = ""
    exit_price: float = 0.0
    quantity: int = 0
    pnl: float = 0.0
    return_pct: float = 0.0
    hold_days: int = 0
    strategy: str = ""
    reason: str = ""


def calculate_metrics(
    trades: list[TradeRecord],
    equity_curve: list[float],
    initial_capital: float,
) -> BacktestMetrics:
    """거래 기록과 자산 곡선으로 성과 지표 계산"""
    metrics = BacktestMetrics()
    metrics.initial_capital = initial_capital
    metrics.final_capital = equity_curve[-1] if equity_curve else initial_capital

    if not trades:
        return metrics

    # 기본 거래 통계
    returns = [t.return_pct for t in trades]
    pnls = [t.pnl for t in trades]
    hold_days = [t.hold_days for t in trades if t.hold_days > 0]

    metrics.total_trades = len(trades)
    metrics.winning_trades = sum(1 for r in returns if r > 0)
    metrics.losing_trades = sum(1 for r in returns if r < 0)
    metrics.win_rate = metrics.winning_trades / metrics.total_trades if metrics.total_trades > 0 else 0

    metrics.total_pnl = sum(pnls)
    metrics.total_return = ((metrics.final_capital - initial_capital) / initial_capital * 100
                            if initial_capital > 0 else 0)
    metrics.avg_return_per_trade = np.mean(returns) if returns else 0
    metrics.max_single_win = max(returns) if returns else 0
    metrics.max_single_loss = min(returns) if returns else 0

    # 승리/패배 평균
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    metrics.avg_win_return = np.mean(wins) if wins else 0
    metrics.avg_loss_return = np.mean(losses) if losses else 0

    # 보유 기간
    metrics.avg_hold_days = np.mean(hold_days) if hold_days else 0

    # 수익 팩터
    total_profit = sum(p for p in pnls if p > 0)
    total_loss = abs(sum(p for p in pnls if p < 0))
    metrics.profit_factor = total_profit / total_loss if total_loss > 0 else float("inf")

    # 샤프비율 (연환산, 무위험수익률 3%)
    if len(returns) > 1:
        daily_returns = np.array(returns)
        excess_returns = daily_returns - (3.0 / 252)  # 연 3% → 일간
        std = np.std(excess_returns)
        if std > 0:
            metrics.sharpe_ratio = round(np.mean(excess_returns) / std * math.sqrt(252), 2)

    # 최대 낙폭 (MDD)
    if equity_curve and len(equity_curve) > 1:
        curve = np.array(equity_curve)
        peak = np.maximum.accumulate(curve)
        drawdown = (curve - peak) / peak * 100
        metrics.max_drawdown = round(float(np.min(drawdown)), 2)

        # MDD 지속 기간
        in_dd = False
        dd_start = 0
        max_duration = 0
        for i, dd in enumerate(drawdown):
            if dd < 0:
                if not in_dd:
                    dd_start = i
                    in_dd = True
            else:
                if in_dd:
                    max_duration = max(max_duration, i - dd_start)
                    in_dd = False
        if in_dd:
            max_duration = max(max_duration, len(drawdown) - dd_start)
        metrics.max_drawdown_duration = max_duration

    return metrics
