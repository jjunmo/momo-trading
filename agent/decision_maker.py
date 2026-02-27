"""매매 결정 + 자율/반자율 모드 분기"""
from datetime import timedelta

from loguru import logger

from core.config import settings
from core.events import Event, EventType, event_bus
from models.order import Order
from models.recommendation import Recommendation
from services.activity_logger import activity_logger
from strategy.signal import TradeSignal
from trading.enums import AutonomyMode, OrderSource, RecommendationStatus
from trading.mcp_client import mcp_client
from util.time_util import now_kst


class DecisionMaker:
    """
    자율/반자율 모드에 따라 실행 방식을 분기.

    AUTONOMOUS: 스캔 → 분석 → 매매까지 전자동
    SEMI_AUTO: 스캔 → 분석 → 추천 생성 → 사용자 승인 대기
    """

    async def execute(
        self, signal: TradeSignal, analysis_id: str = "", cycle_id: str | None = None,
    ) -> dict:
        """시그널에 따라 실행"""
        mode = AutonomyMode(settings.AUTONOMY_MODE)

        if mode == AutonomyMode.AUTONOMOUS:
            return await self._execute_autonomous(signal, cycle_id)
        else:
            return await self._create_recommendation(signal, analysis_id, cycle_id)

    async def _execute_autonomous(self, signal: TradeSignal, cycle_id: str | None = None) -> dict:
        """완전자율: MCP로 즉시 주문 실행"""
        logger.info(
            "[AUTONOMOUS] 주문 실행: {} {} x{} @ {}",
            signal.symbol, signal.action.value,
            signal.suggested_quantity, signal.suggested_price,
        )

        qty = signal.suggested_quantity or 0
        price = signal.suggested_price or 0
        amount = price * qty
        await activity_logger.log(
            "DECISION", "START",
            f"\U0001f4b0 [{signal.symbol}] 자동 주문 실행: "
            f"{signal.action.value} {qty}주 "
            f"@{price:,.0f}원 ({amount:,.0f}원)",
            cycle_id=cycle_id,
            symbol=signal.symbol,
        )

        response = await mcp_client.place_order(
            symbol=signal.symbol,
            side=signal.action.value,
            quantity=signal.suggested_quantity or 0,
            price=signal.suggested_price,
        )

        # 주문 응답 검증: MCP success + 주문번호 존재 확인
        order_data = response.data or {}
        order_id = order_data.get("order_id") or order_data.get("ODNO", "")
        is_submitted = response.success and bool(order_id)

        result = {
            "mode": "AUTONOMOUS",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "success": is_submitted,
            "order_id": order_id,
            "message": "주문 접수" if is_submitted else (response.error or "주문 응답 없음"),
            "data": response.data,
        }

        if is_submitted:
            await activity_logger.log(
                "DECISION", "COMPLETE",
                f"\u2705 [{signal.symbol}] 주문 접수 완료 (체결 대기) — 주문번호: {order_id}",
                cycle_id=cycle_id, symbol=signal.symbol,
                detail=result,
            )
        else:
            await activity_logger.log(
                "DECISION", "ERROR",
                f"\u274c [{signal.symbol}] 주문 실패: {response.error or '주문번호 없음'}",
                cycle_id=cycle_id, symbol=signal.symbol,
                error_message=response.error or "주문번호 없음",
            )

        await event_bus.publish(Event(
            type=EventType.ORDER_EXECUTED,
            data=result,
            source="decision_maker",
        ))

        return result

    async def _create_recommendation(
        self, signal: TradeSignal, analysis_id: str, cycle_id: str | None = None,
    ) -> dict:
        """반자율: 추천 생성 → 사용자 승인 대기"""
        expires_at = now_kst() + timedelta(minutes=settings.RECOMMENDATION_EXPIRE_MIN)

        rec_data = {
            "stock_id": signal.stock_id,
            "analysis_id": analysis_id,
            "action": signal.action.value,
            "suggested_price": signal.suggested_price or 0,
            "suggested_quantity": signal.suggested_quantity or 0,
            "reason": signal.reason,
            "confidence": signal.confidence,
            "status": RecommendationStatus.PENDING.value,
            "expires_at": expires_at,
        }

        qty = signal.suggested_quantity or 0
        price = signal.suggested_price or 0
        amount = price * qty

        logger.info(
            "[SEMI_AUTO] 추천 생성: {} {} x{} (만료: {})",
            signal.symbol, signal.action.value,
            qty, expires_at,
        )

        await activity_logger.log(
            "DECISION", "COMPLETE",
            f"\U0001f4dd 매수 추천 생성: {signal.symbol} {qty}주 "
            f"@{price:,.0f}원 ({amount:,.0f}원)"
            f"\n   \u2192 사용자 승인 대기 (SEMI_AUTO 모드)",
            cycle_id=cycle_id,
            symbol=signal.symbol,
            confidence=signal.confidence,
            detail=rec_data,
        )

        await event_bus.publish(Event(
            type=EventType.RECOMMENDATION_CREATED,
            data={**rec_data, "symbol": signal.symbol},
            source="decision_maker",
        ))

        return {
            "mode": "SEMI_AUTO",
            "symbol": signal.symbol,
            "action": signal.action.value,
            "recommendation": rec_data,
        }


decision_maker = DecisionMaker()
