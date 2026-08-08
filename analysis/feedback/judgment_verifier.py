"""판단 검증 엔진 (하네스) — 에이전트 판단의 예상 vs 실제 결과 채점

설계 원칙: LLM은 제안자, 하네스는 집행·검증자.
- 매수 판단: 보유기간 고가/저가 기준 목표가·손절가 터치 여부 채점
- LLM 판단 매도(ANALYSIS_SELL·HOLDINGS_REVIEW·TAKE_PROFIT_REVIEW):
  매도 후 5거래일 내 최고 종가 대비 조기매도 여부 채점
- 시장 국면: 선언 시점 기록 → 장 마감 지수 등락률과 대조
- 트레이딩 규칙: 만료 시 규칙 활성 전/중 성과 비교

적중률 통계는 일일 리뷰 프롬프트 주입 + 권한 가중 게이트에 사용.
"""
import json
from datetime import date, datetime, timedelta

from loguru import logger
from sqlalchemy import select

from util.time_util import now_kst

# 채점 기준 상수
# PEAK_SELL 채점 지평·허용치는 settings(PEAK_SELL_VERIFY_TRADING_DAYS/MISSED_RALLY_PCT) 참조
RULE_MIN_SAMPLES = 3            # 규칙 전/중 비교 최소 표본
RULE_EFFECT_EPS = 0.05          # 규칙 효과 판정 임계 (평균 수익률 %p)
SIDEWAYS_BAND_PCT = 1.2         # SIDEWAYS 국면 허용 지수 등락 범위

# "적중"으로 집계하는 verdict (권한 가중·통계용)
HIT_VERDICTS = {"TARGET_HIT", "CORRECT", "INTERVENED_PROFIT"}
MISS_VERDICTS = {"STOP_HIT", "WRONG", "INTERVENED_LOSS"}


