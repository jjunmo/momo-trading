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
"""
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from core.config import settings


class TradingScheduler:
    """KRX 장 시간 기반 자동 운영 스케줄러"""

    def __init__(self):
        self.scheduler = AsyncIOScheduler(timezone="Asia/Seoul")
        self._running = False

    async def start(self) -> None:
        if not settings.SCHEDULER_ENABLED:
            logger.info("스케줄러 비활성화 (SCHEDULER_ENABLED=false)")
            return

        self._setup_jobs()
        self.scheduler.start()
        self._running = True
        logger.info("스케줄러 시작 — 트레이딩 타임라인 활성화")

        # 서버 기동 시 현재 상태에 맞는 초기 작업 실행
        await self._on_startup()

    async def stop(self) -> None:
        if self._running:
            self.scheduler.shutdown(wait=False)
            self._running = False
            logger.info("스케줄러 중지")

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

        # ── 장중 재스캔 (11:00, 13:00 평일) — 새로운 기회 탐색 ──
        self.scheduler.add_job(
            self._intraday_rescan,
            "cron",
            hour="11,13", minute=0,
            day_of_week="mon-fri",
            id="intraday_rescan",
            name="장중 재스캔",
            misfire_grace_time=600,
        )

        # ── 장중 보유종목 점검 (1시간 간격, 09:00~15:00) — WebSocket 보완용 안전망 ──
        self.scheduler.add_job(
            self._holdings_check,
            "cron",
            minute="30",
            hour="9-14",
            day_of_week="mon-fri",
            id="holdings_check",
            name="보유종목 손절/익절 점검",
            misfire_grace_time=300,
        )

        # ── 강제 청산 (15:18 평일) — 데이트레이딩: 보유종목 전량 시장가 매도 ──
        if settings.DAY_TRADING_ONLY:
            self.scheduler.add_job(
                self._force_liquidation,
                "cron",
                hour=settings.FORCE_LIQUIDATION_HOUR,
                minute=settings.FORCE_LIQUIDATION_MINUTE,
                day_of_week="mon-fri",
                id="force_liquidation",
                name="장 마감 전 강제 청산",
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

        if market_calendar.is_krx_trading_hours():
            logger.info("서버 기동: 장중 → 즉시 시장 스캔 + 매매 시작")
            asyncio.create_task(self._market_open_scan())
        else:
            next_open = market_calendar.next_krx_open()
            logger.info("서버 기동: 장외 → 다음 장 시작: {}", next_open.strftime("%m/%d %H:%M"))
            # 장외 기동 시 리뷰가 아직 안 되었으면 실행
            asyncio.create_task(self._post_market_if_needed())

    async def _pre_market(self) -> None:
        """장 시작 전 준비 (08:50) — 어제 리뷰 피드백 확인"""
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            holiday_name = market_calendar.get_holiday_name() or "공휴일"
            logger.info("오늘은 휴장일 ({}) — 장 시작 전 준비 스킵", holiday_name)
            await activity_logger.log(
                "SCHEDULE", "PROGRESS",
                f"\U0001f3d6\ufe0f 오늘은 휴장일 ({holiday_name}) — 매매 스킵",
            )
            return

        logger.info("=== 장 시작 전 준비 (08:50) ===")
        await activity_logger.log(
            "SCHEDULE", "PROGRESS",
            "\u2615 장 시작 전 준비 — 10분 후 KRX 개장",
        )

        # 1. 일일 기준 자산 설정 (데이트레이딩 손익 계산용)
        try:
            from agent.trading_agent import trading_agent
            from trading.account_manager import account_manager

            balance = await account_manager.get_balance()
            trading_agent._daily_start_balance = balance.total_asset
            logger.info("일일 기준 자산 설정: {:,.0f}원", balance.total_asset)
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
                        "SCHEDULE", "PROGRESS",
                        f"\U0001f4cb 어제 리뷰 피드백: {report.lessons_learned[:200]}",
                    )
        except Exception as e:
            logger.debug("어제 리뷰 로드 실패: {}", str(e))

    async def _market_open_scan(self) -> None:
        """장 시작 직후 (09:05) — 전체 시장 스캔 → 종목 선정 → 매매

        AI Agent가 전체 시장 데이터를 받아서 어떤 종목에 투자할지 판단하고,
        선정된 종목을 WebSocket 실시간 구독에 등록하여 이후 이벤트 기반 매매.
        """
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            logger.info("휴장일 — 장 시작 스캔 스킵")
            return

        logger.info("=== 장 시작 첫 스캔 (09:05) — 전체 시장 분석 + 매매 시작 ===")
        await activity_logger.log(
            "SCHEDULE", "PROGRESS",
            "\U0001f514 장 시작! 전체 시장 스캔 → AI 종목 선정 → 분석/매매 시작",
        )

        try:
            # 1. AI Agent 매매 사이클 실행 (전체 시장 스캔 → 분석 → 매매)
            result = await trading_agent.run_cycle()

            # 2. 선정 종목 + 보유종목을 WebSocket 실시간 구독
            selected = result.get("selected_symbols", [])

            # 보유종목 추가
            from trading.account_manager import account_manager
            holdings = await account_manager.get_holdings()
            holding_symbols = [(h.symbol, "KRX") for h in holdings if h.symbol]

            # 합치기 (중복 제거, 최대 41)
            all_symbols = list({s[0]: s for s in selected + holding_symbols}.values())[:41]

            if all_symbols:
                from realtime.stream_manager import stream_manager
                await stream_manager.update_subscriptions(all_symbols)

            await activity_logger.log(
                "SCHEDULE", "PROGRESS",
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

        # 매수 마감 시간 이후면 재스캔 불필요
        if settings.DAY_TRADING_ONLY:
            from datetime import time as _time
            cutoff = _time(settings.BUY_CUTOFF_HOUR, settings.BUY_CUTOFF_MINUTE)
            if now_kst().time() >= cutoff:
                logger.info("매수 마감 시간 경과 → 장중 재스캔 스킵")
                return

        logger.info("=== 장중 재스캔 시작 ({}) ===", now_kst().strftime("%H:%M"))
        await activity_logger.log(
            "SCHEDULE", "PROGRESS",
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
                holding_symbols = [(h.symbol, "KRX") for h in holdings if h.symbol]
                all_symbols = list({s[0]: s for s in selected + holding_symbols}.values())[:41]
                if all_symbols:
                    await stream_manager.update_subscriptions(all_symbols)

            await activity_logger.log(
                "SCHEDULE", "PROGRESS",
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

            holdings = await account_manager.get_holdings()
            if holdings:
                symbols = [(h.symbol, "KRX") for h in holdings if h.symbol]
                await stream_manager.update_subscriptions(symbols)
                logger.info("WebSocket 구독 갱신: {}종목", len(symbols))
        except Exception as e:
            logger.warning("WebSocket 구독 갱신 실패: {}", str(e))

    async def _holdings_check(self) -> None:
        """보유종목 현재가 점검 — WebSocket 보완용 안전망 + 시간 기반 조기 청산

        WebSocket 끊김이나 누락 대비, MCP로 보유종목 현재가를 직접 조회하여
        손절/익절 조건을 체크한다. 데이트레이딩 모드에서는 잔여 시간에 따라
        조기 익절/손절도 실행한다. KRX 장중(09:00~15:30)에만 작동.
        """
        from scheduler.market_calendar import market_calendar
        if not market_calendar.is_krx_trading_hours():
            return

        from services.activity_logger import activity_logger
        from util.time_util import now_kst

        try:
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            holdings = await account_manager.get_holdings()
            if not holdings:
                return

            # 구독 갱신 (WebSocket 연결 복원 대비)
            await self._update_realtime_subscriptions()

            # 강제 청산까지 남은 시간 계산
            now = now_kst()
            close_time = now.replace(
                hour=settings.FORCE_LIQUIDATION_HOUR,
                minute=settings.FORCE_LIQUIDATION_MINUTE,
                second=0, microsecond=0,
            )
            minutes_left = max(0, int((close_time - now).total_seconds() / 60))

            alerts = []
            for h in holdings:
                if h.avg_buy_price <= 0 or h.quantity <= 0:
                    continue
                # MCP로 현재가 직접 조회
                resp = await _mcp.get_current_price(h.symbol)
                if not resp.success or not resp.data:
                    continue
                current = float(resp.data.get("price", 0))
                if current <= 0:
                    continue
                pnl_rate = (current - h.avg_buy_price) / h.avg_buy_price * 100

                should_sell = False
                reason = ""

                # AI가 설정한 임계값이 있으면 우선 사용, 없으면 기본값
                from realtime.event_detector import event_detector
                th = event_detector.get_thresholds(h.symbol)

                stop_loss_pct = -3.0  # 기본값
                take_profit_pct = 5.0
                if th.stop_loss > 0 and h.avg_buy_price > 0:
                    stop_loss_pct = ((th.stop_loss - h.avg_buy_price) / h.avg_buy_price) * 100
                if th.take_profit > 0 and h.avg_buy_price > 0:
                    take_profit_pct = ((th.take_profit - h.avg_buy_price) / h.avg_buy_price) * 100

                # 손절/익절
                if pnl_rate <= stop_loss_pct:
                    should_sell = True
                    reason = f"손절 도달 ({pnl_rate:+.1f}%, 기준 {stop_loss_pct:+.1f}%)"
                elif pnl_rate >= take_profit_pct:
                    should_sell = True
                    reason = f"익절 도달 ({pnl_rate:+.1f}%, 기준 {take_profit_pct:+.1f}%)"
                # 시간 기반 조건 (데이트레이딩 전용)
                elif settings.DAY_TRADING_ONLY:
                    if minutes_left <= 60 and pnl_rate > 1.0:
                        should_sell = True
                        reason = f"잔여 {minutes_left}분 + 수익 {pnl_rate:+.1f}% → 조기 익절"
                    elif minutes_left <= 30 and pnl_rate < -1.0:
                        should_sell = True
                        reason = f"잔여 {minutes_left}분 + 손실 {pnl_rate:+.1f}% → 조기 손절"

                if should_sell and settings.TRADING_ENABLED:
                    try:
                        sell_resp = await _mcp.place_order(
                            symbol=h.symbol,
                            side="SELL",
                            quantity=h.quantity,
                            price=None,
                            market="KRX",
                        )
                        status = "성공" if sell_resp.success else f"실패: {sell_resp.error or ''}"
                        alerts.append(
                            f"\U0001f6a8 {h.name}({h.symbol}): {reason} → 매도 {status}"
                        )
                        if sell_resp.success:
                            from realtime.event_detector import event_detector
                            event_detector.remove_levels(h.symbol)
                    except Exception as e:
                        alerts.append(
                            f"\u274c {h.name}({h.symbol}): {reason} → 매도 오류: {str(e)[:50]}"
                        )
                elif should_sell:
                    # TRADING_ENABLED=false이면 알림만
                    alerts.append(
                        f"\u26a0\ufe0f {h.name}({h.symbol}): {reason} (TRADING_ENABLED=false)"
                    )

            if alerts:
                await activity_logger.log(
                    "HOLDINGS_CHECK", "PROGRESS",
                    f"\U0001f50d 보유종목 점검 (잔여 {minutes_left}분):\n" + "\n".join(alerts),
                )
        except Exception as e:
            logger.warning("보유종목 점검 오류: {}", str(e))

    async def _post_market(self) -> None:
        """장 마감 성과 리뷰 (15:40, KRX 종가 기반)"""
        from agent.trading_agent import trading_agent
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            logger.info("휴장일 — 장 마감 리뷰 스킵")
            return

        logger.info("=== 장 마감 리뷰 시작 (15:40) ===")
        await activity_logger.log(
            "SCHEDULE", "PROGRESS",
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
                    logger.info("오늘 리포트 이미 존재 — 장외 리뷰 스킵")
                    return

            # 거래일이고 15:30 이후면 리뷰 실행
            now = now_kst()
            from datetime import time
            from scheduler.market_calendar import market_calendar
            if market_calendar.is_krx_trading_day(now) and now.time() > time(15, 30):
                logger.info("오늘 리뷰 미완료 — 장외 리뷰 실행")
                from agent.trading_agent import trading_agent
                await trading_agent.run_cycle()
        except Exception as e:
            logger.warning("장외 리뷰 체크 실패: {}", str(e))

    async def _force_liquidation(self) -> None:
        """장 마감 전 강제 청산 — 보유종목 전량 시장가 매도 (데이트레이딩, 병렬)

        종가경매(15:20~15:30) 진입 전에 모든 보유종목을 시장가 매도하여
        오버나이트 포지션을 방지한다. asyncio.gather로 병렬 주문 실행.
        """
        import asyncio
        from scheduler.market_calendar import market_calendar
        from services.activity_logger import activity_logger

        if market_calendar.is_krx_holiday():
            return

        if not settings.TRADING_ENABLED:
            logger.info("매매 비활성 — 강제 청산 스킵")
            return

        logger.warning("=== 장 마감 전 강제 청산 시작 ===")
        await activity_logger.log(
            "SCHEDULE", "PROGRESS",
            "\U0001f6a8 데이트레이딩 강제 청산 — 보유종목 전량 시장가 매도 (병렬)",
        )

        try:
            from trading.account_manager import account_manager
            from trading.mcp_client import mcp_client as _mcp

            holdings = await account_manager.get_holdings()
            if not holdings:
                await activity_logger.log(
                    "SCHEDULE", "PROGRESS",
                    "\u2705 보유종목 없음 — 청산 불필요",
                )
                return

            sellable = [h for h in holdings if h.quantity > 0]
            if not sellable:
                return

            async def _sell_one(h):
                resp = await _mcp.place_order(
                    symbol=h.symbol,
                    side="SELL",
                    quantity=h.quantity,
                    price=None,  # 시장가
                    market="KRX",
                )
                return (resp, h)

            results = await asyncio.gather(
                *[_sell_one(h) for h in sellable],
                return_exceptions=True,
            )

            sold_count = 0
            failed_holdings = []

            for r in results:
                if isinstance(r, Exception):
                    logger.error("강제 청산 주문 오류: {}", str(r))
                    continue

                resp, h = r
                if resp.success:
                    sold_count += 1
                    pnl_text = f"{h.pnl_rate:+.1f}%" if hasattr(h, "pnl_rate") else ""
                    await activity_logger.log(
                        "ORDER", "COMPLETE",
                        f"\U0001f6a8 강제 청산: {h.name}({h.symbol}) "
                        f"{h.quantity}주 시장가 매도 {pnl_text}",
                        symbol=h.symbol,
                    )
                else:
                    failed_holdings.append(h)
                    logger.error(
                        "강제 청산 실패: {}({}) — {}",
                        h.name, h.symbol, resp.error or "알 수 없는 오류",
                    )
                    await activity_logger.log(
                        "ORDER", "ERROR",
                        f"\u274c 강제 청산 실패: {h.name}({h.symbol}) — {resp.error or ''}",
                        symbol=h.symbol,
                    )

            # 실패 종목 2차 재시도 (5초 후)
            if failed_holdings:
                logger.warning("강제 청산 {}건 실패 → 5초 후 재시도", len(failed_holdings))
                await activity_logger.log(
                    "SCHEDULE", "PROGRESS",
                    f"\u26a0\ufe0f 강제 청산 {len(failed_holdings)}건 실패 → 5초 후 재시도",
                )
                await asyncio.sleep(5)
                retry_results = await asyncio.gather(
                    *[_sell_one(h) for h in failed_holdings],
                    return_exceptions=True,
                )
                for r in retry_results:
                    if isinstance(r, Exception):
                        logger.error("강제 청산 재시도 오류: {}", str(r))
                        continue
                    resp, h = r
                    if resp.success:
                        sold_count += 1
                        logger.info("강제 청산 재시도 성공: {}({})", h.name, h.symbol)
                    else:
                        logger.error("강제 청산 재시도 실패: {}({}) — {}", h.name, h.symbol, resp.error or "")

            fail_count = len(failed_holdings)
            summary = f"\U0001f6a8 강제 청산 완료: {sold_count}건 매도"
            if fail_count:
                summary += f", {fail_count}건 실패 (재시도 포함)"
            await activity_logger.log("SCHEDULE", "PROGRESS", summary)

            # 청산 후 이벤트 감시 임계값 제거
            from realtime.event_detector import event_detector
            for h in holdings:
                event_detector.remove_levels(h.symbol)

        except Exception as e:
            logger.error("강제 청산 오류: {}", str(e))
            await activity_logger.log(
                "SCHEDULE", "ERROR",
                f"\u274c 강제 청산 오류: {str(e)[:100]}",
            )

    async def _expire_recommendations(self) -> None:
        """만료된 추천 처리"""
        logger.debug("만료 추천 처리 실행")

    @property
    def is_running(self) -> bool:
        return self._running


trading_scheduler = TradingScheduler()
