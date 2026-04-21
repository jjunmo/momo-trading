"""트레이딩 에이전트 스케줄러 — KRX 데이트레이딩 자동 운영

타임라인 (KST):
  08:50  장 시작 전 준비 — 어제 리뷰 피드백 확인
  09:00  KRX 개장
  09:05  장 시작 스캔 → 종목 선정 → 실시간 모니터링 돌입
  09:00~14:30  WebSocket 실시간 이벤트 → AI 분석/매매 (이벤트 기반)
              + 1시간 간격 보유종목 안전 점검 (시간 기반 조기 청산 포함)
  11:00/13:00  장중 재스캔 — 새로운 기회 탐색
  14:30  신규 매수 마감 (청산 시간 확보)
  15:10  보유종목 전량 시장가 강제 청산 (종가경매 전, 병렬 실행)
  15:30  KRX 폐장
  15:40  장 마감 성과 리뷰 (KRX 종가 기반, 피드백 학습)
  16:00  포트폴리오 정산 (KIS ↔ DB 동기화)
  16:30  일봉 데이터 보관용 수집

※ DAY_TRADING_ONLY=true: 당일 매수→당일 청산 필수 (오버나이트 없음)
※ DAY_TRADING_ONLY=false: 스윙 모드 — 유망 종목 오버나이트 보유 (스마트 청산)
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from core.config import settings
from trading.enums import ActivityPhase, ActivityType


class TradingScheduler:
    """KRX 장 시간 기반 자동 운영 스케줄러"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self._running = False

    async def start(self) -> None:
        if not settings.SCHEDULER_ENABLED:
            logger.debug("스케줄러 비활성화 (SCHEDULER_ENABLED=false)")
            return

        self._setup_jobs()
        self.scheduler.start()
        self._running = True
        logger.info("스케줄러 시작 — 트레이딩 타임라인 활성화")

        # MarketRegimeAgent 시작 — 국면 변화 시 보유종목 긴급 재평가 + 동적 스캔
        from agent.market_regime_agent import market_regime_agent
        market_regime_agent.set_regime_change_callback(self._on_regime_change)
        market_regime_agent.set_scan_trigger_callback(self._dynamic_rescan)
        await market_regime_agent.start()

        # 서버 기동 시 현재 상태에 맞는 초기 작업 실행
        await self._on_startup()

    async def stop(self) -> None:
        if self._running:
            from agent.market_regime_agent import market_regime_agent
            await market_regime_agent.stop()
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("스케줄러 중지")

    async def _on_regime_change(self, new_regime: str, old_regime: str) -> None:
        """국면 변화 시 보유종목 긴급 재평가 트리거"""
        logger.info("국면 변화 감지 ({} → {}) → 보유종목 긴급 재평가 실행", old_regime, new_regime)
        try:
            from services.activity_logger import activity_logger
            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.START,
                f"🔄 국면 변화 ({old_regime} → {new_regime}) → 보유종목 긴급 재평가",
            )
            await self._intraday_holdings_review()
        except Exception as e:
            logger.error("국면 변화 긴급 재평가 오류: {}", str(e))

    async def _dynamic_rescan(self) -> None:
        """MarketRegimeAgent가 동적 주기로 트리거하는 시장 재스캔"""
        from agent.market_regime_agent import market_regime_agent

        regime = market_regime_agent.current_regime
        interval_min = market_regime_agent.scan_interval_sec // 60
        logger.info("동적 재스캔 실행 (국면: {}, 주기: {}분)", regime or "미설정", interval_min)

        try:
            await self._intraday_rescan()
        except Exception as e:
            logger.error("동적 재스캔 오류: {}", str(e))

    def _setup_jobs(self) -> None:
        from scheduler.jobs.portfolio_sync_job import portfolio_sync_job
        from scheduler.jobs.market_data_job import market_data_job

        # ── 장 시작 전 준비 (08:50 평일) — KRX 개장 10분 전 ──
        self.scheduler.add_job(
            self._pre_market,
            "cron",
            hour=8, minute=50,
            day_of_week="mon-fri",
            id="pre_market",
            name="장 시작 전 준비",
            misfire_grace_time=600,
        )

        # ── 장 시작 스캔 (09:05 평일) — 전체 시장 스캔 → 종목 선정 → 매매 시작 ──
        self.scheduler.add_job(
            self._market_open_scan,
            "cron",
            hour=9, minute=5,
            day_of_week="mon-fri",
            id="market_open_scan",
            name="장 시작 스캔 + 매매",
            misfire_grace_time=600,
        )

        # ── 장중 재스캔: MarketRegimeAgent가 국면별 동적 주기로 트리거 ──
        # 고정 cron(11:00, 13:00) 대신 국면별 동적 스캔:
        #   BULL: 30분, THEME: 20분, SIDEWAYS: 60분, BEAR: 90분
        # + 국면 변화 시 즉시 재스캔

        # ── 보유종목 점검 (15분 간격, 08:00~19:00) — WebSocket 구독 갱신 + 체결 대기 확인 ──
        self.scheduler.add_job(
            self._holdings_check,
            "cron",
            minute="0,15,30,45",
            hour="8-19",
            day_of_week="mon-fri",
            id="holdings_check",
            name="보유종목 점검 + 구독 갱신",
            misfire_grace_time=300,
        )

        # ── 장중 보유종목 AI 재평가 (30분 간격, 09:00~14:00) — 맥락 기반 HOLD/SELL + 임계값 조정 ──
        self.scheduler.add_job(
            self._intraday_holdings_review,
            "cron",
            minute="0,30",
            hour="9-14",
            day_of_week="mon-fri",
            id="intraday_holdings_review",
            name="장중 보유종목 AI 재평가",
            misfire_grace_time=600,
        )

        # ── 장 마감 전 청산 (15:10 평일) — DAY_TRADING: 전량 매도 / 스윙: 스마트 청산 ──
        self.scheduler.add_job(
            self._force_liquidation,
            "cron",
            hour=settings.FORCE_LIQUIDATION_HOUR,
            minute=settings.FORCE_LIQUIDATION_MINUTE,
            day_of_week="mon-fri",
            id="force_liquidation",
            name="장 마감 전 청산",
            misfire_grace_time=300,
        )

        # ── 장 마감 리뷰 (15:40 평일) — KRX 종가 기반 성과 리뷰 ──
        self.scheduler.add_job(
            self._post_market,
            "cron",
            hour=15, minute=40,
            day_of_week="mon-fri",
            id="post_market",
            name="장 마감 성과 리뷰",
            misfire_grace_time=3600,
        )

        # ── NXT 프리마켓 스캔 (08:05 평일) — NXT 프리마켓 시작 후 스캔+매매 ──
        self.scheduler.add_job(
            self._nxt_pre_market_cycle,
            "cron",
            hour=8, minute=5,
            day_of_week="mon-fri",
            id="nxt_pre_market_cycle",
            name="NXT 프리마켓 스캔",
            misfire_grace_time=300,
        )

        # ── NXT 애프터마켓 전환 (15:35 평일) — KRX→NXT 구독 전환 + 스캔 ──
        self.scheduler.add_job(
            self._nxt_after_transition,
            "cron",
            hour=15, minute=35,
            day_of_week="mon-fri",
            id="nxt_after_transition",
            name="NXT 애프터마켓 전환",
            misfire_grace_time=300,
        )

        # ── NXT 애프터마켓 재스캔 (17:00, 19:00 평일) ──
        self.scheduler.add_job(
            self._nxt_after_rescan,
            "cron",
            hour="17,19", minute=0,
            day_of_week="mon-fri",
            id="nxt_after_rescan",
            name="NXT 애프터마켓 재스캔",
            misfire_grace_time=600,
        )

        # ── NXT 마감 전 정리 (19:45 평일) — DAY_CLOSE 포지션 정리 ──
        self.scheduler.add_job(
            self._nxt_close,
            "cron",
            hour=19, minute=45,
            day_of_week="mon-fri",
            id="nxt_close",
            name="NXT 마감 전 정리",
            misfire_grace_time=300,
        )

        # ── 포트폴리오 정산 (16:00) ──
        self.scheduler.add_job(
            portfolio_sync_job,
            "cron",
            hour=16, minute=0,
            id="portfolio_sync",
            name="포트폴리오 정산",
            misfire_grace_time=3600,
        )

        # ── 일봉 데이터 수집 (16:30) ──
        self.scheduler.add_job(
            market_data_job,
            "cron",
            hour=16, minute=30,
            id="market_data",
            name="일봉 데이터 수집",
            misfire_grace_time=3600,
        )

        # ── 만료 추천 정리 (1시간 간격) ──
        self.scheduler.add_job(
            self._expire_recommendations,
            "interval",
            hours=1,
            id="expire_recommendations",
            name="만료 추천 처리",
        )

    # ─────────── 스케줄 작업 구현 ───────────

    async def _on_startup(self) -> None:
        """서버 기동 시 현재 시간대에 맞는 초기 작업 실행"""
        import asyncio
        from scheduler.market_calendar import market_calendar

        # 기동 직후 약간의 딜레이 (MCP 연결 안정화)
        await asyncio.sleep(3)

        if market_calendar.is_domestic_trading_hours():
            session = market_calendar.get_market_session()
            logger.debug("서버 기동: 장중({}) → 즉시 시장 스캔 + 매매 시작", session)
            asyncio.create_task(self._market_open_scan())
        else:
            next_open = market_calendar.next_market_open()
            logger.debug("서버 기동: 장외 → 다음 장 시작: {}", next_open.strftime("%m/%d %H:%M"))
            # 장외 기동 시 리뷰가 아직 안 되었으면 실행
            asyncio.create_task(self._post_market_if_needed())

    async def _pre_market(self) -> None:
        """장 시작 전 준비 (08:50) — 어제 리뷰 피드백 확인"""
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            holiday_name = market_calendar.get_holiday_name() or "공휴일"
            logger.debug("오늘은 휴장일 ({}) — 장 시작 전 준비 스킵", holiday_name)
            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
                f"\U0001f3d6\ufe0f 오늘은 휴장일 ({holiday_name}) — 매매 스킵",
            )
            return

        logger.debug("=== 장 시작 전 준비 (08:50) ===")
        await activity_logger.log(
            ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
            "\u2615 장 시작 전 준비 — 10분 후 KRX 개장",
        )

        # 1. 일일 기준 자산 설정 (데이트레이딩 손익 계산용)
        try:
            from agent.trading_agent import trading_agent
            from trading.account_manager import account_manager

            balance = await account_manager.get_balance()
            trading_agent._daily_start_balance = balance.total_asset
            logger.debug("일일 기준 자산 설정: {:,.0f}원", balance.total_asset)
        except Exception as e:
            logger.warning("기준 자산 설정 실패: {}", str(e))

        # 2. 어제 리뷰 피드백 확인 (AI 학습용)
        try:
            from datetime import timedelta
            from util.time_util import now_kst
            from core.database import AsyncSessionLocal
            from repositories.daily_report_repository import DailyReportRepository

            yesterday = (now_kst() - timedelta(days=1)).date()
            async with AsyncSessionLocal() as session:
                repo = DailyReportRepository(session)
                report = await repo.get_by_date(yesterday)
                if report and report.lessons_learned:
                    await activity_logger.log(
                        ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
                        f"\U0001f4cb 어제 리뷰 피드백: {report.lessons_learned[:200]}",
                    )
        except Exception as e:
            logger.debug("어제 리뷰 로드 실패: {}", str(e))

        # 3. 오버나이트 포지션 점검
        await self._check_overnight_positions()

        # 4. 활성 트레이딩 규칙 로드 + 적용 (일일 리뷰 피드백 자동 학습)
        try:
            from analysis.feedback.trading_rules import trading_rule_engine
            from agent.trading_agent import trading_agent
            from strategy.risk_manager import risk_manager

            active_rules = await trading_rule_engine.load_active_rules()
            rules = active_rules.get("rules", [])

            if rules:
                trading_rule_engine.apply_to_strategies(
                    trading_agent.strategies, active_rules,
                )
                trading_rule_engine.apply_to_risk_manager(
                    risk_manager, active_rules,
                )
                trading_agent._active_trading_rules = active_rules

                rule_summary = ", ".join(
                    f"{r.param_name}={r.param_value}" for r in rules[:5]
                )
                await activity_logger.log(
                    ActivityType.TRADING_RULE, ActivityPhase.COMPLETE,
                    f"📋 트레이딩 규칙 {len(rules)}건 적용: {rule_summary}",
                )
                await trading_rule_engine.record_application(
                    [r.id for r in rules]
                )

            expired = await trading_rule_engine.expire_old_rules()
            if expired:
                logger.debug("만료된 트레이딩 규칙 {}건 비활성화", expired)
        except Exception as e:
            logger.warning("트레이딩 규칙 로드 실패: {}", str(e))

    async def _market_open_scan(self) -> None:
        """장 시작 직후 (09:05) — 전체 시장 스캔 → 종목 선정 → 매매

        AI Agent가 전체 시장 데이터를 받아서 어떤 종목에 투자할지 판단하고,
        선정된 종목을 WebSocket 실시간 구독에 등록하여 이후 이벤트 기반 매매.
        """
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            logger.debug("휴장일 — 장 시작 스캔 스킵")
            return

        logger.debug("=== 장 시작 첫 스캔 (09:05) — 전체 시장 분석 + 매매 시작 ===")
        await activity_logger.log(
            ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
            "\U0001f514 장 시작! 전체 시장 스캔 → AI 종목 선정 → 분석/매매 시작",
        )

        try:
            # 0. 오버나이트 포지션 갭 체크
            await self._check_overnight_gap()

            # 1. AI Agent 매매 사이클 실행 (전체 시장 스캔 → 분석 → 매매)
            result = await trading_agent.run_cycle()

            # 2. 선정 종목 + 보유종목을 WebSocket 실시간 구독
            selected = result.get("selected_symbols", [])

            # 보유종목 추가
            from trading.account_manager import account_manager
            holdings = await account_manager.get_holdings()
            holding_symbols = [(h.symbol, market_calendar.get_active_market()) for h in holdings if h.symbol]

            # 합치기 (중복 제거, 최대 41)
            all_symbols = list({s[0]: s for s in selected + holding_symbols}.values())[:41]

            if all_symbols:
                from realtime.stream_manager import stream_manager
                await stream_manager.update_subscriptions(all_symbols)

            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
                f"\u2705 장 시작 완료 — 분석 {result.get('analyzed', 0)}건, "
                f"매매 {result.get('executed', 0)}건, "
                f"실시간 감시 {len(all_symbols)}종목 → 모니터링 돌입",
            )
        except Exception as e:
            logger.error("장 시작 스캔 오류: {}", str(e))

    async def _intraday_rescan(self) -> None:
        """장중 재스캔 (11:00, 13:00) — 새로운 기회 탐색

        기존 run_cycle()을 재사용하여 시장 재스캔 → 분석 → 매매.
        cycle_lock이 잡혀있으면 자동 스킵.
        """
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger
        from util.time_util import now_kst

        if market_calendar.is_krx_holiday():
            return

        # 세션 마감 임박 시 재스캔 불필요
        if now_kst().time() >= market_calendar.get_trading_cutoff():
            logger.debug("세션 마감 임박 → 재스캔 스킵")
            return

        logger.debug("=== 장중 재스캔 시작 ({}) ===", now_kst().strftime("%H:%M"))
        await activity_logger.log(
            ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
            f"\U0001f504 장중 재스캔 시작 ({now_kst().strftime('%H:%M')}) — 새로운 기회 탐색",
        )

        try:
            result = await trading_agent.run_cycle()

            # 선정 종목 WebSocket 구독 갱신
            selected = result.get("selected_symbols", [])
            if selected:
                from trading.account_manager import account_manager
                from realtime.stream_manager import stream_manager
                holdings = await account_manager.get_holdings()
                holding_symbols = [(h.symbol, market_calendar.get_active_market()) for h in holdings if h.symbol]
                all_symbols = list({s[0]: s for s in selected + holding_symbols}.values())[:41]
                if all_symbols:
                    await stream_manager.update_subscriptions(all_symbols)

            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
                f"\u2705 장중 재스캔 완료 — 분석 {result.get('analyzed', 0)}건, "
                f"매매 {result.get('executed', 0)}건",
            )
        except Exception as e:
            logger.error("장중 재스캔 오류: {}", str(e))

    async def _update_realtime_subscriptions(self) -> None:
        """보유종목 WebSocket 구독 갱신 (임계값은 AI가 설정)"""
        try:
            from trading.account_manager import account_manager
            from realtime.stream_manager import stream_manager
            from scheduler.market_calendar import market_calendar

            holdings = await account_manager.get_holdings()
            if holdings:
                active_market = market_calendar.get_active_market()
                symbols = [(h.symbol, active_market) for h in holdings if h.symbol]
                await stream_manager.update_subscriptions(symbols)
                logger.debug("WebSocket 구독 갱신: {}종목", len(symbols))
        except Exception as e:
            logger.warning("WebSocket 구독 갱신 실패: {}", str(e))

    async def _holdings_check(self) -> None:
        """매도 체결 대기 확인 + WebSocket 구독 갱신

        손절/익절은 PriceGuard가 실시간 처리. 스케줄러는 체결 대기만 점검.
        """
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_domestic_trading_hours():
            return

        try:
            from agent.decision_maker import decision_maker
            confirmed = await decision_maker.check_pending_sells()
            if confirmed:
                logger.info("매도 체결 확인: {}건 (정기 점검)", confirmed)

            await self._update_realtime_subscriptions()
        except Exception as e:
            logger.warning("보유종목 점검 오류: {}", str(e))

    async def _post_market(self) -> None:
        """장 마감 성과 리뷰 (15:40, KRX 종가 기반)"""
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            logger.debug("휴장일 — 장 마감 리뷰 스킵")
            return

        logger.debug("=== 장 마감 리뷰 시작 (15:40) ===")
        await activity_logger.log(
            ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
            "\U0001f319 장 마감 — 오늘 매매 성과 리뷰 시작",
        )

        try:
            await trading_agent.run_cycle()  # 장외이므로 자동으로 _run_after_hours_cycle 실행
        except Exception as e:
            logger.error("장 마감 리뷰 오류: {}", str(e))

    async def _post_market_if_needed(self) -> None:
        """장외 기동 시 오늘 리뷰가 아직 안 되었으면 실행"""
        try:
            from util.time_util import now_kst
            from core.database import AsyncSessionLocal
            from repositories.daily_report_repository import DailyReportRepository

            today = now_kst().date()
            async with AsyncSessionLocal() as session:
                repo = DailyReportRepository(session)
                existing = await repo.get_by_date(today)
                if existing:
                    logger.debug("오늘 리포트 이미 존재 — 장외 리뷰 스킵")
                    return

            # 거래일이고 전체 시장 마감 후(NXT 포함 20:00 이후)면 리뷰 실행
            now = now_kst()
            from datetime import time
            from scheduler.market_calendar import market_calendar
            if market_calendar.is_krx_trading_day(now) and not market_calendar.is_domestic_trading_hours(now):
                logger.debug("오늘 리뷰 미완료 — 장외 리뷰 실행")
                from agent.trading_agent import trading_agent
                await trading_agent.run_cycle()
        except Exception as e:
            logger.warning("장외 리뷰 체크 실패: {}", str(e))

    async def _force_liquidation(self) -> None:
        """장 마감 전 청산 — AI가 종목별로 판단

        각 종목의 분석 결과(hold_strategy)에 따라:
        OVERNIGHT → 오버나이트 보유
        DAY_CLOSE → 당일 청산 (SellAgent 매도)
        """
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            return
        if not settings.TRADING_ENABLED:
            return

        try:
            from agent.sell_agent import SellParams, sell_agent
            from agent.stock_analysis_agent import StockAnalysisRequest, stock_analysis_agent
            from realtime.event_detector import event_detector
            from trading.account_manager import account_manager

            holdings = await account_manager.get_holdings()
            sellable = [h for h in holdings if h.quantity > 0]
            if not sellable:
                await activity_logger.log(
                    ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
                    "\u2705 보유종목 없음 — 청산 불필요",
                )
                return

            sold = 0
            held = 0

            for h in sellable:
                try:
                    # 최근 분석 결과 확인
                    cached = stock_analysis_agent.get_result(h.symbol)

                    # 캐시 없으면 재분석
                    if not cached or not cached.success:
                        th = event_detector.get_thresholds(h.symbol)
                        request = StockAnalysisRequest(
                            symbol=h.symbol, name=h.name or h.symbol,
                            is_holding=True, purpose="PERIODIC_REVIEW",
                            avg_price=h.avg_buy_price, pnl_rate=h.pnl_rate,
                            quantity=h.quantity,
                            active_stop_loss=th.stop_loss,
                            active_take_profit=th.take_profit,
                            active_trailing_stop_pct=th.trailing_stop_pct,
                        )
                        cached = await stock_analysis_agent.analyze(request, force=True)

                    # hold_strategy에 따라 처리
                    if cached.success and cached.hold_strategy == "OVERNIGHT":
                        held += 1
                        logger.info("[청산] {} → OVERNIGHT 보유 유지", h.symbol)
                    else:
                        # DAY_CLOSE 또는 분석 실패 → 매도
                        await sell_agent.execute_sell(SellParams(
                            symbol=h.symbol,
                            exit_reason="DAY_CLOSE" if cached.success else "FORCE_LIQUIDATION",
                        ))
                        sold += 1

                except Exception as e:
                    logger.warning("청산 판단 실패 ({}) → 매도: {}", h.symbol, str(e))
                    await sell_agent.execute_sell(SellParams(symbol=h.symbol, exit_reason="FORCE_LIQUIDATION"))
                    sold += 1

            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
                f"\U0001f6a8 장 마감 청산: 매도 {sold}건, OVERNIGHT {held}건",
            )

        except Exception as e:
            logger.error("청산 오류: {}", str(e))
            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.ERROR,
                f"\u274c 청산 오류: {str(e)[:100]}",
            )

    async def _collect_holdings_data(
        self, sellable: list,
    ) -> tuple[list[dict], dict, list]:
        """보유종목 데이터 수집 — LLM 프롬프트용 공통 헬퍼

        Returns:
            (holdings_data, holdings_map, fallback_sell)
            - holdings_data: LLM 프롬프트에 넣을 종목별 데이터 리스트
            - holdings_map: symbol → (holding, trade_result, current_price)
            - fallback_sell: 데이터 수집 실패로 바로 SELL 처리할 종목 리스트
        """
        from core.database import AsyncSessionLocal
        from realtime.event_detector import event_detector
        from repositories.trade_result_repository import TradeResultRepository
        from strategy.holding_policy import _calc_hold_days, _get_max_hold_days
        from trading.mcp_client import mcp_client as _mcp

        holdings_data: list[dict] = []
        holdings_map: dict = {}
        fallback_sell: list = []

        async with AsyncSessionLocal() as session:
            repo = TradeResultRepository(session)

            import asyncio

            import pandas as pd

            from analysis.chart_analyzer import chart_analyzer

            for h in sellable:
                try:
                    # 현재가 + 일봉 + 분봉 병렬 조회
                    price_resp, daily_resp, minute_resp = await asyncio.gather(
                        _mcp.get_current_price(h.symbol),
                        _mcp.get_daily_price(h.symbol, count=60),
                        _mcp.get_minute_price(h.symbol, period="5"),
                    )

                    current_price = 0.0
                    if price_resp.success and price_resp.data:
                        current_price = float(price_resp.data.get("price", 0))

                    if current_price <= 0:
                        fallback_sell.append(h)
                        logger.warning("현재가 조회 실패 {} → SELL", h.symbol)
                        continue

                    trade_result = await repo.get_open_buy(h.symbol)

                    if trade_result is None:
                        fallback_sell.append(h)
                        logger.warning("TradeResult 없음 {} → SELL", h.symbol)
                        continue

                    avg_price = h.avg_buy_price
                    pnl_rate = (current_price - avg_price) / avg_price * 100 if avg_price > 0 else 0.0
                    hold_days = _calc_hold_days(trade_result)
                    max_hold_days = _get_max_hold_days(trade_result.strategy_type, settings)

                    # 차트 분석 (기술적 지표 + 패턴 + 추세)
                    chart_text = ""
                    daily_df = pd.DataFrame()
                    minute_df = None
                    if daily_resp.success and daily_resp.data:
                        daily_items = daily_resp.data.get("prices", daily_resp.data.get("items", []))
                        if daily_items:
                            daily_df = pd.DataFrame(daily_items)
                            for col in ["open", "high", "low", "close"]:
                                if col in daily_df.columns:
                                    daily_df[col] = pd.to_numeric(daily_df[col], errors="coerce")
                            if "volume" in daily_df.columns:
                                daily_df["volume"] = pd.to_numeric(daily_df["volume"], errors="coerce")
                    if minute_resp.success and minute_resp.data:
                        minute_items = minute_resp.data.get("prices", [])
                        if minute_items:
                            minute_df = pd.DataFrame(minute_items)
                            for col in ["open", "high", "low", "close"]:
                                if col in minute_df.columns:
                                    minute_df[col] = pd.to_numeric(minute_df[col], errors="coerce")
                            if "volume" in minute_df.columns:
                                minute_df["volume"] = pd.to_numeric(minute_df["volume"], errors="coerce")

                    if not daily_df.empty:
                        chart_result = chart_analyzer.analyze(daily_df, minute_df)
                        chart_text = chart_result.prompt_text or ""

                    # 현재 event_detector 활성 임계값
                    th = event_detector.get_thresholds(h.symbol)

                    data = {
                        "symbol": h.symbol,
                        "stock_name": h.name or trade_result.stock_name or h.symbol,
                        "avg_price": avg_price,
                        "current_price": current_price,
                        "pnl_rate": pnl_rate,
                        "quantity": h.quantity,
                        "hold_days": hold_days,
                        "max_hold_days": max_hold_days,
                        "confidence": trade_result.ai_confidence or 0.0,
                        "target_price": trade_result.ai_target_price,
                        "stop_loss_price": trade_result.ai_stop_loss_price,
                        "strategy_type": trade_result.strategy_type or "N/A",
                        "active_stop_loss": th.stop_loss,
                        "active_take_profit": th.take_profit,
                        "active_trailing_stop_pct": th.trailing_stop_pct,
                        "chart_analysis": chart_text,
                    }
                    holdings_data.append(data)
                    holdings_map[h.symbol] = (h, trade_result, current_price)

                except Exception as e:
                    fallback_sell.append(h)
                    logger.warning("보유종목 데이터 수집 오류 {} → SELL: {}", h.symbol, str(e))

        return holdings_data, holdings_map, fallback_sell

    async def _smart_liquidation(self, sellable: list) -> tuple[list, list]:
        """스윙 모드: LLM Tier1 기반 종목별 HOLD/SELL 판정

        전 종목 데이터를 LLM에 일괄 전달하여 포트폴리오 맥락을 고려한 판정.
        LLM 실패 시 코드 룰(holding_policy) 폴백.

        Returns:
            (to_sell, to_hold) 두 리스트
        """
        import time

        from services.activity_logger import activity_logger
        from strategy.holding_policy import evaluate_overnight_hold

        to_sell = []
        to_hold = []

        # ── 1) 전 종목 데이터 수집 (공통 헬퍼) ──
        holdings_data, holdings_map, fallback_sell = await self._collect_holdings_data(sellable)
        to_sell.extend(fallback_sell)

        if not holdings_data:
            return to_sell, to_hold

        # ── 2) LLM Tier1 단일 호출 (전 종목 일괄 판정) ──
        llm_decisions = {}  # symbol → {"action": ..., "reason": ..., "confidence": ...}
        llm_provider = ""
        llm_elapsed_ms = 0

        try:
            from analysis.llm.llm_factory import llm_factory
            from analysis.llm.prompts.overnight_hold import (
                OVERNIGHT_HOLD_SYSTEM,
                build_overnight_prompt,
            )
            from core.json_utils import parse_llm_json

            # 시장 국면 가져오기
            from agent.trading_agent import trading_agent
            market_regime = trading_agent._market_regime or ""

            prompt = build_overnight_prompt(holdings_data, market_regime)

            start = time.time()
            result_text, llm_provider = await llm_factory.generate_tier1(
                prompt, system_prompt=OVERNIGHT_HOLD_SYSTEM,
            )
            llm_elapsed_ms = int((time.time() - start) * 1000)

            parsed = parse_llm_json(result_text)
            if parsed and "decisions" in parsed:
                for d in parsed["decisions"]:
                    symbol = d.get("symbol", "")
                    if symbol and symbol in holdings_map:
                        llm_decisions[symbol] = {
                            "action": d.get("action", "SELL").upper(),
                            "reason": d.get("reason", ""),
                            "confidence": d.get("confidence", 0.0),
                        }

            logger.info(
                "스마트 청산 LLM 판정 완료: {}건 / {} ({}ms)",
                len(llm_decisions), llm_provider, llm_elapsed_ms,
            )
        except Exception as e:
            logger.warning("스마트 청산 LLM 호출 실패 → 코드 룰 폴백: {}", str(e))

        # ── 3) 판정 결과 분류 + 누락 종목 폴백 ──
        log_lines = []

        for data in holdings_data:
            symbol = data["symbol"]
            h, trade_result, current_price = holdings_map[symbol]
            stock_name = data["stock_name"]

            if symbol in llm_decisions:
                decision = llm_decisions[symbol]
                action = decision["action"]
                reason = decision["reason"]
                conf = decision["confidence"]

                if action == "HOLD":
                    to_hold.append(h)
                    log_lines.append(
                        f"  - {stock_name}({symbol}): HOLD — {reason} "
                        f"(AI 신뢰도: {conf:.2f})"
                    )
                else:
                    to_sell.append(h)
                    log_lines.append(
                        f"  - {stock_name}({symbol}): SELL — {reason} "
                        f"(AI 신뢰도: {conf:.2f})"
                    )
                logger.info("스마트 청산 {}: {} — {}", action, symbol, reason)
            else:
                # LLM 응답에서 누락 → 코드 룰 폴백
                fallback = evaluate_overnight_hold(h, trade_result, current_price, settings)
                if fallback.action == "HOLD":
                    to_hold.append(h)
                else:
                    to_sell.append(h)
                log_lines.append(
                    f"  - {stock_name}({symbol}): {fallback.action} — "
                    f"{fallback.reason} (폴백)"
                )
                logger.info(
                    "스마트 청산 폴백 {}: {} — {}",
                    fallback.action, symbol, fallback.reason,
                )

        # ── 4) 활동 로그 ──
        provider_text = f"\nLLM: {llm_provider} ({llm_elapsed_ms}ms)" if llm_provider else "\n(코드 룰 폴백)"
        await activity_logger.log(
            ActivityType.SCHEDULE, ActivityPhase.PROGRESS,
            f"📊 스마트 청산 AI 판정:\n" + "\n".join(log_lines) + provider_text,
        )

        return to_sell, to_hold

    async def _intraday_holdings_review(self) -> None:
        """장중 보유종목 AI 재평가 (30분 간격) — Agent에 위임"""
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_domestic_trading_hours():
            return

        from services.activity_logger import activity_logger

        try:
            from agent.buy_agent import BuyParams, buy_agent
            from agent.sell_agent import SellParams, sell_agent
            from agent.stock_analysis_agent import StockAnalysisRequest, stock_analysis_agent
            from realtime.event_detector import event_detector
            from trading.account_manager import account_manager

            holdings = await account_manager.get_holdings()
            sellable = [h for h in holdings if h.quantity > 0]
            if not sellable:
                return

            log_lines = []
            for h in sellable:
                try:
                    th = event_detector.get_thresholds(h.symbol)
                    request = StockAnalysisRequest(
                        symbol=h.symbol,
                        name=h.name or h.symbol,
                        is_holding=True,
                        purpose="PERIODIC_REVIEW",
                        avg_price=h.avg_buy_price,
                        pnl_rate=h.pnl_rate,
                        quantity=h.quantity,
                        active_stop_loss=th.stop_loss,
                        active_take_profit=th.take_profit,
                        active_trailing_stop_pct=th.trailing_stop_pct,
                    )
                    result = await stock_analysis_agent.analyze(request, force=True)
                    if not result.success:
                        log_lines.append(f"  - {h.name}({h.symbol}): 분석 실패")
                        continue

                    # 임계값은 StockAnalysisAgent가 분석 시 직접 설정
                    rec = result.recommendation
                    if rec == "BUY":
                        await buy_agent.execute(BuyParams(
                            symbol=h.symbol, name=h.name or h.symbol,
                            strategy_type="STABLE_SHORT", price=result.current_price,
                            confidence=result.confidence, reason=result.reason,
                            stop_loss_price=result.stop_loss_price,
                            take_profit_price=result.target_price,
                            trailing_stop_pct=result.trailing_stop_pct,
                            breakeven_trigger_pct=result.breakeven_trigger_pct,
                            review_threshold_pct=result.review_threshold_pct,
                        ))
                        log_lines.append(f"  - {h.name}({h.symbol}): ADD_BUY")
                    elif rec == "SELL":
                        await sell_agent.execute_sell(SellParams(symbol=h.symbol, exit_reason="HOLDINGS_REVIEW"))
                        log_lines.append(f"  - {h.name}({h.symbol}): SELL")
                    else:
                        log_lines.append(f"  - {h.name}({h.symbol}): HOLD")

                except Exception as e:
                    log_lines.append(f"  - {h.symbol}: 오류 {str(e)[:50]}")

            if log_lines:
                await activity_logger.log(
                    ActivityType.HOLDINGS_CHECK, ActivityPhase.PROGRESS,
                    f"🔄 장중 보유 재평가:\n" + "\n".join(log_lines),
                )

        except Exception as e:
            logger.warning("장중 보유 재평가 오류: {}", str(e))

    async def _check_overnight_positions(self) -> None:
        """오버나이트 포지션 프리마켓 점검 (08:50)

        서버 재시작 대비 event_detector 임계값 재설정 + 보유일 경고.
        """
        from services.activity_logger import activity_logger

        try:
            from core.database import AsyncSessionLocal
            from realtime.event_detector import event_detector
            from repositories.trade_result_repository import TradeResultRepository

            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open()

                if not open_positions:
                    return

                # 실제 KIS 보유종목과 교차 검증 → 고아 레코드 정리
                actual_symbols = set()
                try:
                    from trading.account_manager import account_manager
                    actual_holdings = await account_manager.get_holdings()
                    actual_symbols = {h.symbol for h in actual_holdings if h.quantity > 0}
                except Exception:
                    actual_symbols = {tr.stock_symbol for tr in open_positions}

                orphan_count = 0
                # 고아 레코드에 대해 실제 매도 가격 추정 시도
                # SELL 레코드나 현재가로 exit_price/pnl 계산
                for tr in open_positions:
                    if tr.stock_symbol not in actual_symbols:
                        from util.time_util import now_kst
                        now = now_kst()
                        tr.exit_at = now
                        tr.exit_reason = "ORPHAN_CLEANUP"

                        # exit_price 추정: 현재가 또는 마지막 SELL 레코드
                        exit_price = 0.0
                        try:
                            from trading.mcp_client import mcp_client
                            resp = await mcp_client.get_current_price(tr.stock_symbol)
                            if resp.success and resp.data:
                                exit_price = float(resp.data.get("price", 0))
                        except Exception:
                            pass

                        if exit_price > 0 and tr.entry_price > 0:
                            tr.exit_price = exit_price
                            tr.pnl = (exit_price - tr.entry_price) * tr.quantity
                            tr.return_pct = round(
                                (exit_price - tr.entry_price) / tr.entry_price * 100, 2
                            )
                            tr.is_win = tr.pnl > 0
                            from util.time_util import ensure_kst
                            tr.hold_days = (now - ensure_kst(tr.entry_at)).days if tr.entry_at else 0

                        orphan_count += 1

                if orphan_count:
                    await session.commit()
                    logger.warning("프리마켓 고아 TradeResult {}건 정리 (손익 계산 포함)", orphan_count)
                    # 고아 제거 후 다시 조회
                    open_positions = [tr for tr in open_positions if tr.exit_at is None]

            if not open_positions:
                return

            restored = 0
            warnings = []
            for tr in open_positions:
                # event_detector 임계값 재설정 (트레일링 스탑 포함)
                kwargs = {}
                if tr.ai_stop_loss_price and tr.ai_stop_loss_price > 0:
                    kwargs["stop_loss"] = tr.ai_stop_loss_price
                    kwargs["initial_stop_loss"] = tr.ai_stop_loss_price
                if tr.ai_target_price and tr.ai_target_price > 0:
                    kwargs["take_profit"] = tr.ai_target_price
                    kwargs["initial_take_profit"] = tr.ai_target_price
                # 트레일링 스탑 복원 (전략별 기본값)
                if tr.entry_price and tr.entry_price > 0:
                    kwargs["entry_price"] = tr.entry_price
                if tr.strategy_type:
                    kwargs["strategy_type"] = tr.strategy_type
                    # 전략별 기본 trailing_stop_pct
                    from agent.trading_agent import trading_agent
                    strategy = trading_agent.strategies.get(tr.strategy_type)
                    default_trailing = getattr(strategy, "DEFAULT_TRAILING_STOP_PCT", 3.0)
                    kwargs["trailing_stop_pct"] = default_trailing
                    kwargs["breakeven_trigger_pct"] = 1.5
                if kwargs:
                    event_detector.set_thresholds(tr.stock_symbol, **kwargs)
                    restored += 1

                # 최대 보유일 경고
                from strategy.holding_policy import _calc_hold_days, _get_max_hold_days
                hold_days = _calc_hold_days(tr)
                max_days = _get_max_hold_days(tr.strategy_type, settings)
                if hold_days >= max_days:
                    warnings.append(
                        f"{tr.stock_name}({tr.stock_symbol}): 보유 {hold_days}일 ≥ 최대 {max_days}일"
                    )

            msg = f"\U0001f30d 오버나이트 포지션 {len(open_positions)}건 점검"
            if restored:
                msg += f" | 임계값 복원 {restored}건"
            if warnings:
                msg += f" | ⚠️ 초과보유: {', '.join(warnings)}"

            logger.info(msg)
            await activity_logger.log(
                ActivityType.SCHEDULE, ActivityPhase.PROGRESS, msg,
            )
        except Exception as e:
            logger.warning("오버나이트 포지션 점검 오류: {}", str(e))

    async def _check_overnight_gap(self) -> None:
        """장 시작 갭 체크 (09:05) — 오버나이트 포지션 손절/익절 즉시 처리"""
        from services.activity_logger import activity_logger

        try:
            from core.database import AsyncSessionLocal
            from repositories.trade_result_repository import TradeResultRepository
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            holdings = await account_manager.get_holdings()
            if not holdings:
                return

            async with AsyncSessionLocal() as session:
                repo = TradeResultRepository(session)
                open_positions = await repo.get_all_open()

            # symbol → TradeResult 매핑
            open_map = {tr.stock_symbol: tr for tr in open_positions}

            alerts = []
            for h in holdings:
                if h.quantity <= 0:
                    continue
                tr = open_map.get(h.symbol)
                if not tr:
                    continue  # 당일 매수 등 — 갭 체크 불필요

                resp = await _mcp.get_current_price(h.symbol)
                if not resp.success or not resp.data:
                    continue
                current = float(resp.data.get("price", 0))
                if current <= 0:
                    continue

                should_sell = False
                reason = ""

                # 갭 하락 → 손절가 이하
                if tr.ai_stop_loss_price and current <= tr.ai_stop_loss_price:
                    should_sell = True
                    reason = f"갭 하락 손절 (현재 {current:,.0f} ≤ 손절 {tr.ai_stop_loss_price:,.0f})"

                # 갭 상승 → 익절가 이상
                elif tr.ai_target_price and current >= tr.ai_target_price:
                    should_sell = True
                    reason = f"갭 상승 익절 (현재 {current:,.0f} ≥ 목표 {tr.ai_target_price:,.0f})"

                if should_sell and settings.TRADING_ENABLED:
                    # P0-2: 이중 매도 방지
                    from agent.trading_agent import trading_agent
                    if not await trading_agent._acquire_sell(h.symbol):
                        alerts.append(f"\u26a0\ufe0f {h.name}({h.symbol}): {reason} → 이미 매도 진행 중")
                        continue
                    try:
                        sell_resp = await _mcp.place_order(
                            symbol=h.symbol, side="SELL",
                            quantity=h.quantity, price=None,
                            market=market_calendar.get_excg_dvsn_cd(),
                        )
                        status = "성공" if sell_resp.success else f"실패: {sell_resp.error or ''}"
                        alerts.append(f"\U0001f6a8 {h.name}({h.symbol}): {reason} → 매도 {status}")
                        if sell_resp.success:
                            from realtime.event_detector import event_detector
                            event_detector.remove_levels(h.symbol)
                            # 체결 확인 + TradeResult 기록
                            from agent.decision_maker import decision_maker
                            order_data = sell_resp.data or {}
                            order_id = order_data.get("order_id", "")
                            await decision_maker.confirm_and_record(
                                symbol=h.symbol, side="SELL",
                                order_id=order_id, quantity=h.quantity,
                                expected_price=current,
                                exit_reason="GAP_CHECK",
                            )
                    finally:
                        trading_agent._release_sell(h.symbol)
                elif should_sell:
                    alerts.append(f"\u26a0\ufe0f {h.name}({h.symbol}): {reason} (TRADING_ENABLED=false)")

            if alerts:
                msg = "\U0001f30d 오버나이트 갭 체크:\n" + "\n".join(alerts)
                logger.info(msg)
                await activity_logger.log(
                    ActivityType.SCHEDULE, ActivityPhase.PROGRESS, msg,
                )
        except Exception as e:
            logger.warning("오버나이트 갭 체크 오류: {}", str(e))

    async def _trigger_rescan_after_sell(self) -> None:
        """매도 완료 후 재스캔 (현금 충분 + 장중 + 매수 마감 전)"""
        import asyncio
        from datetime import time as _time

        await asyncio.sleep(5)  # 체결 확인 대기

        try:
            if not settings.TRADING_ENABLED:
                return

            from scheduler.market_calendar import market_calendar
            if not market_calendar.is_domestic_trading_hours():
                return

            # 세션 마감 임박 시 재스캔 스킵
            from util.time_util import now_kst
            if now_kst().time() >= market_calendar.get_trading_cutoff():
                logger.debug("세션 마감 임박 → 재스캔 스킵")
                return

            from trading.account_manager import account_manager
            balance = await account_manager.get_balance()
            min_order_amount = settings.MIN_BUY_QUANTITY * 1000  # 대략적 최소 주문 금액
            if balance.cash < min_order_amount:
                logger.debug("현금 부족 ({:,.0f}원) → 재스캔 스킵", balance.cash)
                return

            logger.debug("매도 후 재스캔 트리거 — 현금 {:,.0f}원", balance.cash)
            from agent.trading_agent import trading_agent
            await trading_agent.run_cycle()
        except Exception as e:
            logger.warning("매도 후 재스캔 실패: {}", str(e))

    async def _expire_recommendations(self) -> None:
        """만료된 추천 처리"""
        logger.debug("만료 추천 처리 실행")

    # ── NXT 잡 구현 ──

    async def _nxt_pre_market_cycle(self) -> None:
        """NXT 프리마켓 스캔+매매 (08:05)"""
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_nxt_pre_market():
            return
        if not settings.TRADING_ENABLED:
            return
        logger.info("=== NXT 프리마켓 사이클 시작 ===")
        try:
            from agent.trading_agent import trading_agent
            await trading_agent.run_cycle()
        except Exception as e:
            logger.error("NXT 프리마켓 사이클 오류: {}", str(e))

    async def _nxt_after_transition(self) -> None:
        """NXT 애프터마켓 스캔 (15:35) — H0UNCNT0 통합 구독이므로 전환 불필요"""
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_nxt_after_market():
            return
        if not settings.TRADING_ENABLED:
            return
        logger.info("=== NXT 애프터마켓 사이클 시작 ===")
        try:
            from agent.trading_agent import trading_agent
            await trading_agent.run_cycle()
        except Exception as e:
            logger.error("NXT 애프터 사이클 오류: {}", str(e))

    async def _nxt_after_rescan(self) -> None:
        """NXT 애프터마켓 재스캔 (17:00, 19:00)"""
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_nxt_after_market():
            return
        if not settings.TRADING_ENABLED:
            return
        logger.info("NXT 애프터 재스캔 시작")
        try:
            from agent.trading_agent import trading_agent
            await trading_agent.run_cycle()
        except Exception as e:
            logger.error("NXT 애프터 재스캔 오류: {}", str(e))

    async def _nxt_close(self) -> None:
        """NXT 마감 전 포지션 정리 (19:45)"""
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_nxt_after_market():
            return
        if not settings.TRADING_ENABLED:
            return
        logger.info("=== NXT 마감 전 포지션 정리 ===")
        try:
            from agent.sell_agent import SellParams, sell_agent
            from agent.stock_analysis_agent import StockAnalysisRequest, stock_analysis_agent
            from trading.account_manager import account_manager

            holdings = await account_manager.get_holdings()
            for h in [h for h in holdings if h.quantity > 0]:
                # AI 분석으로 마감 전 매도 판단
                request = StockAnalysisRequest(
                    symbol=h.symbol, name=h.name,
                    is_holding=True, purpose="PERIODIC_REVIEW",
                )
                result = await stock_analysis_agent.analyze(request, force=True)
                if result and result.recommendation == "SELL":
                    await sell_agent.execute_sell(SellParams(
                        symbol=h.symbol, exit_reason="NXT_CLOSE",
                    ))
        except Exception as e:
            logger.error("NXT 마감 정리 오류: {}", str(e))

    @property
    def is_running(self) -> bool:
        return self._running


trading_scheduler = TradingScheduler()
