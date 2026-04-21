"""MarketRegimeAgent — 시장 국면 감시 + 동적 스캔/재평가 트리거

10분 간격으로 시장 지수/변동성을 확인하여 국면(BULL/THEME/SIDEWAYS/BEAR)을 판단.
복합 스코어링: 당일 등락률 + MA 위치(3일/10일) + 시장 폭 + 거래량 추이
국면 변화 감지 시 보유종목 긴급 재평가 + 시장 재스캔을 트리거한다.
국면별 스캔 주기를 동적으로 제어한다.
"""
import asyncio
import time

from loguru import logger

from agent.base import BaseAgent
from trading.enums import ActivityPhase, ActivityType

# 지수 프록시 ETF
_KOSPI_PROXY = "069500"   # KODEX 200
_KOSDAQ_PROXY = "229200"  # KODEX 코스닥150


class MarketRegimeAgent(BaseAgent):
    """시장 국면 감시 Agent

    복합 스코어링으로 안정적 국면 판단:
    - 당일 등락률 (보조, 1점)
    - MA 위치 3일/10일 (핵심, 2점)
    - 시장 폭 상승/하락 종목수 (보조, 1점)
    - 거래량 추이 전일 대비 (보조, 1점)
    - 급변 오버라이드: ±1.2% 이상이면 즉시 전환
    """

    DEFAULT_INTERVAL_SEC = 600   # 10분
    URGENT_INTERVAL_SEC = 300    # 5분 (급변 시)

    # 급변 오버라이드 임계값
    OVERRIDE_THRESHOLD = 1.2  # ±1.2% 이상이면 MA 무시, 즉시 전환

    # 국면별 시장 스캔 주기 (초)
    SCAN_INTERVAL: dict[str, int] = {
        "BULL": 1800,     # 30분 — 모멘텀 종목 포착
        "THEME": 1200,    # 20분 — 테마 급등 빠르게 포착
        "SIDEWAYS": 3600, # 60분 — 기본 주기
        "BEAR": 5400,     # 90분 — 스캔 줄이고 보유 재평가에 집중
    }
    DEFAULT_SCAN_INTERVAL = 3600  # 60분

    @property
    def name(self) -> str:
        return "MarketRegimeAgent"

    def __init__(self):
        self._current_regime: str = ""
        self._previous_regime: str = ""
        self._last_check_at: float = 0.0
        self._regime_changed_at: float = 0.0
        self._running = False
        self._task: asyncio.Task | None = None
        self._scan_task: asyncio.Task | None = None
        # 콜백
        self._on_regime_change_callback = None
        self._on_scan_trigger_callback = None
        # 최근 지수 데이터
        self._last_kospi: dict = {}
        self._last_kosdaq: dict = {}
        # 스캔 추적
        self._last_scan_at: float = 0.0
        # ── 복합 스코어링 데이터 ──
        # ETF 일봉 캐시 (하루 1회 갱신)
        self._daily_cache: dict[str, list[dict]] = {}
        self._daily_cache_date: str = ""
        # 전일 거래량 (거래량 추이 비교용)
        self._prev_day_volume: dict[str, int] = {}
        # 시장 폭 (scanner가 update_breadth()로 전달)
        self._advancing: int = 0
        self._declining: int = 0

    @property
    def current_regime(self) -> str:
        return self._current_regime

    @property
    def interval_sec(self) -> float:
        """국면에 따른 체크 간격"""
        if self._current_regime in ("BEAR", "THEME"):
            return self.URGENT_INTERVAL_SEC
        return self.DEFAULT_INTERVAL_SEC

    @property
    def scan_interval_sec(self) -> int:
        """현재 국면에 따른 시장 스캔 주기"""
        return self.SCAN_INTERVAL.get(self._current_regime, self.DEFAULT_SCAN_INTERVAL)

    def set_regime_change_callback(self, callback) -> None:
        """국면 변화 시 호출할 콜백 등록

        callback(new_regime: str, old_regime: str) -> None
        """
        self._on_regime_change_callback = callback

    def set_scan_trigger_callback(self, callback) -> None:
        """동적 스캔 트리거 콜백 등록

        callback() -> None (시장 재스캔 실행)
        """
        self._on_scan_trigger_callback = callback

    def set_regime(self, regime: str) -> None:
        """외부에서 국면 설정 (MarketScanAgent 결과 반영)"""
        if regime and regime != self._current_regime:
            self._previous_regime = self._current_regime
            self._current_regime = regime
            self._regime_changed_at = time.time()
            logger.info("시장 국면 변경: {} → {} (외부 설정)", self._previous_regime or "없음", regime)

    async def start(self) -> None:
        """국면 감시 + 동적 스캔 루프 시작"""
        self._running = True
        self._task = asyncio.create_task(self._monitor_loop())
        self._scan_task = asyncio.create_task(self._dynamic_scan_loop())
        logger.info(
            "MarketRegimeAgent 시작 (국면 체크 {}초, 스캔 {}초)",
            self.DEFAULT_INTERVAL_SEC, self.DEFAULT_SCAN_INTERVAL,
        )

    async def stop(self) -> None:
        """감시 루프 중지"""
        self._running = False
        for task in (self._task, self._scan_task):
            if task:
                task.cancel()
        self._task = None
        self._scan_task = None

    async def _monitor_loop(self) -> None:
        """주기적 국면 체크 루프"""
        while self._running:
            try:
                await self.check_regime()
            except Exception as e:
                logger.warning("MarketRegimeAgent 체크 오류: {}", str(e))

            await asyncio.sleep(self.interval_sec)

    async def _dynamic_scan_loop(self) -> None:
        """국면별 동적 주기로 시장 스캔 트리거"""
        # 장 시작 스캔(09:05)은 스케줄러가 처리하므로 첫 동적 스캔은 대기 후 시작
        await asyncio.sleep(self.scan_interval_sec)

        while self._running:
            from scheduler.market_calendar import market_calendar
            if not market_calendar.is_domestic_trading_hours():
                await asyncio.sleep(60)
                continue

            # 세션 마감 이후 스캔 불필요
            from util.time_util import now_kst
            if now_kst().time() >= market_calendar.get_trading_cutoff():
                await asyncio.sleep(300)
                continue

            if self._on_scan_trigger_callback:
                try:
                    logger.info(
                        "동적 스캔 트리거 (국면: {}, 주기: {}분)",
                        self._current_regime or "미설정", self.scan_interval_sec // 60,
                    )
                    self._last_scan_at = time.time()
                    await self._on_scan_trigger_callback()
                except Exception as e:
                    logger.error("동적 스캔 트리거 오류: {}", str(e))

            await asyncio.sleep(self.scan_interval_sec)

    async def check_regime(self) -> str:
        """시장 지수 조회 → 국면 판단 → 변화 시 트리거"""
        from trading.kis_api import get_market_index
        from scheduler.market_calendar import market_calendar

        if not market_calendar.is_domestic_trading_hours():
            return self._current_regime

        # 지수 데이터 수집 + 일봉 캐시 갱신
        try:
            kospi, kosdaq = await asyncio.gather(
                get_market_index("0001"),
                get_market_index("2001"),
            )
            self._last_kospi = kospi
            self._last_kosdaq = kosdaq
        except Exception as e:
            logger.warning("지수 조회 실패: {}", str(e))
            return self._current_regime

        # ETF 일봉 캐시 갱신 (하루 1회, MA 계산용)
        await self._ensure_daily_cache()

        # 국면 판단 (복합 스코어링 — 등락률 + MA + 시장폭 + 거래량)
        new_regime = self._classify_regime(kospi, kosdaq)
        self._last_check_at = time.time()

        if new_regime and new_regime != self._current_regime:
            old = self._current_regime
            self._previous_regime = old
            self._current_regime = new_regime
            self._regime_changed_at = time.time()

            logger.info("시장 국면 변경 감지: {} → {}", old or "없음", new_regime)

            # 활동 로그
            try:
                from services.activity_logger import activity_logger
                await activity_logger.log(
                    ActivityType.EVENT, ActivityPhase.PROGRESS,
                    f"📊 시장 국면 변경: {old or '없음'} → {new_regime}",
                )
            except Exception:
                pass

            # 분석 결과 무효화 (국면 변화 → 기존 분석 무효)
            try:
                from agent.stock_analysis_agent import stock_analysis_agent
                stock_analysis_agent.invalidate_all()
                logger.info("국면 변화 → 분석 결과 전체 무효화")
            except Exception:
                pass

            # 콜백 트리거: 보유종목 긴급 재평가 + 시장 재스캔
            if self._on_regime_change_callback:
                try:
                    await self._on_regime_change_callback(new_regime, old)
                except Exception as e:
                    logger.error("국면 변화 재평가 콜백 오류: {}", str(e))
            if self._on_scan_trigger_callback:
                try:
                    logger.info("국면 변화 → 시장 긴급 재스캔 트리거")
                    self._last_scan_at = time.time()
                    await self._on_scan_trigger_callback()
                except Exception as e:
                    logger.error("국면 변화 스캔 콜백 오류: {}", str(e))

        return self._current_regime

    # ── 시장 폭 업데이트 (scanner에서 호출) ──

    def update_breadth(self, advancing: int, declining: int) -> None:
        """시장 폭 갱신 — market_scanner.scan()에서 호출"""
        self._advancing = advancing
        self._declining = declining

    # ── ETF 일봉 캐싱 (하루 1회) ──

    async def _ensure_daily_cache(self) -> None:
        """ETF 프록시 일봉을 하루 1회 캐싱"""
        from util.time_util import now_kst
        today = now_kst().strftime("%Y%m%d")
        if self._daily_cache_date == today and self._daily_cache:
            return  # 이미 오늘 캐시 있음

        from trading.kis_api import get_stock_daily_chart
        try:
            results = await asyncio.gather(
                get_stock_daily_chart(_KOSPI_PROXY, count=15),
                get_stock_daily_chart(_KOSDAQ_PROXY, count=15),
                return_exceptions=True,
            )
            for sym, resp in zip([_KOSPI_PROXY, _KOSDAQ_PROXY], results):
                if isinstance(resp, Exception):
                    logger.warning("일봉 캐시 실패 [{}]: {}", sym, resp)
                    continue
                if resp.get("success") and resp.get("prices"):
                    self._daily_cache[sym] = resp["prices"]
                    # 전일 거래량 저장
                    if len(resp["prices"]) >= 2:
                        try:
                            self._prev_day_volume[sym] = int(resp["prices"][1].get("volume", 0))
                        except (ValueError, TypeError):
                            pass
            self._daily_cache_date = today
            logger.debug("일봉 캐시 갱신: KOSPI {}일, KOSDAQ {}일",
                         len(self._daily_cache.get(_KOSPI_PROXY, [])),
                         len(self._daily_cache.get(_KOSDAQ_PROXY, [])))
        except Exception as e:
            logger.warning("일봉 캐시 갱신 실패: {}", str(e))

    @staticmethod
    def _calc_sma(prices: list[dict], period: int) -> float | None:
        """일봉 리스트에서 SMA 계산 (prices[0]이 최근)"""
        if len(prices) < period:
            return None
        try:
            closes = [float(p["close"]) for p in prices[:period]]
            return sum(closes) / period
        except (ValueError, TypeError, KeyError):
            return None

    def _get_ma_signals(self, proxy_symbol: str, current_price: float) -> tuple[bool, bool]:
        """프록시 ETF의 MA 위 여부 반환: (above_3ma, above_10ma)"""
        prices = self._daily_cache.get(proxy_symbol, [])
        if not prices:
            return True, True  # 데이터 없으면 중립 (bull 가정)

        sma3 = self._calc_sma(prices, 3)
        sma10 = self._calc_sma(prices, 10)
        above_3 = current_price > sma3 if sma3 else True
        above_10 = current_price > sma10 if sma10 else True
        return above_3, above_10

    def _get_volume_change(self, proxy_symbol: str, current_volume: int) -> float:
        """전일 대비 거래량 변화율 (%). 데이터 없으면 0.0"""
        prev = self._prev_day_volume.get(proxy_symbol, 0)
        if prev <= 0 or current_volume <= 0:
            return 0.0
        return ((current_volume - prev) / prev) * 100

    def _classify_regime(self, kospi: dict, kosdaq: dict) -> str:
        """복합 스코어링 국면 판단

        시그널 조합 (최대 bull/bear 각 8점):
        1. 당일 등락률: ±0.5% 기준 (1점)
        2. 급변 오버라이드: ±1.2% 이상이면 즉시 반환
        3. MA 위치 3일/10일: KOSPI+KOSDAQ 각각 (2점)
        4. 시장 폭: 상승비율 60%↑/40%↓ (1점)
        5. 거래량 추이: 전일 대비 (1점)
        """
        kospi_rate = kospi.get("change_rate", 0) if kospi.get("success") else 0
        kosdaq_rate = kosdaq.get("change_rate", 0) if kosdaq.get("success") else 0
        kospi_price = kospi.get("price", 0) if kospi.get("success") else 0
        kosdaq_price = kosdaq.get("price", 0) if kosdaq.get("success") else 0
        kospi_vol = kospi.get("volume", 0) if kospi.get("success") else 0

        # ── 급변 오버라이드: ±1.2% 이상이면 즉시 ──
        if kospi_rate >= self.OVERRIDE_THRESHOLD or kosdaq_rate >= self.OVERRIDE_THRESHOLD:
            logger.info("국면 급변 오버라이드 → BULL (KOSPI {:+.2f}%, KOSDAQ {:+.2f}%)",
                        kospi_rate, kosdaq_rate)
            return "BULL"
        if kospi_rate <= -self.OVERRIDE_THRESHOLD or kosdaq_rate <= -self.OVERRIDE_THRESHOLD:
            logger.info("국면 급변 오버라이드 → BEAR (KOSPI {:+.2f}%, KOSDAQ {:+.2f}%)",
                        kospi_rate, kosdaq_rate)
            return "BEAR"

        score_bull = 0
        score_bear = 0
        details = []

        # ── 시그널 1: 당일 등락률 (1점) ──
        if kospi_rate > 0.5 and kosdaq_rate > 0.5:
            score_bull += 1
            details.append("등락↑")
        elif kospi_rate < -0.5 and kosdaq_rate < -0.5:
            score_bear += 1
            details.append("등락↓")

        # ── 시그널 2: MA 위치 (KOSPI 2점 + KOSDAQ 2점) ──
        kospi_a3, kospi_a10 = self._get_ma_signals(_KOSPI_PROXY, kospi_price)
        kosdaq_a3, kosdaq_a10 = self._get_ma_signals(_KOSDAQ_PROXY, kosdaq_price)

        if kospi_a3 and kospi_a10:
            score_bull += 2
            details.append("KOSPI_MA↑")
        elif not kospi_a3 and not kospi_a10:
            score_bear += 2
            details.append("KOSPI_MA↓")

        if kosdaq_a3 and kosdaq_a10:
            score_bull += 2
            details.append("KOSDAQ_MA↑")
        elif not kosdaq_a3 and not kosdaq_a10:
            score_bear += 2
            details.append("KOSDAQ_MA↓")

        # ── 시그널 3: 시장 폭 (1점) ──
        total_breadth = self._advancing + self._declining
        if total_breadth > 0:
            adv_ratio = self._advancing / total_breadth
            if adv_ratio > 0.6:
                score_bull += 1
                details.append(f"폭↑{adv_ratio:.0%}")
            elif adv_ratio < 0.4:
                score_bear += 1
                details.append(f"폭↓{adv_ratio:.0%}")

        # ── 시그널 4: 거래량 추이 (1점) ──
        vol_change = self._get_volume_change(_KOSPI_PROXY, kospi_vol)
        if vol_change > 10 and kospi_rate > 0:
            score_bull += 1
            details.append(f"거래량↑{vol_change:+.0f}%")
        elif vol_change > 10 and kospi_rate < 0:
            score_bear += 1
            details.append(f"거래량↑하락{vol_change:+.0f}%")

        # ── 종합 판단 ──
        regime = "SIDEWAYS"
        if score_bull >= 4:
            regime = "BULL"
        elif score_bear >= 4:
            regime = "BEAR"
        elif abs(kospi_rate - kosdaq_rate) >= 1.5:
            regime = "THEME"

        logger.debug(
            "국면 스코어: bull={} bear={} → {} [{}] | KOSPI {:+.2f}% KOSDAQ {:+.2f}%",
            score_bull, score_bear, regime, ",".join(details) if details else "중립",
            kospi_rate, kosdaq_rate,
        )
        return regime

    def get_status(self) -> dict:
        """현재 상태 (Admin API용)"""
        return {
            "current_regime": self._current_regime,
            "previous_regime": self._previous_regime,
            "last_check_at": self._last_check_at,
            "regime_changed_at": self._regime_changed_at,
            "regime_check_interval_sec": self.interval_sec,
            "scan_interval_sec": self.scan_interval_sec,
            "last_scan_at": self._last_scan_at,
            "kospi": self._last_kospi,
            "kosdaq": self._last_kosdaq,
        }


# 싱글톤
market_regime_agent = MarketRegimeAgent()
