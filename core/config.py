import os
import shutil
from pathlib import Path

from loguru import logger
from pydantic_settings import BaseSettings, SettingsConfigDict

from trading.enums import LLMProvider, LLMTier


VALID_CODEX_REASONING_EFFORTS = {"low", "medium", "high", "xhigh"}


def _resolve_settings_env_file() -> str:
    """런타임 env 파일 선택.

    기본은 .env이고, multi-agent 실험처럼 별도 env를 쓸 때는
    MOMO_ENV_FILE=.env.multi-agent 로 지정한다.
    """
    return os.environ.get("MOMO_ENV_FILE", ".env")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_ignore_empty=True, extra="ignore")

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

    # === AI / LLM ===
    LLM_PROVIDER: str = LLMProvider.CLAUDE_CODE.value  # CLAUDE_CODE / CODEX_CLI

    # Claude Code CLI — 구독 크레딧 사용
    CLAUDE_CODE_MODEL: str = "sonnet"  # 기본 모델 (Tier별 미지정 시 사용)
    CLAUDE_CODE_MODEL_TIER1: str = "haiku"  # Tier1 (스캔/분석): 빠른 모델
    CLAUDE_CODE_MODEL_TIER2: str = "sonnet"  # Tier2 (최종 검토): 정확한 모델
    CLAUDE_CODE_PATH: str = ""  # 비어있으면 자동 탐색 (예: /opt/homebrew/bin/claude)

    # Codex CLI
    CODEX_MODEL: str = "gpt-5.4"  # 기본 모델 (Tier별 미지정 시 사용)
    CODEX_MODEL_TIER1: str = "gpt-5.4-mini"  # Tier1 (스캔/분석): 빠른 모델
    CODEX_MODEL_TIER2: str = "gpt-5.4"  # Tier2 (최종 검토): 정확한 모델
    CODEX_MODEL_ORCHESTRATOR: str = "gpt-5.4"  # 오케스트레이터 전용
    CODEX_REASONING_EFFORT: str = ""  # 공통 fallback
    CODEX_REASONING_EFFORT_TIER1: str = "medium"
    CODEX_REASONING_EFFORT_TIER2: str = "high"
    CODEX_REASONING_EFFORT_ORCHESTRATOR: str = "xhigh"
    CODEX_CLI_PATH: str = ""  # 비어있으면 PATH 자동 탐색
    CODEX_DISABLE_MCP: bool = True  # 자동매매 LLM 호출에서는 사용자 Codex MCP 설정 차단

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

    @property
    def llm_provider(self) -> LLMProvider:
        """선택된 LLM provider enum."""
        raw = (self.LLM_PROVIDER or LLMProvider.CLAUDE_CODE.value).strip().upper()
        try:
            return LLMProvider(raw)
        except ValueError:
            logger.warning("알 수 없는 LLM_PROVIDER={} — CLAUDE_CODE로 대체", self.LLM_PROVIDER)
            return LLMProvider.CLAUDE_CODE

    @property
    def runtime_env_file(self) -> str:
        return _resolve_settings_env_file()

    def get_llm_model(self, provider: LLMProvider, tier: LLMTier) -> str:
        """provider/tier별 모델명."""
        if provider == LLMProvider.CODEX_CLI:
            if tier == LLMTier.TIER1:
                return self.CODEX_MODEL_TIER1 or self.CODEX_MODEL or "gpt-5.4-mini"
            return self.CODEX_MODEL_TIER2 or self.CODEX_MODEL or "gpt-5.4"

        if tier == LLMTier.TIER1:
            return self.CLAUDE_CODE_MODEL_TIER1 or self.CLAUDE_CODE_MODEL or "haiku"
        return self.CLAUDE_CODE_MODEL_TIER2 or self.CLAUDE_CODE_MODEL or "sonnet"

    def get_llm_reasoning_effort(self, provider: LLMProvider, tier: LLMTier) -> str:
        """provider/tier별 추론 강도. Claude는 provider 내부 기본값을 사용."""
        if provider != LLMProvider.CODEX_CLI:
            return ""
        if tier == LLMTier.TIER1:
            return self._parse_codex_reasoning_effort(
                self.CODEX_REASONING_EFFORT_TIER1 or self.CODEX_REASONING_EFFORT or "medium",
                fallback="medium",
            )
        return self._parse_codex_reasoning_effort(
            self.CODEX_REASONING_EFFORT_TIER2 or self.CODEX_REASONING_EFFORT or "high",
            fallback="high",
        )

    def get_orchestrator_llm_model_for_provider(self, provider: LLMProvider) -> str:
        if provider == LLMProvider.CODEX_CLI:
            return self.CODEX_MODEL_ORCHESTRATOR or self.CODEX_MODEL or "gpt-5.4"
        return self.CLAUDE_CODE_MODEL_TIER2 or self.CLAUDE_CODE_MODEL or "sonnet"

    def get_orchestrator_llm_reasoning_effort_for_provider(self, provider: LLMProvider) -> str:
        if provider != LLMProvider.CODEX_CLI:
            return ""
        return self._parse_codex_reasoning_effort(
            self.CODEX_REASONING_EFFORT_ORCHESTRATOR or "xhigh",
            fallback="xhigh",
        )

    def get_llm_cli_path(self, provider: LLMProvider) -> str | None:
        if provider == LLMProvider.CODEX_CLI:
            return self._find_codex_path()
        return self._find_claude_path()

    def validate_on_startup(self) -> None:
        """시작 시 필수 설정 검증 — 누락된 키에 대해 경고 로그"""
        self._validate_codex_reasoning_efforts()

        provider = self.llm_provider
        cli_path = self.get_llm_cli_path(provider)
        if cli_path:
            logger.debug("{} CLI 감지: {}", provider.value, cli_path)
        else:
            logger.warning(
                "{} CLI를 찾을 수 없음. CLI PATH 설정 또는 설치 상태를 확인하세요.",
                provider.value,
            )

        if not self.KIS_APP_KEY and not self.KIS_PAPER_APP_KEY:
            logger.warning(
                "KIS API 키 미설정: KIS_APP_KEY, KIS_PAPER_APP_KEY 모두 비어있음. "
                "실매매/모의투자 모두 불가합니다."
            )

        if not self.TRADING_ENABLED:
            logger.debug("TRADING_ENABLED=false: 매매 기능이 비활성화 상태입니다.")

    def _find_claude_path(self) -> str | None:
        """claude CLI 경로 탐색 (설정값 → PATH → 일반적 설치 경로)"""
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

    def _find_codex_path(self) -> str | None:
        """codex CLI 경로 탐색 (설정값 → PATH → 일반적 설치 경로)"""
        if self.CODEX_CLI_PATH:
            return self.CODEX_CLI_PATH
        path = shutil.which("codex")
        if path:
            return path
        for candidate in [
            "/opt/homebrew/bin/codex",
            "/usr/local/bin/codex",
            str(Path.home() / ".local/bin/codex"),
            str(Path.home() / ".npm-global/bin/codex"),
        ]:
            if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
                return candidate
        return None

    def _parse_codex_reasoning_effort(self, value: str, *, fallback: str) -> str:
        effort = (value or "").strip().lower()
        if not effort:
            return fallback
        if effort not in VALID_CODEX_REASONING_EFFORTS:
            logger.warning("잘못된 Codex reasoning effort={} — {}로 대체", value, fallback)
            return fallback
        return effort

    def _validate_codex_reasoning_efforts(self) -> None:
        for key, fallback in [
            ("CODEX_REASONING_EFFORT_TIER1", "medium"),
            ("CODEX_REASONING_EFFORT_TIER2", "high"),
            ("CODEX_REASONING_EFFORT_ORCHESTRATOR", "xhigh"),
        ]:
            self._parse_codex_reasoning_effort(getattr(self, key, ""), fallback=fallback)


settings = Settings(_env_file=_resolve_settings_env_file())
