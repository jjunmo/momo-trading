"""StockAnalysisAgent — 종목 분석 통합 파이프라인

신규 후보든 보유종목이든 동일한 데이터 수집 + LLM(Tier2 Sonnet) 분석.
입력만 다르고 파이프라인은 동일:
  데이터 수집 (현재가 + 일봉 + 분봉) → 차트 분석 → LLM 분석 → 수치 결정
"""
import asyncio
import time as _time
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

from agent.base import BaseAgent
from analysis.chart_analyzer import ChartAnalysisResult, chart_analyzer
from analysis.llm.llm_factory import llm_factory
from analysis.llm.prompts.stock_analysis import STOCK_ANALYSIS_PROMPT, STOCK_ANALYSIS_SYSTEM
from core.json_utils import parse_llm_json
from trading.mcp_client import mcp_client

# 분석 목적별 LLM 프롬프트 관점
PURPOSE_CONTEXT = {
    "NEW_BUY": "【분석 관점: 신규 매수 판단】이 종목을 신규 매수할 근거가 충분한지 판단하세요.",
    "TAKE_PROFIT_REVIEW": "【분석 관점: 익절 도달 재검토】익절선에 도달했습니다. 현재 모멘텀과 차트를 보고 매도할지, 목표를 상향하고 수익을 더 키울지 판단하세요.",
    "PERIODIC_REVIEW": "【분석 관점: 보유종목 정기 재평가】보유 중인 종목입니다. 계속 보유할 근거가 유효한지, 손절/익절 수치를 조정할 필요가 있는지, 추가 매수로 평단가를 낮출 기회인지 종합 판단하세요.",
    "REGIME_CHANGE": "【분석 관점: 국면 변화 대응】시장 국면이 변화했습니다. 새 국면에서 이 종목의 보유 논거가 여전히 유효한지, 매도 또는 추가 매수가 필요한지 판단하세요.",
}


@dataclass
class StockAnalysisRequest:
    """분석 요청 — 신규 후보 / 보유종목 공통"""
    symbol: str
    name: str
    strategy_type: str = "STABLE_SHORT"
    # 보유종목인 경우 추가 컨텍스트
    is_holding: bool = False
    avg_price: float = 0.0
    pnl_rate: float = 0.0
    quantity: int = 0
    hold_days: int = 0
    max_hold_days: int = 5
    active_stop_loss: float = 0.0
    active_take_profit: float = 0.0
    active_trailing_stop_pct: float = 0.0
    # 분석 목적 (프롬프트 관점 결정)
    purpose: str = "NEW_BUY"  # NEW_BUY / TAKE_PROFIT_REVIEW / PERIODIC_REVIEW / REGIME_CHANGE
    # 외부 컨텍스트
    market_context: str = ""
    trading_context: str = ""
    feedback_context: str = ""
    cycle_id: str | None = None


@dataclass
class StockAnalysisResult:
    """분석 결과 — 모든 수치 포함"""
    symbol: str
    name: str
    success: bool = False
    # LLM 분석 결과
    recommendation: str = "HOLD"  # BUY / SELL / HOLD
    confidence: float = 0.0
    reason: str = ""
    analysis: str = ""
    # 모든 수치 (LLM이 결정)
    target_price: float = 0.0
    stop_loss_price: float = 0.0
    trailing_stop_pct: float = 0.0
    breakeven_trigger_pct: float = 0.0
    # 현재가/차트 데이터
    current_price: float = 0.0
    chart_result: ChartAnalysisResult = field(default_factory=ChartAnalysisResult)
    price_data: dict = field(default_factory=dict)
    # 재평가 주기 조절 (LLM이 ATR 기반 결정, 가격 트리거)
    review_threshold_pct: float = 0.0
    # 다음 재평가까지 분 (LLM이 종목 특성 기반 결정, 시간 트리거)
    review_interval_min: int = 0
    # 보유 전략 (LLM이 종목별 판단)
    hold_strategy: str = "DAY_CLOSE"  # OVERNIGHT / DAY_CLOSE
    # 메타
    analyzed_at: float = 0.0
    provider: str = ""
    key_factors: list = field(default_factory=list)
    raw_analysis: dict = field(default_factory=dict)


