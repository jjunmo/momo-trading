"""실현 손익 계산 — 수수료·세금 반영

KIS API가 수수료를 별도 필드로 반환하지 않아 시장별 추정값으로 산정한다.
세율은 사용자 4/24 실거래 데이터로 검증된 값:
- KOSPI 매도세 ≈ 0.199% (대한해운/삼성E&A/두산에너빌리티 실측)
- KIS 비대면 수수료 ≈ 0.00140527%

추정값과 실제 정산금액 차이는 거래당 1~2원 수준이라 실용 목적엔 충분.
"""
from dataclasses import dataclass


KIS_COMMISSION_RATE = 0.0000140527  # 비대면 매수·매도 각각

MARKET_TAX_RATES: dict[str, float] = {
    "KOSPI": 0.0020,    # 거래세 0.05% + 농특세 0.15%
    "KOSDAQ": 0.0018,   # 거래세 0.18% (농특세 면제)
    "KONEX": 0.0010,
    "NXT": 0.0020,      # 미확인 — KOSPI 동일 가정
}


@dataclass
class PnLBreakdown:
    gross_pnl: int      # (exit-entry)*qty (원 단위)
    commission: int     # 매수+매도 수수료 합산 (원 단위 절사)
    tax: int            # 매도 거래세 (원 단위 절사)
    net_pnl: int        # gross - commission - tax (원 단위)
    return_pct: float   # 순수익률 (%, 소수점 2자리)
    is_win: bool


def estimate_commission(price: float, qty: int) -> int:
    """KIS 비대면 수수료 추정 (한 방향 1회분, 원 단위 절사 — KIS 실제 처리 방식)"""
    if price <= 0 or qty <= 0:
        return 0
    return int(price * qty * KIS_COMMISSION_RATE)


def estimate_tax(exit_price: float, qty: int, market: str = "KOSPI") -> int:
    """매도 세금 추정 (시장별 거래세, 원 단위 절사)"""
    if exit_price <= 0 or qty <= 0:
        return 0
    rate = MARKET_TAX_RATES.get(market.upper(), MARKET_TAX_RATES["KOSPI"])
    return int(exit_price * qty * rate)


def round_trip_cost_pct(market: str = "KOSPI") -> float:
    """왕복 거래비용 % (매수수수료 + 매도수수료 + 매도세금)

    KOSPI 기준 약 0.2028% — 매도가가 매수가보다 이만큼 높아야 본전.
    """
    tax = MARKET_TAX_RATES.get(market.upper(), MARKET_TAX_RATES["KOSPI"])
    return round((KIS_COMMISSION_RATE * 2 + tax) * 100, 4)


def breakeven_price(entry_price: float, market: str = "KOSPI") -> float:
    """수수료/세금 차감 후 본전이 되는 매도가"""
    if entry_price <= 0:
        return 0.0
    cost = round_trip_cost_pct(market) / 100
    return round(entry_price * (1 + cost), 2)


def compute_pnl(
    entry_price: float,
    exit_price: float,
    qty: int,
    market: str = "KOSPI",
    commission: int | None = None,
    tax: int | None = None,
) -> PnLBreakdown:
    """순손익 계산 — commission/tax가 None이면 시장별 추정값 사용. 모두 원 단위 정수."""
    gross = int((exit_price - entry_price) * qty)
    if commission is None:
        commission = estimate_commission(entry_price, qty) + estimate_commission(exit_price, qty)
    if tax is None:
        tax = estimate_tax(exit_price, qty, market)
    net = gross - commission - tax
    cost_basis = entry_price * qty
    return_pct = round((net / cost_basis) * 100, 2) if cost_basis > 0 else 0.0
    return PnLBreakdown(
        gross_pnl=gross,
        commission=commission,
        tax=tax,
        net_pnl=net,
        return_pct=return_pct,
        is_win=net > 0,
    )
