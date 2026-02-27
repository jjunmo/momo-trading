"""MCP를 통한 잔고/체결내역 조회"""
from loguru import logger

from trading.mcp_client import mcp_client
from trading.models import AccountBalance, HoldingInfo


class AccountManager:
    """계좌 관리 (MCP 통신)

    KIS API inquery-balance 응답 형식:
    - output1: 보유종목 리스트 [{pdno, prdt_name, hldg_qty, pchs_avg_pric, prpr, evlu_pfls_amt, evlu_pfls_rt, ...}]
    - output2: 계좌 총합 [{dnca_tot_amt, scts_evlu_amt, tot_evlu_amt, nass_amt, ...}]
    """

    async def get_balance(self) -> AccountBalance:
        """계좌 잔고 조회"""
        response = await mcp_client.get_account_balance()
        if not response.success:
            logger.error("잔고 조회 실패: {}", response.error)
            return AccountBalance(
                total_asset=0, cash=0, stock_value=0,
                total_pnl=0, total_pnl_rate=0,
                is_valid=False,
            )

        data = response.data or {}
        logger.debug("잔고 MCP 응답: {}", str(data)[:500])

        # KIS API 형식 (output2에 계좌 총합)
        output2 = data.get("output2", [])
        if output2 and isinstance(output2, list):
            summary = output2[0] if output2 else {}
            cash = float(summary.get("dnca_tot_amt", 0))
            stock_value = float(summary.get("scts_evlu_amt", 0))
            total_asset = float(summary.get("tot_evlu_amt", 0)) or (cash + stock_value)
            total_pnl = float(summary.get("evlu_pfls_smtl_amt", 0))
            total_pnl_rate = 0.0
            pchs_amt = float(summary.get("pchs_amt_smtl_amt", 0))
            if pchs_amt > 0:
                total_pnl_rate = (total_pnl / pchs_amt) * 100

            return AccountBalance(
                total_asset=total_asset,
                cash=cash,
                stock_value=stock_value,
                total_pnl=total_pnl,
                total_pnl_rate=total_pnl_rate,
            )

        # 기존 래핑 형식 fallback
        return AccountBalance(
            total_asset=float(data.get("total_asset", 0)),
            cash=float(data.get("cash", 0)),
            stock_value=float(data.get("stock_value", 0)),
            total_pnl=float(data.get("total_pnl", 0)),
            total_pnl_rate=float(data.get("total_pnl_rate", 0)),
        )

    async def get_holdings(self) -> list[HoldingInfo]:
        """보유 종목 목록 조회"""
        response = await mcp_client.get_account_balance()
        if not response.success:
            logger.error("보유종목 조회 실패: {}", response.error)
            return []

        data = response.data or {}
        holdings = []

        # KIS API 형식 (output1에 보유종목)
        output1 = data.get("output1", [])
        if output1 and isinstance(output1, list):
            for item in output1:
                qty = int(item.get("hldg_qty", 0))
                if qty <= 0:
                    continue
                holdings.append(HoldingInfo(
                    symbol=item.get("pdno", ""),
                    name=item.get("prdt_name", ""),
                    quantity=qty,
                    avg_buy_price=float(item.get("pchs_avg_pric", 0)),
                    current_price=float(item.get("prpr", 0)),
                    pnl=float(item.get("evlu_pfls_amt", 0)),
                    pnl_rate=float(item.get("evlu_pfls_rt", 0)),
                ))
            return holdings

        # 기존 래핑 형식 fallback
        for item in data.get("holdings", []):
            holdings.append(HoldingInfo(
                symbol=item.get("symbol", ""),
                name=item.get("name", ""),
                quantity=int(item.get("quantity", 0)),
                avg_buy_price=float(item.get("avg_buy_price", 0)),
                current_price=float(item.get("current_price", 0)),
                pnl=float(item.get("pnl", 0)),
                pnl_rate=float(item.get("pnl_rate", 0)),
            ))
        return holdings

    async def get_available_cash(self) -> float:
        """투자 가용 현금 조회"""
        balance = await self.get_balance()
        return balance.cash


account_manager = AccountManager()
