from fastapi import APIRouter

from dependencies.services import StrategyServiceDep, StrategyServiceTxDep
from schemas.common import SuccessResponse
from schemas.strategy_schema import (
    StrategyConfigCreate,
    StrategyConfigResponse,
    StrategyConfigUpdate,
    StrategySignalResponse,
)

router = APIRouter(prefix="/strategies", tags=["전략"])


@router.get("", response_model=SuccessResponse[list[StrategyConfigResponse]])
async def get_strategies(service: StrategyServiceDep):
    configs = await service.get_all_configs()
    return SuccessResponse(data=[StrategyConfigResponse.model_validate(c) for c in configs])


@router.post("", response_model=SuccessResponse[StrategyConfigResponse], status_code=201)
async def create_strategy(data: StrategyConfigCreate, service: StrategyServiceTxDep):
    config = await service.create_config(data)
    return SuccessResponse(data=StrategyConfigResponse.model_validate(config), message="전략이 생성되었습니다")


@router.get("/{strategy_id}", response_model=SuccessResponse[StrategyConfigResponse])
async def get_strategy(strategy_id: str, service: StrategyServiceDep):
    config = await service.get_config_by_id(strategy_id)
    return SuccessResponse(data=StrategyConfigResponse.model_validate(config))


@router.put("/{strategy_id}", response_model=SuccessResponse[StrategyConfigResponse])
async def update_strategy(strategy_id: str, data: StrategyConfigUpdate, service: StrategyServiceTxDep):
    config = await service.update_config(strategy_id, data)
    return SuccessResponse(data=StrategyConfigResponse.model_validate(config))


@router.patch("/{strategy_id}/toggle", response_model=SuccessResponse[StrategyConfigResponse])
async def toggle_strategy(strategy_id: str, service: StrategyServiceTxDep):
    config = await service.toggle_config(strategy_id)
    return SuccessResponse(data=StrategyConfigResponse.model_validate(config))


@router.get("/signals/recent", response_model=SuccessResponse[list[StrategySignalResponse]])
async def get_recent_signals(service: StrategyServiceDep, limit: int = 20):
    signals = await service.get_recent_signals(limit=limit)
    return SuccessResponse(data=[StrategySignalResponse.model_validate(s) for s in signals])
