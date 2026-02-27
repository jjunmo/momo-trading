from fastapi import APIRouter
from pydantic import BaseModel

from core.config import settings
from schemas.common import SuccessResponse
from schemas.dashboard_schema import SystemStatus
from trading.enums import AutonomyMode

router = APIRouter(prefix="/dashboard", tags=["대시보드"])


@router.get("/system/status", response_model=SuccessResponse[SystemStatus])
async def get_system_status():
    status = SystemStatus(
        trading_enabled=settings.TRADING_ENABLED,
        autonomy_mode=settings.AUTONOMY_MODE,
        mcp_connected=False,  # Phase 2에서 실시간 상태 연동
        websocket_connected=False,
        scheduler_running=settings.SCHEDULER_ENABLED,
    )
    return SuccessResponse(data=status)


class AutonomyModeUpdate(BaseModel):
    mode: AutonomyMode


@router.put("/system/autonomy", response_model=SuccessResponse[dict])
async def update_autonomy_mode(data: AutonomyModeUpdate):
    settings.AUTONOMY_MODE = data.mode.value
    return SuccessResponse(
        data={"autonomy_mode": settings.AUTONOMY_MODE},
        message=f"자율 모드가 {data.mode.value}로 변경되었습니다",
    )


class TradingToggle(BaseModel):
    enabled: bool


@router.put("/system/trading-enabled", response_model=SuccessResponse[dict])
async def toggle_trading(data: TradingToggle):
    settings.TRADING_ENABLED = data.enabled
    return SuccessResponse(
        data={"trading_enabled": settings.TRADING_ENABLED},
        message=f"매매가 {'활성화' if data.enabled else '비활성화'}되었습니다",
    )
