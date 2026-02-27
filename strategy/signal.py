"""Signal 데이터 클래스"""
from dataclasses import dataclass, field

from trading.enums import SignalAction, SignalUrgency


@dataclass
class TradeSignal:
    """매매 시그널"""
    symbol: str
    stock_id: str
    action: SignalAction
    strength: float  # 0~1
    suggested_price: float | None = None
    suggested_quantity: int | None = None
    target_price: float | None = None
    stop_loss_price: float | None = None
    urgency: SignalUrgency = SignalUrgency.WAIT
    strategy_type: str = ""
    reason: str = ""
    confidence: float = 0.0
    metadata: dict = field(default_factory=dict)
