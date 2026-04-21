"""매매불가 종목 블록리스트 — 서버 재시작 시에도 유지"""
from uuid import uuid4

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from models.base import Base, TimestampMixin


class UntradeableSymbol(Base, TimestampMixin):
    """매매불가 종목 (NXT 미상장 ETF, 매매불가 종목 등)"""
    __tablename__ = "untradeable_symbols"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid4()))
    symbol: Mapped[str] = mapped_column(String(20), unique=True, index=True, nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200), nullable=True)
