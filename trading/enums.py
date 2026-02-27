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
