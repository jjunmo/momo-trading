"""과거 일봉/분봉 데이터 로드 (DB + MCP)"""
from datetime import date

import pandas as pd
from loguru import logger

from trading.mcp_client import mcp_client


class BacktestDataLoader:
    """백테스팅용 과거 데이터 로더"""

    @staticmethod
    async def load_from_mcp(
        symbol: str, start_date: date, end_date: date,
        market: str = "KRX", period: str = "D",
    ) -> pd.DataFrame:
        """MCP를 통해 KIS에서 과거 일봉 데이터 로드"""
        total_days = (end_date - start_date).days
        all_data = []

        # MCP는 한 번에 최대 100봉 정도 반환하므로 분할 요청
        current_end = end_date
        while total_days > 0:
            count = min(100, total_days)
            resp = await mcp_client.get_daily_price(symbol, period=period, count=count, market=market)
            if resp.success and resp.data:
                items = resp.data.get("prices", resp.data.get("items", []))
                all_data.extend(items)
                total_days -= count
                if items:
                    # 다음 배치를 위해 가장 오래된 날짜 이전으로
                    oldest = items[-1].get("date", items[-1].get("trade_date", ""))
                    if oldest:
                        from datetime import datetime
                        current_end = datetime.strptime(oldest, "%Y%m%d").date()
                        total_days = (current_end - start_date).days
                    else:
                        break
                else:
                    break
            else:
                logger.warning("MCP 데이터 로드 실패: {}", resp.error)
                break

        if not all_data:
            return pd.DataFrame()

        df = pd.DataFrame(all_data)
        # 컬럼 정규화
        col_map = {
            "stck_bsop_date": "date", "trade_date": "date",
            "stck_oprc": "open", "stck_hgpr": "high",
            "stck_lwpr": "low", "stck_clpr": "close",
            "acml_vol": "volume",
        }
        df = df.rename(columns={k: v for k, v in col_map.items() if k in df.columns})

        for col in ["open", "high", "low", "close"]:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" in df.columns:
            df["volume"] = pd.to_numeric(df["volume"], errors="coerce")

        if "date" in df.columns:
            df["date"] = pd.to_datetime(df["date"])
            df = df.sort_values("date").reset_index(drop=True)

        return df

    @staticmethod
    def load_from_dataframe(df: pd.DataFrame) -> pd.DataFrame:
        """이미 로드된 DataFrame을 백테스팅용으로 정규화"""
        required = ["open", "high", "low", "close", "volume"]
        for col in required:
            if col not in df.columns:
                raise ValueError(f"필수 컬럼 누락: {col}")
        return df.copy()

    @staticmethod
    def load_from_db(daily_data: list) -> pd.DataFrame:
        """MarketDataDaily ORM 객체 리스트를 DataFrame으로 변환"""
        if not daily_data:
            return pd.DataFrame()

        records = [{
            "date": d.trade_date,
            "open": d.open,
            "high": d.high,
            "low": d.low,
            "close": d.close,
            "volume": d.volume,
        } for d in daily_data]

        df = pd.DataFrame(records)
        df = df.sort_values("date").reset_index(drop=True)
        return df
