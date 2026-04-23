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

    # === AI / LLM 백엔드 선택 ===
    # "anthropic": Anthropic API 직접 호출 (기본)
    # "claude_code": Claude Code CLI 구독 크레딧 사용 (롤백 경로)
    LLM_BACKEND: str = "anthropic"

    # --- Anthropic API (LLM_BACKEND=anthropic) ---
    ANTHROPIC_API_KEY: str = ""
    # Claude 4.x는 *-latest alias 미지원 (2026-04 기준). 새 모델 출시 시 env로 수동 업데이트.
    LLM_MODEL_TIER1: str = "claude-haiku-4-5-20251001"
    LLM_MODEL_TIER2: str = "claude-sonnet-4-6"
    LLM_MAX_RETRIES: int = 3
    LLM_REQUEST_TIMEOUT_SEC: int = 120
    LLM_MAX_OUTPUT_TOKENS: int = 4096
    LLM_CACHE_ENABLED: bool = True  # Layered prompt caching

    # --- Claude Code CLI (LLM_BACKEND=claude_code) ---
    CLAUDE_CODE_MODEL: str = "sonnet"
    CLAUDE_CODE_MODEL_TIER1: str = "haiku"
    CLAUDE_CODE_MODEL_TIER2: str = "sonnet"
    CLAUDE_CODE_PATH: str = ""  # 비어있으면 자동 탐색

    # === AI Agent ===
    AUTONOMY_MODE: str = "AUTONOMOUS"  # AUTONOMOUS / SEMI_AUTO
    RECOMMENDATION_EXPIRE_MIN: int = 60

    # === Trading Safety ===
    TRADING_ENABLED: bool = True
    FORCE_LIQUIDATION_HOUR: int = 15  # 장 마감 청산 시각
    FORCE_LIQUIDATION_MINUTE: int = 15
    MAX_DAILY_TRADES: int = 0  # 0 = 무제한 (AI Risk Tuner가 동적 조정)
    MAX_SINGLE_ORDER_KRW: int = 0  # 0 = AI 자율 결정
    MAX_SINGLE_ORDER_USD: int = 0  # 0 = AI 자율 결정
    MIN_BUY_QUANTITY: int = 1

    # === System Hard Limit (AI도 무시 못함) ===
    DAILY_LOSS_LIMIT_HARD: float = -7.0   # 일일 손실 -7% → 전체 매매 즉시 중단

    # === 하위 호환 (AI가 동적 판단하지만 참조 코드 존재) ===
    DAY_TRADING_ONLY: bool = False       # deprecated: AI가 종목별 hold_strategy 판단
    BUY_CUTOFF_HOUR: int = 15            # deprecated: AI가 판단
    BUY_CUTOFF_MINUTE: int = 0
    DAILY_LOSS_LIMIT_SOFT: float = -3.0  # deprecated: AI Risk Tuner가 동적 결정
    MAX_CONSECUTIVE_LOSSES: int = 5      # deprecated: AI Risk Tuner가 동적 결정
    AI_RISK_TUNING_ENABLED: bool = True  # deprecated: 항상 활성
    RISK_APPETITE: str = "AGGRESSIVE"    # deprecated: AI가 국면별 판단
    MIN_CASH_RATIO: float = 0.0          # deprecated: AI Risk Tuner가 결정
    MAX_HOLD_DAYS_STABLE: int = 5        # deprecated: AI가 종목별 판단
    MAX_HOLD_DAYS_AGGRESSIVE: int = 3    # deprecated: AI가 종목별 판단

    # === Scheduler ===
    SCHEDULER_ENABLED: bool = True

    # === LLM 재평가 주기 안전망 (AI가 도출한 값의 극단치만 방지) ===
    REVIEW_INTERVAL_MIN_SAFE_LOW: int = 3    # 분 — 이보다 작으면 노이즈 재호출로 차단
    REVIEW_INTERVAL_MIN_SAFE_HIGH: int = 240  # 분 — 이보다 크면 장중 망각 방지로 차단

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
        return self.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL"

    def validate_on_startup(self) -> None:
        """시작 시 필수 설정 검증 — 누락된 키에 대해 경고 로그"""
        if self.LLM_BACKEND == "anthropic":
            if not self.ANTHROPIC_API_KEY:
                logger.warning(
                    "LLM_BACKEND=anthropic 이지만 ANTHROPIC_API_KEY 미설정 — "
                    "LLM 호출 실패. .env에 키 추가 또는 LLM_BACKEND=claude_code로 전환."
                )
            else:
                logger.debug("Anthropic API backend 활성 (tier1={}, tier2={})",
                             self.LLM_MODEL_TIER1, self.LLM_MODEL_TIER2)
        elif self.LLM_BACKEND == "claude_code":
            claude_path = self._find_claude_path()
            if claude_path:
                logger.debug("Claude Code CLI 감지: {} — CLI 백엔드 활성", claude_path)
            else:
                logger.warning(
                    "LLM_BACKEND=claude_code 이지만 Claude Code CLI를 찾을 수 없음. "
                    "CLAUDE_CODE_PATH를 설정하거나 LLM_BACKEND=anthropic으로 전환."
                )
        else:
            logger.warning("알 수 없는 LLM_BACKEND={} — 'anthropic' 또는 'claude_code' 사용", self.LLM_BACKEND)

        if not self.KIS_APP_KEY and not self.KIS_PAPER_APP_KEY:
            logger.warning(
                "KIS API 키 미설정: KIS_APP_KEY, KIS_PAPER_APP_KEY 모두 비어있음. "
                "실매매/모의투자 모두 불가합니다."
            )

        if not self.TRADING_ENABLED:
            logger.debug("TRADING_ENABLED=false: 매매 기능이 비활성화 상태입니다.")


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
