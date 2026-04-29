"""한국 주식 호가 단위 (tick size) 정규화

KIS 주문 가격은 가격대별 호가 단위에 맞아야 한다. 안 맞으면 rt_cd=7 "주식주문호가단위 오류" 거부.
"""


def get_tick_size(price: float) -> int:
    """KOSPI/KOSDAQ 호가 단위 (2024년 1월 개편 기준)"""
    if price < 2000:
        return 1
    if price < 5000:
        return 5
    if price < 20000:
        return 10
    if price < 50000:
        return 50
    if price < 200000:
        return 100
    if price < 500000:
        return 500
    return 1000


def round_to_tick(price: float, mode: str = "round") -> int:
    """가격을 호가 단위로 정규화

    Args:
        price: 원가격
        mode:
            "round" — 가까운 호가 (기본)
            "floor" — 내림 (매수 시 보수적)
            "ceil" — 올림 (매수 시 적극적, 체결 우선)
    """
    if price <= 0:
        return 0
    tick = get_tick_size(price)
    if mode == "floor":
        return int(price // tick * tick)
    if mode == "ceil":
        # ceil(price / tick) * tick — 음수 트릭으로 정수 ceil
        return int(-(-int(price * 1000) // (tick * 1000)) * tick) if price % tick else int(price)
    # round (반올림)
    return int(round(price / tick) * tick)
