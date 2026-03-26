from loguru import logger

from exceptions.common import ServiceException
from models.strategy import StrategyConfig, StrategySignal
from repositories.strategy_repository import StrategyConfigRepository, StrategySignalRepository
from schemas.strategy_schema import StrategyConfigCreate, StrategyConfigUpdate


class StrategyService:
    def __init__(
        self,
        config_repo: StrategyConfigRepository,
        signal_repo: StrategySignalRepository,
    ):
        self.config_repo = config_repo
        self.signal_repo = signal_repo

    async def get_all_configs(self) -> list[StrategyConfig]:
        return await self.config_repo.get_all()

    async def get_config_by_id(self, config_id: str) -> StrategyConfig:
        config = await self.config_repo.get_by_id(config_id)
        if not config:
            raise ServiceException.not_found(f"전략을 찾을 수 없습니다: {config_id}")
        return config

    async def get_active_configs(self) -> list[StrategyConfig]:
        return await self.config_repo.get_active_strategies()

    async def create_config(self, data: StrategyConfigCreate) -> StrategyConfig:
        existing = await self.config_repo.get_by_type(data.type.value)
        if existing:
            raise ServiceException.conflict(f"이미 존재하는 전략 유형입니다: {data.type.value}")
        config = StrategyConfig(
            name=data.name,
            type=data.type.value,
            stop_loss_pct=data.stop_loss_pct,
            take_profit_pct=data.take_profit_pct,
            max_hold_days=data.max_hold_days,
            max_position_pct=data.max_position_pct,
            min_confidence=data.min_confidence,
            description=data.description,
        )
        created = await self.config_repo.create(config)
        logger.debug("전략 생성: {} ({})", data.name, data.type.value)
        return created

    async def update_config(self, config_id: str, data: StrategyConfigUpdate) -> StrategyConfig:
        config = await self.get_config_by_id(config_id)
        for field, value in data.model_dump(exclude_unset=True).items():
            setattr(config, field, value)
        return await self.config_repo.update(config)

    async def toggle_config(self, config_id: str) -> StrategyConfig:
        config = await self.get_config_by_id(config_id)
        config.is_active = not config.is_active
        logger.debug("전략 토글: {} → {}", config.name, "활성" if config.is_active else "비활성")
        return await self.config_repo.update(config)

    async def get_recent_signals(self, limit: int = 20) -> list[StrategySignal]:
        return await self.signal_repo.get_recent_signals(limit)

    async def save_signal(self, signal: StrategySignal) -> StrategySignal:
        return await self.signal_repo.create(signal)
