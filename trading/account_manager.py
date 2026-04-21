"""MCP를 통한 잔고/체결내역 조회 — 장외 시간 폴링 차단 + 캐시"""
from loguru import logger

from scheduler.market_calendar import market_calendar
from trading.mcp_client import mcp_client
from trading.models import AccountBalance, HoldingInfo, PendingOrderInfo


class AccountManager:
    """계좌 관리 (MCP 통신)

    KIS API inquery-balance 응답 형식:
    - output1: 보유종목 리스트 [{pdno, prdt_name, hldg_qty, pchs_avg_pric, prpr, evlu_pfls_amt, evlu_pfls_rt, ...}]
    - output2: 계좌 총합 [{dnca_tot_amt, scts_evlu_amt, tot_evlu_amt, nass_amt, ...}]
    """

    def __init__(self):
        self._balance_cache: AccountBalance | None = None
        self._holdings_cache: list[HoldingInfo] | None = None
        self._pending_orders_cache: list[PendingOrderInfo] | None = None

    def invalidate_cache(self) -> None:
        """캐시 무효화 (체결 후 즉시 최신 데이터 조회 강제)"""
        self._balance_cache = None
        self._holdings_cache = None
        self._pending_orders_cache = None

    def _empty_balance(self) -> AccountBalance:
        return AccountBalance(
            total_asset=0, cash=0, stock_value=0,
            total_pnl=0, total_pnl_rate=0,
            is_valid=False,
        )

    def _parse_balance(self, data: dict, holdings: list["HoldingInfo"] | None = None) -> AccountBalance:
        """MCP 응답에서 AccountBalance 파싱

        holdings가 전달되면 stock_value를 보유종목 평가합계로 계산하여
        화면 표시값과 일치시킵니다.
        """
        output2 = data.get("output2", [])
        if output2 and isinstance(output2, list):
            summary = output2[0] if output2 else {}
            kis_total = float(summary.get("tot_evlu_amt", 0))

            # 주식 평가: 보유종목 기반 계산 (화면 일관성), fallback으로 KIS값
            if holdings:
                stock_value = sum(
                    h.current_price * h.quantity for h in holdings
                )
            else:
                stock_value = float(summary.get("scts_evlu_amt", 0))

            # 총자산: KIS 공식 총평가금액 사용
            total_asset = kis_total if kis_total > 0 else stock_value

            # 현금: 총자산에서 주식평가 차감 (dnca_tot_amt는 총자산과 동일하여 사용 불가)
            cash = total_asset - stock_value

            # 디버그 로깅: KIS 원본 필드값 기록
            kis_scts = float(summary.get("scts_evlu_amt", 0))
            logger.debug(
                "KIS 잔고 원본: tot_evlu={} scts_evlu={:,.0f} "
                "dnca={:,.0f} nass={} pchs={} | 보유종목 평가합={:,.0f}",
                summary.get("tot_evlu_amt", "N/A"), kis_scts, cash,
                summary.get("nass_amt", "N/A"),
                summary.get("pchs_amt_smtl_amt", "N/A"),
                stock_value,
            )

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

    def _parse_holdings(self, data: dict) -> list[HoldingInfo]:
        """MCP 응답에서 HoldingInfo 리스트 파싱"""
        holdings = []

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

    async def get_account_snapshot(self) -> tuple[AccountBalance, list[HoldingInfo]]:
        """잔고 + 보유종목 조회

        KRX 장중: MCP inquery-balance (AFHR_FLPR_YN=N)
        NXT 장중: KIS REST API 직접 호출 (AFHR_FLPR_YN=X → NXT 실시간 가격 반영)
        장외 + 캐시 있음: 캐시 반환
        """
        # 장외 + 캐시 있음 → 바로 반환
        if not market_calendar.is_domestic_trading_hours():
            if self._balance_cache and self._holdings_cache is not None:
                logger.debug("장외 시간 → 계좌 스냅샷 캐시 반환")
                return self._balance_cache, self._holdings_cache

        # KIS REST API 직접 호출 (AFHR_FLPR_YN=X: KRX+NXT 통합 가격 반영)
        # MCP inquery-balance는 AFHR_FLPR_YN 미지원 → 항상 직접 호출
        data = await self._fetch_balance_direct("X")

        if data is None:
            balance = self._balance_cache if self._balance_cache else self._empty_balance()
            holdings = self._holdings_cache if self._holdings_cache is not None else []
            return balance, holdings

        holdings = self._parse_holdings(data)
        balance = self._parse_balance(data, holdings=holdings)

        self._balance_cache = balance
        self._holdings_cache = holdings
        return balance, holdings

    async def _fetch_balance_direct(self, afhr_flpr_yn: str = "X") -> dict | None:
        """KIS REST API 직접 잔고 조회 (NXT 시간용, AFHR_FLPR_YN=X)"""
        from trading.kis_api import get_balance_direct
        result = await get_balance_direct(afhr_flpr_yn=afhr_flpr_yn)
        if not result.get("success"):
            logger.warning("계좌 직접 조회 실패: {}", result.get("error"))
            return None
        return result

    async def get_balance(self) -> AccountBalance:
        """계좌 잔고 조회 (하위 호환)"""
        balance, _ = await self.get_account_snapshot()
        return balance

    async def get_holdings(self) -> list[HoldingInfo]:
        """보유 종목 목록 조회 (하위 호환)"""
        _, holdings = await self.get_account_snapshot()
        return holdings

    def _parse_pending_orders(self, data: dict) -> list[PendingOrderInfo]:
        """MCP 응답에서 미체결 주문 리스트 파싱"""
        orders = []
        output = data.get("output", [])
        if not output or not isinstance(output, list):
            return orders

        for item in output:
            rmn_qty = int(item.get("rmn_qty", 0))
            if rmn_qty <= 0:
                continue
            # sll_buy_dvsn_cd: 01=매도, 02=매수
            side_code = item.get("sll_buy_dvsn_cd", "")
            side = "매수" if side_code == "02" else "매도"
            orders.append(PendingOrderInfo(
                order_id=item.get("odno", ""),
                symbol=item.get("pdno", ""),
                name=item.get("prdt_name", ""),
                side=side,
                order_qty=int(item.get("ord_qty", 0)),
                filled_qty=int(item.get("tot_ccld_qty", 0)),
                remaining_qty=rmn_qty,
                order_price=float(item.get("ord_unpr", 0)),
                order_time=item.get("ord_tmd", ""),
            ))
        return orders

    async def get_pending_orders(self) -> list[PendingOrderInfo]:
        """미체결 주문 목록 조회

        장중: 매번 MCP 호출 → 캐시 갱신
        장외 + 캐시 있음: 캐시 반환
        장외 + 캐시 없음: MCP 1회 호출 → 캐시 저장
        """
        if not market_calendar.is_domestic_trading_hours():
            if self._pending_orders_cache is not None:
                logger.debug("장외 시간 → 미체결 주문 캐시 반환")
                return self._pending_orders_cache

        response = await mcp_client.get_order_list()
        if not response.success:
            logger.warning("미체결 주문 조회 실패: {}", response.error)
            return self._pending_orders_cache if self._pending_orders_cache is not None else []

        data = response.data or {}
        orders = self._parse_pending_orders(data)
        self._pending_orders_cache = orders
        return orders

    async def get_available_cash(self) -> float:
        """투자 가용 현금 조회"""
        balance = await self.get_balance()
        return balance.cash


account_manager = AccountManager()