class JudgmentVerifier:
    """판단 채점 엔진 — 16:00 정산·장마감 리뷰 직전에 실행"""

    # ─────────────────────────────────────────
    # 일봉 저장 (검증 데이터 기반)
    # ─────────────────────────────────────────

    async def store_daily_candles(self, symbols: list[str], days: int = 10) -> int:
        """매매 관여 종목의 일봉을 market_data_daily에 저장 (중복 스킵)"""
        if not symbols:
            return 0

        from core.database import AsyncSessionLocal
        from models import MarketDataDaily, Stock
        from trading.mcp_client import mcp_client

        stored = 0
        async with AsyncSessionLocal() as session:
            async with session.begin():
                for symbol in symbols:
                    try:
                        resp = await mcp_client.get_daily_price(symbol, count=days)
                        if not resp.success or not resp.data:
                            continue
                        prices = resp.data.get("prices", [])
                        if not prices:
                            continue

                        stock = (await session.execute(
                            select(Stock).where(Stock.symbol == symbol)
                        )).scalar_one_or_none()
                        if stock is None:
                            stock = Stock(symbol=symbol, name=symbol, market="KOSPI")
                            session.add(stock)
                            await session.flush()

                        existing_dates = set((await session.execute(
                            select(MarketDataDaily.trade_date)
                            .where(MarketDataDaily.stock_id == stock.id)
                        )).scalars().all())

                        for p in prices:
                            d_str = str(p.get("date", ""))
                            if len(d_str) != 8 or p.get("close", 0) <= 0:
                                continue
                            d = date(int(d_str[:4]), int(d_str[4:6]), int(d_str[6:8]))
                            if d in existing_dates:
                                continue
                            session.add(MarketDataDaily(
                                stock_id=stock.id, trade_date=d,
                                open=p.get("open", 0.0), high=p.get("high", 0.0),
                                low=p.get("low", 0.0), close=p.get("close", 0.0),
                                volume=int(p.get("volume", 0) or 0),
                            ))
                            stored += 1
                    except Exception as e:
                        logger.warning("[검증] 일봉 저장 실패 {}: {}", symbol, str(e))

        if stored:
            logger.info("[검증] 일봉 {}건 저장 ({}종목)", stored, len(symbols))
        return stored

    async def _get_candles(self, session, symbol: str, start: date, end: date) -> list:
        """저장된 일봉 조회 (start~end 포함)"""
        from models import MarketDataDaily, Stock
        rows = (await session.execute(
            select(MarketDataDaily)
            .join(Stock, Stock.id == MarketDataDaily.stock_id)
            .where(Stock.symbol == symbol)
            .where(MarketDataDaily.trade_date >= start)
            .where(MarketDataDaily.trade_date <= end)
            .order_by(MarketDataDaily.trade_date)
        )).scalars().all()
        return list(rows)

    # ─────────────────────────────────────────
    # 채점 오케스트레이터
    # ─────────────────────────────────────────

    async def verify_all(self, lookback_days: int = 14) -> dict:
        """미채점 판단 전체 채점 — 16:00 정산·20:05 리뷰 직전 호출"""
        from core.database import AsyncSessionLocal
        from models import TradeResult

        summary = {"candles": 0, "buy": 0, "peak_sell": 0, "regime": 0, "rule": 0}

        # 0) 최근 매매 관여 종목 일봉 확보
        try:
            cutoff = now_kst() - timedelta(days=lookback_days)
            async with AsyncSessionLocal() as session:
                symbols = (await session.execute(
                    select(TradeResult.stock_symbol).distinct()
                    .where(TradeResult.created_at >= cutoff)
                )).scalars().all()
            summary["candles"] = await self.store_daily_candles(list(symbols), days=lookback_days)
        except Exception as e:
            logger.warning("[검증] 일봉 확보 실패: {}", str(e))

        for name, fn in (
            ("buy", self._verify_buy_judgments),
            ("peak_sell", self._verify_peak_sells),
            ("regime", self._verify_regime_judgments),
            ("rule", self._verify_rule_judgments),
        ):
            try:
                summary[name] = await fn(lookback_days)
            except Exception as e:
                logger.error("[검증] {} 채점 오류: {}", name, str(e))

        verified = sum(v for k, v in summary.items() if k != "candles")
        if verified:
            logger.info("[검증] 판단 채점 완료: {}", summary)
        return summary

    async def _unverified_ids(self, session, judgment_type: str) -> set[str]:
        from models import JudgmentVerification
        ids = (await session.execute(
            select(JudgmentVerification.source_id)
            .where(JudgmentVerification.judgment_type == judgment_type)
        )).scalars().all()
        return {i for i in ids if i}

    # ── 매수 판단 채점 ──

    async def _verify_buy_judgments(self, lookback_days: int) -> int:
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification, TradeResult

        cutoff = now_kst() - timedelta(days=lookback_days)
        count = 0
        async with AsyncSessionLocal() as session:
            async with session.begin():
                done = await self._unverified_ids(session, "BUY_ANALYSIS")
                trades = (await session.execute(
                    select(TradeResult)
                    .where(TradeResult.side == "BUY")
                    .where(TradeResult.status == "CONFIRMED")
                    .where(TradeResult.exit_at.isnot(None))
                    .where(TradeResult.exit_price > 0)
                    .where(TradeResult.ai_target_price.isnot(None))
                    .where(TradeResult.exit_at >= cutoff)
                )).scalars().all()

                for tr in trades:
                    if tr.id in done:
                        continue
                    verdict, score, actual = await self._grade_buy(session, tr)
                    session.add(JudgmentVerification(
                        judgment_type="BUY_ANALYSIS", source_id=tr.id,
                        symbol=tr.stock_symbol,
                        rationale=f"conf={tr.ai_confidence:.2f}, strategy={tr.strategy_type}",
                        expected=json.dumps({
                            "target": tr.ai_target_price, "stop": tr.ai_stop_loss_price,
                            "entry": tr.entry_price, "confidence": tr.ai_confidence,
                        }, ensure_ascii=False),
                        actual=json.dumps(actual, ensure_ascii=False),
                        verdict=verdict, score=score,
                        judged_at=tr.entry_at, verified_at=now_kst(),
                    ))
                    count += 1
        return count

    async def _grade_buy(self, session, tr) -> tuple[str, float, dict]:
        """목표/손절 터치 여부 채점 — 일봉 있으면 고가/저가 기준, 없으면 청산가 기준"""
        target = tr.ai_target_price or 0.0
        stop = tr.ai_stop_loss_price or 0.0
        entry = tr.entry_price or 0.0
        exit_px = tr.exit_price or 0.0

        max_high, min_low = exit_px, exit_px
        basis = "exit_price"
        try:
            candles = await self._get_candles(
                session, tr.stock_symbol,
                tr.entry_at.date(), tr.exit_at.date(),
            )
            if candles:
                max_high = max(max(c.high for c in candles), exit_px)
                min_low = min(min(c.low for c in candles), exit_px)
                basis = "daily_candle"
        except Exception:
            pass

        if target > 0 and max_high >= target:
            verdict = "TARGET_HIT"
        elif stop > 0 and min_low <= stop:
            verdict = "STOP_HIT"
        elif (tr.pnl or 0) > 0:
            verdict = "INTERVENED_PROFIT"
        else:
            verdict = "INTERVENED_LOSS"

        # score: 목표 대비 진행률 (보유 중 최고가 기준, 0~1)
        score = 0.0
        if target > entry > 0:
            score = max(0.0, min(1.0, (max_high - entry) / (target - entry)))

        actual = {
            "exit": exit_px, "max_high": max_high, "min_low": min_low,
            "return_pct": tr.return_pct, "exit_reason": tr.exit_reason, "basis": basis,
        }
        return verdict, score, actual

    # ── 정점 판단 매도 채점 ──

    async def _verify_peak_sells(self, lookback_days: int) -> int:
        """LLM 판단 매도: 매도 후 N거래일 내 최고 종가가 매도가 +X% 초과면 조기매도(WRONG)

        비대칭 조기확정: WRONG은 초과 즉시 확정, CORRECT는 N거래일 관찰 완료 필요.
        (기존 '익일 종가' 기준은 급등주의 하루 눌림을 정답으로 오채점 → 5거래일 지평으로 교체)
        """
        from core.config import settings
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification, TradeResult
        from trading.enums import LLM_SELL_REASONS

        horizon = settings.PEAK_SELL_VERIFY_TRADING_DAYS
        threshold = settings.PEAK_SELL_MISSED_RALLY_PCT
        cutoff = now_kst() - timedelta(days=lookback_days)
        count = 0
        async with AsyncSessionLocal() as session:
            async with session.begin():
                done = await self._unverified_ids(session, "PEAK_SELL")
                trades = (await session.execute(
                    select(TradeResult)
                    .where(TradeResult.side == "BUY")
                    .where(TradeResult.exit_reason.in_(LLM_SELL_REASONS))
                    .where(TradeResult.exit_at.isnot(None))
                    .where(TradeResult.exit_price > 0)
                    .where(TradeResult.exit_at >= cutoff)
                )).scalars().all()

                for tr in trades:
                    if tr.id in done:
                        continue
                    exit_d = tr.exit_at.date()
                    # 달력일 +14일 조회 → 앞 N거래일 슬라이스 (주말/휴일 커버)
                    candles = await self._get_candles(
                        session, tr.stock_symbol, exit_d + timedelta(days=1),
                        exit_d + timedelta(days=14),
                    )
                    window = candles[:horizon]
                    if not window:
                        continue  # 캔들 없음 → 다음 실행 대기
                    max_close = max(c.close for c in window)
                    diff_pct = (max_close - tr.exit_price) / tr.exit_price * 100
                    if diff_pct > threshold:
                        verdict = "WRONG"  # 놓친 상승 확정 (관찰일 부족해도 즉시)
                    elif len(window) >= horizon:
                        verdict = "CORRECT"  # 지평 완주 + 미초과
                    else:
                        continue  # 관찰 미완 + 미초과 → 채점 보류
                    session.add(JudgmentVerification(
                        judgment_type="PEAK_SELL", source_id=tr.id,
                        symbol=tr.stock_symbol,
                        rationale=f"LLM 판단 매도 @{tr.exit_price:,.0f} ({tr.exit_reason})",
                        expected=json.dumps({"direction": "DOWN_AFTER_SELL",
                                             "sell_price": tr.exit_price}, ensure_ascii=False),
                        actual=json.dumps({"max_close": max_close,
                                           "diff_pct": round(diff_pct, 2),
                                           "days_observed": len(window),
                                           "exit_reason": tr.exit_reason}, ensure_ascii=False),
                        verdict=verdict,
                        # 놓친 상승폭 반비례: diff 0% → 1.0, +10% 이상 → 0.0
                        score=max(0.0, min(1.0, 1.0 - max(diff_pct, 0.0) / 10.0)),
                        judged_at=tr.exit_at, verified_at=now_kst(),
                    ))
                    count += 1
        return count

    # ── 시장 국면 판단: 기록 + 채점 ──

    async def record_regime_judgment(self, regime: str, kospi_rate: float | None = None) -> None:
        """국면 선언 시점 기록 (market_regime_agent에서 호출, fire-and-forget)"""
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification

        try:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    session.add(JudgmentVerification(
                        judgment_type="MARKET_REGIME",
                        rationale=f"국면 선언: {regime}",
                        expected=json.dumps({
                            "regime": regime,
                            "kospi_rate_at": kospi_rate,
                        }, ensure_ascii=False),
                        judged_at=now_kst(),
                    ))
        except Exception as e:
            logger.warning("[검증] 국면 판단 기록 실패: {}", str(e))

    async def _verify_regime_judgments(self, lookback_days: int) -> int:
        """당일 국면 선언을 장 마감 지수 등락률과 대조 (당일만 채점, 과거분은 EXPIRED)"""
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification

        today = now_kst().date()
        count = 0
        final_rate: float | None = None

        async with AsyncSessionLocal() as session:
            async with session.begin():
                pending = (await session.execute(
                    select(JudgmentVerification)
                    .where(JudgmentVerification.judgment_type == "MARKET_REGIME")
                    .where(JudgmentVerification.verdict.is_(None))
                )).scalars().all()
                if not pending:
                    return 0

                for jv in pending:
                    judged_d = jv.judged_at.date() if jv.judged_at else today
                    if judged_d != today:
                        jv.verdict = "EXPIRED"  # 당일 지수 종가를 놓침 → 채점 불가
                        jv.verified_at = now_kst()
                        count += 1
                        continue

                    if final_rate is None:
                        try:
                            from trading.kis_api import get_market_index
                            idx = await get_market_index("0001")
                            if idx.get("success"):
                                final_rate = float(idx.get("change_rate", 0.0))
                        except Exception as e:
                            logger.warning("[검증] 지수 조회 실패: {}", str(e))
                    if final_rate is None:
                        continue  # 다음 실행에서 재시도

                    try:
                        regime = json.loads(jv.expected).get("regime", "")
                    except Exception:
                        regime = ""

                    if regime in ("BULL", "THEME"):
                        correct = final_rate > 0
                    elif regime == "BEAR":
                        correct = final_rate < 0
                    elif regime == "SIDEWAYS":
                        correct = abs(final_rate) <= SIDEWAYS_BAND_PCT
                    else:
                        jv.verdict = "EXPIRED"
                        jv.verified_at = now_kst()
                        count += 1
                        continue

                    jv.verdict = "CORRECT" if correct else "WRONG"
                    jv.actual = json.dumps({"kospi_close_rate": final_rate}, ensure_ascii=False)
                    jv.verified_at = now_kst()
                    count += 1
        return count

    # ── 트레이딩 규칙 채점 ──

    async def _verify_rule_judgments(self, lookback_days: int) -> int:
        """만료된 규칙: 활성 기간 vs 직전 동일 기간 평균 수익률 비교"""
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification, TradeResult, TradingRule
        from util.time_util import ensure_kst

        now = now_kst()
        cutoff = now - timedelta(days=lookback_days)
        count = 0
        async with AsyncSessionLocal() as session:
            async with session.begin():
                done = await self._unverified_ids(session, "TRADING_RULE")
                rules = (await session.execute(
                    select(TradingRule)
                    .where(TradingRule.rule_type == "PARAM_OVERRIDE")
                    .where(TradingRule.created_at >= cutoff)
                    .where(TradingRule.expires_at <= now)
                )).scalars().all()

                for rule in rules:
                    if rule.id in done:
                        continue

                    start = ensure_kst(rule.created_at)
                    end = ensure_kst(rule.expires_at)
                    span = end - start
                    during = await self._trade_stats(session, start, end)
                    before = await self._trade_stats(session, start - span, start)

                    if during["n"] < RULE_MIN_SAMPLES or before["n"] < RULE_MIN_SAMPLES:
                        verdict, score = "EXPIRED", None  # 표본 부족 — 판정 불가
                    else:
                        delta = during["avg_return"] - before["avg_return"]
                        if delta > RULE_EFFECT_EPS:
                            verdict, score = "CORRECT", 1.0
                        elif delta < -RULE_EFFECT_EPS:
                            verdict, score = "WRONG", 0.0
                        else:
                            verdict, score = "EXPIRED", 0.5  # 유의미한 차이 없음

                    session.add(JudgmentVerification(
                        judgment_type="TRADING_RULE", source_id=rule.id,
                        rationale=f"{rule.strategy_type}.{rule.param_name}={rule.param_value}: {rule.reason[:100]}",
                        expected=rule.expected_effect or json.dumps(
                            {"metric": "avg_return", "direction": "UP"}, ensure_ascii=False),
                        actual=json.dumps({"before": before, "during": during}, ensure_ascii=False),
                        verdict=verdict, score=score,
                        judged_at=rule.created_at, verified_at=now,
                    ))
                    count += 1
        return count

    async def _trade_stats(self, session, start: datetime, end: datetime) -> dict:
        from models import TradeResult
        trades = (await session.execute(
            select(TradeResult.return_pct, TradeResult.is_win)
            .where(TradeResult.side == "BUY")
            .where(TradeResult.status == "CONFIRMED")
            .where(TradeResult.exit_at.isnot(None))
            .where(TradeResult.entry_at >= start)
            .where(TradeResult.entry_at < end)
        )).all()
        n = len(trades)
        if n == 0:
            return {"n": 0, "avg_return": 0.0, "win_rate": 0.0}
        return {
            "n": n,
            "avg_return": round(sum(t[0] or 0 for t in trades) / n, 3),
            "win_rate": round(100.0 * sum(1 for t in trades if t[1]) / n, 1),
        }

    # ─────────────────────────────────────────
    # 적중률 통계 (프롬프트 주입 + 권한 가중 게이트)
    # ─────────────────────────────────────────

    async def get_accuracy_stats(self, days: int = 28) -> dict:
        """판단 유형별 롤링 적중률 — {type: {n, hit, rate}}"""
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification

        cutoff = now_kst() - timedelta(days=days)
        stats: dict[str, dict] = {}
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(JudgmentVerification.judgment_type, JudgmentVerification.verdict)
                .where(JudgmentVerification.verified_at >= cutoff)
                .where(JudgmentVerification.verdict.isnot(None))
            )).all()

        for jtype, verdict in rows:
            if verdict not in HIT_VERDICTS and verdict not in MISS_VERDICTS:
                continue  # EXPIRED 등 판정 불가는 제외
            s = stats.setdefault(jtype, {"n": 0, "hit": 0})
            s["n"] += 1
            if verdict in HIT_VERDICTS:
                s["hit"] += 1

        for s in stats.values():
            s["rate"] = round(100.0 * s["hit"] / s["n"], 1) if s["n"] else 0.0
        return stats

    async def is_judgment_trusted(
        self, judgment_type: str, min_rate: float = 50.0, min_samples: int = 10,
    ) -> bool:
        """권한 가중 게이트 — 적중률 미달 판단 유형의 자동 실행 차단용.

        표본 부족(N<min_samples) 시 True (별도 보수 게이트가 방어).
        """
        try:
            stats = await self.get_accuracy_stats()
            s = stats.get(judgment_type)
            if not s or s["n"] < min_samples:
                return True
            return s["rate"] >= min_rate
        except Exception as e:
            logger.warning("[검증] 적중률 조회 실패 ({}): {}", judgment_type, str(e))
            return True

    async def get_peak_sell_missed_summary(self, days: int = 28) -> str:
        """조기매도로 놓친 상승 요약 — 일일 리뷰 프롬프트 주입용

        PEAK_SELL 채점의 actual.diff_pct(매도 후 5거래일 내 최고 종가 대비 상승률)를 집계.
        표본 없으면 빈 문자열.
        """
        from core.database import AsyncSessionLocal
        from models import JudgmentVerification

        cutoff = now_kst() - timedelta(days=days)
        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(JudgmentVerification.verdict, JudgmentVerification.actual)
                .where(JudgmentVerification.judgment_type == "PEAK_SELL")
                .where(JudgmentVerification.verified_at >= cutoff)
                .where(JudgmentVerification.verdict.in_(["CORRECT", "WRONG"]))
            )).all()

        if not rows:
            return ""

        wrong_diffs = []
        for verdict, actual in rows:
            if verdict != "WRONG" or not actual:
                continue
            try:
                diff = float(json.loads(actual).get("diff_pct", 0))
                wrong_diffs.append(diff)
            except (ValueError, TypeError):
                continue

        if not wrong_diffs:
            return f"- LLM 매도 후 5거래일 추적: {len(rows)}건 모두 적절 (조기매도 없음)"
        return (
            f"- LLM 매도 후 5거래일 추적: {len(rows)}건 중 조기매도 {len(wrong_diffs)}건, "
            f"놓친 상승 평균 +{sum(wrong_diffs) / len(wrong_diffs):.1f}% "
            f"(최대 +{max(wrong_diffs):.1f}%) — 매도 판단 시 추세 지속 가능성을 더 무겁게 볼 것"
        )

    def format_stats_for_prompt(self, stats: dict) -> str:
        """리뷰 프롬프트 주입용 텍스트"""
        if not stats:
            return "아직 채점된 판단 없음 (검증 루프 초기 가동)"
        labels = {
            "BUY_ANALYSIS": "매수 분석", "PEAK_SELL": "LLM 판단 매도(조기매도 여부)",
            "MARKET_REGIME": "시장 국면", "TRADING_RULE": "트레이딩 규칙",
        }
        lines = []
        for jtype, s in sorted(stats.items()):
            lines.append(
                f"- {labels.get(jtype, jtype)}: 적중률 {s['rate']:.0f}% ({s['hit']}/{s['n']}건)"
            )
        return "\n".join(lines)


judgment_verifier = JudgmentVerifier()
