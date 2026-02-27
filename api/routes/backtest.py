"""백테스팅 API"""
from datetime import date, timedelta

from fastapi import APIRouter, HTTPException

from backtesting.data_loader import BacktestDataLoader
from backtesting.engine import BacktestConfig, BacktestEngine
from backtesting.report import BacktestReport
from schemas.backtest_schema import BacktestRunRequest, BacktestResultResponse
from schemas.common import SuccessResponse

router = APIRouter(prefix="/backtest", tags=["backtest"])


@router.post("/run", response_model=SuccessResponse[BacktestResultResponse])
async def run_backtest(req: BacktestRunRequest):
    """백테스팅 실행"""
    end_dt = req.end_date or date.today()
    start_dt = req.start_date or (end_dt - timedelta(days=180))

    # MCP에서 과거 데이터 로드
    df = await BacktestDataLoader.load_from_mcp(
        symbol=req.symbol,
        start_date=start_dt,
        end_date=end_dt,
    )

    if df.empty:
        raise HTTPException(status_code=400, detail="과거 데이터를 로드할 수 없습니다.")

    config = BacktestConfig(
        strategy_type=req.strategy_type,
        initial_capital=req.initial_capital,
        max_position_pct=req.max_position_pct,
        commission_rate=req.commission_rate,
        slippage_rate=req.slippage_rate,
        stop_loss_pct=req.stop_loss_pct,
        take_profit_pct=req.take_profit_pct,
        max_hold_days=req.max_hold_days,
    )

    engine = BacktestEngine(config)
    result = await engine.run(req.symbol, df)

    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])

    report = BacktestReport.generate(
        symbol=req.symbol,
        strategy_type=req.strategy_type,
        metrics=result["metrics"],
        trades=result["trades"],
        config={
            "initial_capital": config.initial_capital,
            "stop_loss_pct": config.stop_loss_pct,
            "take_profit_pct": config.take_profit_pct,
            "max_hold_days": config.max_hold_days,
            "commission_rate": config.commission_rate,
            "slippage_rate": config.slippage_rate,
        },
    )

    return SuccessResponse(data=report)
