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
    LLM_MAX_OUTPUT_TOKENS: int = 8192
    LLM_CACHE_ENABLED: bool = True  # Layered prompt caching

    # --- Claude Code CLI (LLM_BACKEND=claude_code) ---
    CLAUDE_CODE_MODEL: str = "sonnet"
    CLAUDE_CODE_MODEL_TIER1: str = "haiku"
    CLAUDE_CODE_MODEL_TIER2: str = "sonnet"
    CLAUDE_CODE_PATH: str = ""  # 비어있으면 자동 탐색


    # === Trading Safety ===
    TRADING_ENABLED: bool = True
    FORCE_LIQUIDATION_HOUR: int = 15  # 장 마감 청산 시각
    FORCE_LIQUIDATION_MINUTE: int = 15
    # 장 초반 시초가 노이즈 매수 차단 (KRX 정규장 개장 직후, 신규 매수만)
    OPENING_BUY_BLOCK_HOUR: int = 9
    OPENING_BUY_BLOCK_MINUTE: int = 30
    # 익절 도달 후 재분석 재트리거 최소 간격(초) — 매 틱 무한 재발화 방지
    TAKE_PROFIT_REVIEW_COOLDOWN_SEC: int = 300
    # 변동성 적응형 트레일링스탑 — 종목 ATR 기반 (노이즈 청산 방지)
    TRAILING_ATR_MULT: float = 0.8     # trailing_pct = ATR% × 0.8
    TRAILING_ATR_MIN_PCT: float = 2.5  # 하한
    TRAILING_ATR_MAX_PCT: float = 7.0  # 상한
    # 분할 익절 + 러너 — LLM 매도 판단 시 일부만 실현, 잔량은 트레일링 전용 러너
    PARTIAL_TP_RATIO: float = 0.5              # LLM 매도 판단 시 매도 비율 (잔량=러너)
    RUNNER_TRAILING_FALLBACK_PCT: float = 3.0  # 러너 전환 시 트레일링 미설정이면 이 값 사용
    # PEAK_SELL 채점 지평 — 매도 후 N거래일 내 최고 종가 기준 조기매도 판정
    PEAK_SELL_VERIFY_TRADING_DAYS: int = 5     # 채점 대상 거래일 수
    PEAK_SELL_MISSED_RALLY_PCT: float = 2.0    # 최고 종가가 매도가 대비 이 % 초과 상승 시 WRONG
    MAX_DAILY_TRADES: int = 0  # 0 = 무제한 (AI Risk Tuner가 동적 조정)
    MAX_SINGLE_ORDER_KRW: int = 0  # 0 = AI 자율 결정
    MAX_SINGLE_ORDER_USD: int = 0  # 0 = AI 자율 결정
    MIN_BUY_QUANTITY: int = 1
    # 분석-주문 간 가격 변동 허용 한도 (%). 초과 시 AI 판단 무효로 간주하고 주문 스킵
    ORDER_PRICE_DRIFT_MAX_PCT: float = 3.0

    # === 급등 감지 즉시 재스캔 (동적 스캔 주기와 무관하게 새 급등주 즉시 포착) ===
    SURGE_RESCAN_ENABLED: bool = True
    SURGE_RESCAN_THRESHOLD_PCT: float = 5.0   # 등락률 +5% 이상 신규 급등
    SURGE_RESCAN_VOL_INC_PCT: float = 200.0   # AND 전일비 거래량 +200% 이상 (수급 동반 — 가격 노이즈 제외)
    SURGE_RESCAN_COOLDOWN_SEC: int = 180      # 직전 스캔 후 최소 간격 (과도한 재스캔 방지)
    SURGE_MONITOR_INTERVAL_SEC: int = 60      # 급등 감지 폴링 주기

    # === System Hard Limit (AI도 무시 못함) ===
    DAILY_LOSS_LIMIT_HARD: float = -7.0   # 일일 손실 -7% → 전체 매매 즉시 중단

    # === 하위 호환 (AI가 동적 판단하지만 참조 코드 존재) ===
    MIN_CASH_RATIO: float = 0.0          # risk_manager 초과매수 가드 기준 (0=준비금 없음)
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
