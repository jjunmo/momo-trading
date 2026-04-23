from enum import Enum


class Market(str, Enum):
    """주식 시장"""
    KOSPI = "KOSPI"
    KOSDAQ = "KOSDAQ"
    NASDAQ = "NASDAQ"
    NYSE = "NYSE"
    AMEX = "AMEX"


class OrderSide(str, Enum):
    """주문 방향"""
    BUY = "BUY"
    SELL = "SELL"


class OrderType(str, Enum):
    """주문 유형"""
    MARKET = "MARKET"
    LIMIT = "LIMIT"


class OrderStatus(str, Enum):
    """주문 상태"""
    PENDING = "PENDING"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class OrderSource(str, Enum):
    """주문 출처"""
    AI = "AI"
    MANUAL = "MANUAL"
    RISK = "RISK"


class PortfolioType(str, Enum):
    """포트폴리오 유형"""
    STABLE_SHORT = "STABLE_SHORT"
    AGGRESSIVE_SHORT = "AGGRESSIVE_SHORT"


class TradingMode(str, Enum):
    """매매 모드"""
    PAPER = "PAPER"
    LIVE = "LIVE"


class AutonomyMode(str, Enum):
    """자율 모드"""
    AUTONOMOUS = "AUTONOMOUS"
    SEMI_AUTO = "SEMI_AUTO"


class AnalysisType(str, Enum):
    """분석 유형"""
    TECHNICAL = "TECHNICAL"
    FUNDAMENTAL = "FUNDAMENTAL"
    SENTIMENT = "SENTIMENT"
    COMPREHENSIVE = "COMPREHENSIVE"


class SignalAction(str, Enum):
    """매매 시그널"""
    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class SignalUrgency(str, Enum):
    """시그널 긴급도"""
    IMMEDIATE = "IMMEDIATE"
    WAIT = "WAIT"


class RecommendationStatus(str, Enum):
    """AI 추천 상태 (반자율 모드)"""
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    EXECUTED = "EXECUTED"


class LLMTier(str, Enum):
    """LLM 계층"""
    TIER1 = "TIER1"      # 빠른 분석 (스캔/선별)
    TIER2 = "TIER2"      # 프리미엄 (최종 검토)


class LLMProvider(str, Enum):
    """LLM 제공자"""
    CLAUDE_CODE = "CLAUDE_CODE"  # 로컬 Claude Code CLI (구독 크레딧)
    ANTHROPIC = "ANTHROPIC"      # Anthropic API (API 키)


class OrderConfirmStatus(str, Enum):
    """주문 체결 확인 상태"""
    PENDING_CONFIRM = "PENDING_CONFIRM"
    CONFIRMED = "CONFIRMED"
    CONFIRM_FAILED = "CONFIRM_FAILED"


class ActivityType(str, Enum):
    """에이전트 활동 유형"""
    CYCLE = "CYCLE"
    SCAN = "SCAN"
    SCREENING = "SCREENING"
    TIER1_ANALYSIS = "TIER1_ANALYSIS"
    TIER2_REVIEW = "TIER2_REVIEW"
    STRATEGY_EVAL = "STRATEGY_EVAL"
    RISK_CHECK = "RISK_CHECK"
    RISK_TUNING = "RISK_TUNING"
    RISK_GATE = "RISK_GATE"
    DECISION = "DECISION"
    TRADE_RESULT = "TRADE_RESULT"
    ORDER = "ORDER"
    EVENT = "EVENT"
    LLM_CALL = "LLM_CALL"
    DAILY_PLAN = "DAILY_PLAN"
    REPORT = "REPORT"
    SCHEDULE = "SCHEDULE"
    HOLDINGS_CHECK = "HOLDINGS_CHECK"
    TRADING_RULE = "TRADING_RULE"
    QA = "QA"


class ActivityPhase(str, Enum):
    """활동 단계"""
    START = "START"
    PROGRESS = "PROGRESS"
    COMPLETE = "COMPLETE"
    ERROR = "ERROR"
    SKIP = "SKIP"
