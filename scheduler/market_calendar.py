"""KRX + NXT + NASDAQ 거래일/시간 관리 — 공휴일 포함

거래 시간 (KST):
  NXT 프리마켓:   08:00 ~ 08:50  (NXT 단독)
  KRX + NXT:     09:00 ~ 15:20  (동시 거래)
  KRX 종가경매:   15:20 ~ 15:30  (KRX만)
  NXT 애프터마켓:  15:30 ~ 20:00  (NXT 단독)

거래일: 평일 (토/일 + 공휴일/대체공휴일 제외)
"""
from datetime import datetime, time, timedelta

import holidays

from util.time_util import KST, now_kst

# 한국 공휴일 (대체공휴일 포함) — 매년 자동 갱신
_kr_holidays = holidays.KR(years=range(2024, 2030))


class MarketCalendar:
    """시장 거래 시간 관리 (KRX + NXT + 주말/공휴일 처리)"""

    # KRX 정규장
    KRX_OPEN = time(9, 0)
    KRX_CLOSE = time(15, 30)

    # NXT (넥스트레이드) — 프리마켓 + 정규 + 애프터마켓
    NXT_PRE_OPEN = time(8, 0)    # 프리마켓 시작
    NXT_PRE_CLOSE = time(8, 50)  # 프리마켓 종료
    NXT_AFTER_OPEN = time(15, 30)   # 애프터마켓 시작
    NXT_AFTER_CLOSE = time(20, 0)   # 애프터마켓 종료

    # 전체 거래 가능 시간 (NXT 포함)
    MARKET_EARLIEST = time(8, 0)    # 가장 이른 시작
    MARKET_LATEST = time(20, 0)     # 가장 늦은 종료

    # NASDAQ/NYSE (미국, 한국 시간 기준)
    US_OPEN_SUMMER = time(22, 30)
    US_CLOSE_SUMMER = time(5, 0)
    US_OPEN_WINTER = time(23, 30)
    US_CLOSE_WINTER = time(6, 0)

    # ── 휴장일 판별 ──

    @staticmethod
    def is_krx_holiday(dt: datetime | None = None) -> bool:
        """KRX/NXT 휴장일 여부 (주말 + 공휴일)"""
        dt = dt or now_kst()
        if dt.weekday() >= 5:  # 토, 일
            return True
        return dt.date() in _kr_holidays

    @staticmethod
    def is_krx_trading_day(dt: datetime | None = None) -> bool:
        """KRX/NXT 거래일 여부"""
        return not MarketCalendar.is_krx_holiday(dt)

    # ── KRX 정규장 ──

    @staticmethod
    def is_krx_trading_hours(dt: datetime | None = None) -> bool:
        """KRX 정규장 장중 여부 (09:00~15:30)"""
        dt = dt or now_kst()
        if MarketCalendar.is_krx_holiday(dt):
            return False
        current_time = dt.time()
        return MarketCalendar.KRX_OPEN <= current_time <= MarketCalendar.KRX_CLOSE

    # ── NXT 시간대 ──

    @staticmethod
    def is_nxt_pre_market(dt: datetime | None = None) -> bool:
        """NXT 프리마켓 (08:00~08:50)"""
        dt = dt or now_kst()
        if MarketCalendar.is_krx_holiday(dt):
            return False
        current_time = dt.time()
        return MarketCalendar.NXT_PRE_OPEN <= current_time <= MarketCalendar.NXT_PRE_CLOSE

    @staticmethod
    def is_nxt_after_market(dt: datetime | None = None) -> bool:
        """NXT 애프터마켓 (15:30~20:00)"""
        dt = dt or now_kst()
        if MarketCalendar.is_krx_holiday(dt):
            return False
        current_time = dt.time()
        return MarketCalendar.NXT_AFTER_OPEN <= current_time <= MarketCalendar.NXT_AFTER_CLOSE

    @staticmethod
    def is_nxt_trading_hours(dt: datetime | None = None) -> bool:
        """NXT 거래 가능 시간 (프리 + 정규 + 애프터: 08:00~20:00)"""
        dt = dt or now_kst()
        if MarketCalendar.is_krx_holiday(dt):
            return False
        current_time = dt.time()
        return MarketCalendar.MARKET_EARLIEST <= current_time <= MarketCalendar.MARKET_LATEST

    # ── 통합 판별 ──

    @staticmethod
    def is_domestic_trading_hours(dt: datetime | None = None) -> bool:
        """국내 시장 거래 가능 여부 (KRX 또는 NXT 어디든)"""
        return MarketCalendar.is_nxt_trading_hours(dt)  # NXT가 가장 넓음 (08:00~20:00)

    @staticmethod
    def get_market_session(dt: datetime | None = None) -> str:
        """현재 시장 세션 반환

        Returns:
            'NXT_PRE'    — 08:00~08:50 (NXT 프리마켓)
            'KRX_NXT'    — 09:00~15:20 (KRX + NXT 동시)
            'KRX_CLOSE'  — 15:20~15:30 (KRX 종가경매)
            'NXT_AFTER'  — 15:30~20:00 (NXT 애프터마켓)
            'CLOSED'     — 장외
        """
        dt = dt or now_kst()
        if MarketCalendar.is_krx_holiday(dt):
            return "CLOSED"
        t = dt.time()
        if time(8, 0) <= t < time(8, 50):
            return "NXT_PRE"
        if time(9, 0) <= t < time(15, 20):
            return "KRX_NXT"
        if time(15, 20) <= t <= time(15, 30):
            return "KRX_CLOSE"
        if time(15, 30) < t <= time(20, 0):
            return "NXT_AFTER"
        return "CLOSED"

    @staticmethod
    def is_any_market_open(dt: datetime | None = None) -> bool:
        """어떤 시장이라도 열려있는지 (국내 + 미국)"""
        return (MarketCalendar.is_domestic_trading_hours(dt) or
                MarketCalendar.is_us_trading_hours(dt))

    @staticmethod
    def is_us_trading_hours(dt: datetime | None = None) -> bool:
        """미국 장중 여부 (한국 시간 기준, 대략적)"""
        dt = dt or now_kst()
        current_time = dt.time()
        if current_time >= MarketCalendar.US_OPEN_SUMMER or current_time <= MarketCalendar.US_CLOSE_SUMMER:
            return True
        return False

    # ── 다음 개장 ──

    @staticmethod
    def next_krx_open(dt: datetime | None = None) -> datetime:
        """다음 KRX 개장 시각 (주말 + 공휴일 건너뜀)"""
        dt = dt or now_kst()
        if dt.time() < MarketCalendar.KRX_OPEN and MarketCalendar.is_krx_trading_day(dt):
            return dt.replace(hour=9, minute=0, second=0, microsecond=0)
        next_day = dt + timedelta(days=1)
        while MarketCalendar.is_krx_holiday(next_day):
            next_day += timedelta(days=1)
        return next_day.replace(hour=9, minute=0, second=0, microsecond=0)

    @staticmethod
    def next_market_open(dt: datetime | None = None) -> datetime:
        """다음 NXT 프리마켓 시작 (08:00, 주말+공휴일 건너뜀)"""
        dt = dt or now_kst()
        if dt.time() < MarketCalendar.NXT_PRE_OPEN and MarketCalendar.is_krx_trading_day(dt):
            return dt.replace(hour=8, minute=0, second=0, microsecond=0)
        next_day = dt + timedelta(days=1)
        while MarketCalendar.is_krx_holiday(next_day):
            next_day += timedelta(days=1)
        return next_day.replace(hour=8, minute=0, second=0, microsecond=0)

    # ── 공휴일 정보 ──

    @staticmethod
    def get_holiday_name(dt: datetime | None = None) -> str | None:
        """공휴일이면 휴일명 반환, 아니면 None"""
        dt = dt or now_kst()
        return _kr_holidays.get(dt.date())


market_calendar = MarketCalendar()