class StockAnalysisAgent(BaseAgent):
    """종목 분석 통합 Agent — 신규/보유 동일 파이프라인, Tier2(Sonnet) 사용

    분석 결과를 저장하여 중복 분석 방지. 국면 변화 시 invalidate.
    """

    def __init__(self):
        self._results: dict[str, StockAnalysisResult] = {}

    @property
    def name(self) -> str:
        return "StockAnalysisAgent"

    def get_result(self, symbol: str) -> StockAnalysisResult | None:
        return self._results.get(symbol)

    def invalidate(self, symbol: str) -> None:
        self._results.pop(symbol, None)

    def invalidate_all(self) -> None:
        self._results.clear()

    async def analyze(self, request: StockAnalysisRequest, *, force: bool = False) -> StockAnalysisResult:
        """종목 분석 실행 — 데이터 수집 → 차트 분석 → LLM(Tier2)

        신규 후보든 보유종목이든 동일한 파이프라인.
        보유종목은 request.is_holding=True + 추가 컨텍스트.
        force=False: 캐시에서 반환 (중복 분석 방지)
        force=True: 캐시 무시, 강제 재분석 (익절 도달, 긴급 재평가 등)
        """
        # 캐시 확인 (force=True면 무시)
        if not force:
            cached = self._results.get(request.symbol)
            if cached and cached.success and cached.analyzed_at > 0:
                from agent.market_regime_agent import market_regime_agent
                elapsed = _time.time() - cached.analyzed_at
                if elapsed < market_regime_agent.scan_interval_sec:
                    logger.debug("[{}] 캐시 히트 ({:.0f}초 전 분석)", request.symbol, elapsed)
                    return cached

        result = StockAnalysisResult(symbol=request.symbol, name=request.name)

        # 1. 데이터 수집 (현재가 + 일봉60일 + 분봉5분 병렬)
        current_price, price_data, daily_df, minute_df = await self._collect_data(request.symbol)
        result.current_price = current_price
        result.price_data = price_data

        if current_price <= 0 and daily_df.empty:
            logger.warning("[{}] 현재가·일봉 모두 없음 → 분석 스킵", request.symbol)
            return result

        if len(daily_df) < 5:
            logger.warning("[{}] 일봉 데이터 부족 ({}개 < 5) → 분석 스킵", request.symbol, len(daily_df))
            return result

        # 2. 차트 분석 (기술적 지표 + 패턴 + 추세)
        chart_result = ChartAnalysisResult()
        if not daily_df.empty:
            chart_result = chart_analyzer.analyze(daily_df, minute_df)
        result.chart_result = chart_result

        # 3. LLM 분석 (Tier2 Sonnet)
        parsed = await self._llm_analyze(request, current_price, price_data, chart_result)
        if not parsed:
            return result

        # 4. 결과 매핑
        result.success = True
        result.recommendation = parsed.get("recommendation", "HOLD")
        result.confidence = float(parsed.get("confidence", 0))
        result.reason = parsed.get("reason", "")
        result.analysis = parsed.get("analysis", "")
        result.target_price = float(parsed.get("target_price", 0))
        result.stop_loss_price = float(parsed.get("stop_loss_price", 0))
        result.trailing_stop_pct = float(parsed.get("trailing_stop_pct", 0))
        result.breakeven_trigger_pct = float(parsed.get("breakeven_trigger_pct", 0))
        result.review_threshold_pct = float(parsed.get("review_threshold_pct", 0))
        try:
            raw_interval = int(float(parsed.get("review_interval_min", 0) or 0))
            if raw_interval > 0:
                from core.config import settings as _s
                clamped = max(_s.REVIEW_INTERVAL_MIN_SAFE_LOW,
                              min(raw_interval, _s.REVIEW_INTERVAL_MIN_SAFE_HIGH))
                if clamped != raw_interval:
                    logger.info("[분석] {} review_interval_min clamp: {}분 → {}분",
                                request.symbol, raw_interval, clamped)
                result.review_interval_min = clamped
            else:
                result.review_interval_min = 0
        except (TypeError, ValueError):
            result.review_interval_min = 0
        result.hold_strategy = parsed.get("hold_strategy", "DAY_CLOSE")
        result.provider = parsed.get("provider", "")
        result.key_factors = parsed.get("key_factors", [])
        result.raw_analysis = parsed
        result.analyzed_at = _time.time()

        # confidence 정규화
        if result.confidence > 1.0:
            result.confidence = result.confidence / 100.0 if result.confidence <= 100.0 else 1.0

        # 보유종목 분석 완료 → PriceGuard 임계값 설정 (분석 Agent가 직접) + DB 동기화
        if request.is_holding and result.success:
            from realtime.event_detector import event_detector
            kwargs = {}
            if result.stop_loss_price > 0:
                kwargs["stop_loss"] = result.stop_loss_price
            if result.target_price > 0:
                kwargs["take_profit"] = result.target_price
            if result.trailing_stop_pct > 0:
                kwargs["trailing_stop_pct"] = result.trailing_stop_pct
            if result.breakeven_trigger_pct > 0:
                kwargs["breakeven_trigger_pct"] = result.breakeven_trigger_pct
            if result.review_threshold_pct > 0:
                kwargs["review_threshold_pct"] = result.review_threshold_pct
            if result.review_interval_min > 0:
                kwargs["review_interval_min"] = result.review_interval_min
                logger.info("[분석] {} 다음 재평가: {}분 후", request.symbol, result.review_interval_min)
            if kwargs:
                event_detector.set_thresholds(request.symbol, **kwargs)
                logger.info("[분석] {} 임계값 설정: {}", request.symbol, kwargs)

            # DB 동기화: trade_results의 open BUY 레코드 업데이트 → 서버 재시작 시 복원값이 최신
            if result.target_price > 0 or result.stop_loss_price > 0 or result.review_interval_min > 0:
                try:
                    from datetime import datetime, timedelta
                    from core.database import AsyncSessionLocal
                    from repositories.trade_result_repository import TradeResultRepository
                    from util.time_util import now_kst
                    async with AsyncSessionLocal() as session:
                        async with session.begin():
                            repo = TradeResultRepository(session)
                            open_positions = await repo.get_all_open()
                            for tr in open_positions:
                                if tr.stock_symbol == request.symbol and tr.side == "BUY":
                                    if result.target_price > 0:
                                        tr.ai_target_price = result.target_price
                                    if result.stop_loss_price > 0:
                                        tr.ai_stop_loss_price = result.stop_loss_price
                                    if result.review_interval_min > 0:
                                        tr.next_review_at = now_kst() + timedelta(minutes=result.review_interval_min)
                except Exception as e:
                    logger.warning("[분석] {} 임계값 DB 동기화 실패: {}", request.symbol, str(e))

        # 결과 저장
        self._results[request.symbol] = result

        return result

    async def analyze_batch(
        self, requests: list[StockAnalysisRequest], max_concurrent: int = 3,
    ) -> list[StockAnalysisResult]:
        """여러 종목 병렬 분석 (Semaphore로 동시 실행 제한)"""
        semaphore = asyncio.Semaphore(max_concurrent)

        async def _limited(req: StockAnalysisRequest) -> StockAnalysisResult:
            async with semaphore:
                return await self.analyze(req)

        return await asyncio.gather(*[_limited(r) for r in requests])

    # ── 내부 메서드 ──

    async def _collect_data(
        self, symbol: str,
    ) -> tuple[float, dict, pd.DataFrame, pd.DataFrame | None]:
        """MCP 병렬 조회: 현재가 + 일봉60일 + 분봉5분

        Returns: (current_price, price_data, daily_df, minute_df)
        """
        price_resp, daily_resp, minute_resp = await asyncio.gather(
            mcp_client.get_current_price(symbol),
            mcp_client.get_daily_price(symbol, count=60),
            mcp_client.get_minute_price(symbol, period="5"),
        )

        current_price = 0.0
        price_data: dict = {}
        if price_resp.success and price_resp.data:
            price_data = price_resp.data
            current_price = float(
                price_data.get("price", price_data.get("current_price", 0))
            )

        daily_df = pd.DataFrame()
        if daily_resp.success and daily_resp.data:
            daily_items = daily_resp.data.get("prices", daily_resp.data.get("items", []))
            if daily_items:
                daily_df = pd.DataFrame(daily_items)
                for col in ["open", "high", "low", "close"]:
                    if col in daily_df.columns:
                        daily_df[col] = pd.to_numeric(daily_df[col], errors="coerce")
                if "volume" in daily_df.columns:
                    daily_df["volume"] = pd.to_numeric(daily_df["volume"], errors="coerce")

        minute_df = None
        if minute_resp.success and minute_resp.data:
            minute_items = minute_resp.data.get("prices", [])
            if minute_items:
                minute_df = pd.DataFrame(minute_items)
                for col in ["open", "high", "low", "close"]:
                    if col in minute_df.columns:
                        minute_df[col] = pd.to_numeric(minute_df[col], errors="coerce")
                if "volume" in minute_df.columns:
                    minute_df["volume"] = pd.to_numeric(minute_df["volume"], errors="coerce")

        return current_price, price_data, daily_df, minute_df

    async def _llm_analyze(
        self,
        request: StockAnalysisRequest,
        current_price: float,
        price_data: dict,
        chart_result: ChartAnalysisResult,
    ) -> dict | None:
        """LLM Tier2(Sonnet) 분석 호출"""

        # 분석 목적 컨텍스트
        purpose_text = PURPOSE_CONTEXT.get(request.purpose, PURPOSE_CONTEXT["NEW_BUY"])

        # 보유종목 추가 컨텍스트
        holding_context = ""
        if request.is_holding:
            holding_context = (
                f"\n### 보유 상태\n"
                f"- 매입가: {request.avg_price:,.0f}원 → 현재가: {current_price:,.0f}원\n"
                f"- 수익률: {request.pnl_rate:+.2f}%\n"
                f"- 보유: {request.quantity}주 / {request.hold_days}일 (최대 {request.max_hold_days}일)\n"
                f"- 활성 손절: {request.active_stop_loss:,.0f}원 | 익절: {request.active_take_profit:,.0f}원"
                f" | 트레일링: {request.active_trailing_stop_pct:.1f}%\n"
            )

        prompt = STOCK_ANALYSIS_PROMPT.format(
            stock_name=request.name,
            symbol=request.symbol,
            current_price=current_price or 0,
            change=float(price_data.get("change") or 0),
            change_rate=float(price_data.get("change_rate") or 0),
            volume=int(float(price_data.get("volume") or 0)),
            technical_indicators=chart_result.indicators_text or "지표 없음",
            chart_patterns=chart_result.patterns_text or "패턴 없음",
            daily_data=chart_result.trend_text or "추세 데이터 없음",
            per=price_data.get("per", "N/A"),
            pbr=price_data.get("pbr", "N/A"),
            market_cap=price_data.get("market_cap", "N/A"),
            feedback_context=request.feedback_context or "매매 이력 없음",
            market_context=f"{purpose_text}\n{request.market_context or '시장 컨텍스트 없음'}{holding_context}",
            trading_context=request.trading_context or "매매 컨텍스트 없음",
        )

        try:
            result_text, provider = await llm_factory.generate_tier2(
                prompt, system_prompt=STOCK_ANALYSIS_SYSTEM,
                symbol=request.symbol, cycle_id=request.cycle_id,
            )
            parsed = parse_llm_json(result_text)
            if parsed:
                parsed["provider"] = provider
            return parsed
        except Exception as e:
            logger.error("[{}] StockAnalysisAgent LLM 분석 실패: {}", request.symbol, str(e))
            return None


# 싱글톤
stock_analysis_agent = StockAnalysisAgent()
