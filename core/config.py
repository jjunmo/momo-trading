from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    APP_NAME: str = "momo-trading"
    APP_VERSION: str = "0.1.0"
    ENVIRONMENT: str = "local"  # local | staging | production

    DATABASE_URL: str = "sqlite:///./app.db"
    LOG_LEVEL: str = "DEBUG"
    CORS_ORIGINS: list[str] = ["http://localhost:3000", "http://localhost:8000"]

    # === KIS MCP 서버 ===
    KIS_MCP_URL: str = "http://localhost:3100/sse"

    # KIS API 인증
    KIS_APP_KEY: str = ""
    KIS_APP_SECRET: str = ""
    KIS_PAPER_APP_KEY: str = ""
    KIS_PAPER_APP_SECRET: str = ""
    KIS_HTS_ID: str = ""
    KIS_ACCT_STOCK: str = ""
    KIS_PAPER_STOCK: str = ""
    KIS_PROD_TYPE: str = "01"
    KIS_ACCOUNT_TYPE: str = "VIRTUAL"

    # KIS WebSocket
    KIS_WS_URL_DOMESTIC: str = "ws://ops.koreainvestment.com:21000"
    KIS_WS_URL_OVERSEAS: str = "ws://ops.koreainvestment.com:31000"

    # === AI / LLM (Claude Code CLI — 구독 크레딧 사용) ===
    CLAUDE_CODE_MODEL: str = "sonnet"  # 기본 모델 (Tier별 미지정 시 사용)
    CLAUDE_CODE_MODEL_TIER1: str = "haiku"  # Tier1 (스캔/분석): 빠른 모델
    CLAUDE_CODE_MODEL_TIER2: str = "sonnet"  # Tier2 (최종 검토): 정확한 모델
    CLAUDE_CODE_PATH: str = ""  # 비어있으면 자동 탐색 (예: /opt/homebrew/bin/claude)

    # === AI Agent ===
    AUTONOMY_MODE: str = "AUTONOMOUS"  # AUTONOMOUS / SEMI_AUTO
    RECOMMENDATION_EXPIRE_MIN: int = 60
    MIN_BUY_QUANTITY: int = 5

    # === Trading Safety ===
    TRADING_ENABLED: bool = True
    DAY_TRADING_ONLY: bool = True  # 당일 청산 필수 (데이트레이딩 모드)
    BUY_CUTOFF_HOUR: int = 14  # 신규 매수 마감 시각 (14시 이후 매수 차단)
    BUY_CUTOFF_MINUTE: int = 30
    FORCE_LIQUIDATION_HOUR: int = 15  # 강제 청산 시각 (종가경매 전)
    FORCE_LIQUIDATION_MINUTE: int = 10
    MAX_DAILY_TRADES: int = 20
    MAX_SINGLE_ORDER_KRW: int = 0  # 0 = AI 자율 결정 (시스템 하드 리밋 없음)
    MAX_SINGLE_ORDER_USD: int = 0  # 0 = AI 자율 결정

    # === AI Risk Tuning ===
    AI_RISK_TUNING_ENABLED: bool = True
    RISK_APPETITE: str = "AGGRESSIVE"  # CONSERVATIVE / MODERATE / AGGRESSIVE
    MIN_CASH_RATIO: float = 0.05  # 최소 현금 비중 5% (빠른 대응 위한 여유금)

    # === Scheduler ===
    SCHEDULER_ENABLED: bool = True

    @property
    def async_database_url(self) -> str:
        """Sync URL에서 async 드라이버 URL을 자동 생성"""
        url = self.DATABASE_URL
        if url.startswith("sqlite:///"):
            return url.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
        if url.startswith("postgresql://"):
            return url.replace("postgresql://", "postgresql+asyncpg://", 1)
        if url.startswith("mysql://"):
            return url.replace("mysql://", "mysql+aiomysql://", 1)
        return url

    @property
    def is_local(self) -> bool:
        return self.ENVIRONMENT == "local"

    @property
    def is_paper_trading(self) -> bool:
        return bool(self.KIS_PAPER_APP_KEY)

    def validate_on_startup(self) -> None:
        """시작 시 필수 설정 검증 — 누락된 키에 대해 경고 로그"""
        claude_path = self._find_claude_path()
        if claude_path:
            logger.info("Claude Code CLI 감지: {} — CLAUDE_CODE 프로바이더 사용", claude_path)
        else:
            logger.warning(
                "Claude Code CLI를 찾을 수 없음. "
                "CLAUDE_CODE_PATH를 설정하거나 claude CLI를 설치하세요."
            )

        if not self.KIS_APP_KEY and not self.KIS_PAPER_APP_KEY:
            logger.warning(
                "KIS API 키 미설정: KIS_APP_KEY, KIS_PAPER_APP_KEY 모두 비어있음. "
                "실매매/모의투자 모두 불가합니다."
            )

        if not self.TRADING_ENABLED:
            logger.info("TRADING_ENABLED=false: 매매 기능이 비활성화 상태입니다.")


    def _find_claude_path(self) -> str | None:
        """claude CLI 경로 탐색 (설정값 → PATH → 일반적 설치 경로)"""
        import os
        import shutil
        if self.CLAUDE_CODE_PATH:
            return self.CLAUDE_CODE_PATH
        path = shutil.which("claude")
        if path:
            return path
        for candidate in [
            "/opt/homebrew/bin/claude",
            "/usr/local/bin/claude",
            os.path.expanduser("~/.local/bin/claude"),
            os.path.expanduser("~/.npm-global/bin/claude"),
        ]:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None


settings = Settings()
