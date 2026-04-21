"""KIS REST API 직접 호출 클라이언트

MCP를 거치지 않고 KIS API를 직접 호출하여
분봉, 거래량순위, 등락률순위 등 MCP 미지원 데이터를 조회한다.
"""
import asyncio
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from loguru import logger

from core.config import settings

DOMAIN = "https://openapi.koreainvestment.com:9443"
VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"
TOKEN_FILE = Path("data/kis_token.json")

# 토큰 동시 발급 방지용 Lock + 메모리 캐시
_token_lock = asyncio.Lock()
_cached_token: str | None = None
_cached_expires_at: datetime | None = None


def _get_domain() -> str:
    """계좌 유형에 따른 도메인 반환 (조회 API는 실전 도메인 공통)"""
    return DOMAIN


def _get_app_key() -> str:
    if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL":
        return settings.KIS_PAPER_APP_KEY or settings.KIS_APP_KEY
    return settings.KIS_APP_KEY


def _get_app_secret() -> str:
    if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL":
        return settings.KIS_PAPER_APP_SECRET or settings.KIS_APP_SECRET
    return settings.KIS_APP_SECRET


async def _get_access_token(client: httpx.AsyncClient) -> str:
    """토큰 발급 (메모리 캐시 + Lock으로 동시 발급 방지)

    asyncio.gather()로 병렬 호출 시 Lock으로 직렬화하여
    KIS 1분당 1회 토큰 발급 제한(EGW00133) 에러를 방지한다.
    EGW00133 시 Lock을 해제한 뒤 60초 대기 → 재시도 (Lock 점유 최소화).
    """
    global _cached_token, _cached_expires_at

    # 빠른 경로: 메모리 캐시에 유효한 토큰이 있으면 즉시 반환 (Lock 불필요)
    if _cached_token and _cached_expires_at and datetime.now() < _cached_expires_at:
        return _cached_token

    for attempt in range(2):
        async with _token_lock:
            # Double-check: 다른 태스크가 Lock 대기 중 이미 발급했을 수 있음
            if _cached_token and _cached_expires_at and datetime.now() < _cached_expires_at:
                return _cached_token

            # 파일 캐시 확인
            if TOKEN_FILE.exists():
                try:
                    token_data = json.loads(TOKEN_FILE.read_text())
                    expires_at = datetime.fromisoformat(token_data["expires_at"])
                    if datetime.now() < expires_at:
                        _cached_token = token_data["token"]
                        _cached_expires_at = expires_at
                        logger.debug("KIS 토큰: 파일 캐시에서 로드")
                        return _cached_token
                except Exception:
                    pass

            # 새 토큰 발급
            token = await _issue_new_token(client)
            if token is not None:
                return token

        # EGW00133 발급 제한 → Lock 해제 후 대기 (다른 API 호출 차단 안 함)
        if attempt == 0:
            logger.warning("KIS 토큰 발급 제한(EGW00133), 60초 대기 후 재시도")
            await asyncio.sleep(60)

    raise Exception("KIS 토큰 발급 실패: 재시도 횟수 초과")


async def _issue_new_token(client: httpx.AsyncClient) -> str | None:
    """실제 토큰 발급 요청 (_token_lock 내부에서 호출)

    성공 시 토큰 반환, EGW00133 시 None 반환 (호출자가 Lock 해제 후 재시도).
    """
    global _cached_token, _cached_expires_at

    resp = await client.post(
        f"{DOMAIN}/oauth2/tokenP",
        headers={"content-type": "application/json"},
        json={
            "grant_type": "client_credentials",
            "appkey": _get_app_key(),
            "appsecret": _get_app_secret(),
        },
    )

    if resp.status_code != 200:
        resp_text = resp.text
        if "EGW00133" in resp_text:
            return None  # 호출자가 Lock 해제 후 대기·재시도
        raise Exception(f"KIS 토큰 발급 실패: {resp_text}")

    data = resp.json()
    token = data["access_token"]
    expires_at = datetime.now() + timedelta(hours=23)

    # 메모리 캐시 갱신
    _cached_token = token
    _cached_expires_at = expires_at

    # 파일 캐시 저장
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token,
        "expires_at": expires_at.isoformat(),
    }))

    logger.debug("KIS 토큰 신규 발급 완료")
    return token


async def get_minute_chart(symbol: str, period: str = "5") -> dict:
    """국내 주식 분봉 차트 조회

    Args:
        symbol: 종목코드 (예: 005930)
        period: 분봉 간격 - "1", "5", "15", "30", "60"

    Returns:
        {"success": True, "prices": [{"time", "open", "high", "low", "close", "volume"}, ...]}
    """
    end_time = datetime.now().strftime("%H%M%S")

    try:
        async with httpx.AsyncClient() as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHKST03010200",
                },
                params={
                    "FID_ETC_CLS_CODE": "",
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_HOUR_1": end_time,
                    "FID_PW_DATA_INCU_YN": "N",
                },
            )

            if response.status_code != 200:
                logger.warning("분봉 조회 실패: HTTP {}", response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "prices": []}

            result = response.json()

        if not result or "output2" not in result:
            return {"success": False, "error": "분봉 데이터 없음", "prices": []}

        prices = []
        for item in result.get("output2", []):
            prices.append({
                "time": item.get("stck_cntg_hour", ""),
                "open": item.get("stck_oprc", "0"),
                "high": item.get("stck_hgpr", "0"),
                "low": item.get("stck_lwpr", "0"),
                "close": item.get("stck_prpr", "0"),
                "volume": item.get("cntg_vol", "0"),
            })

        return {"success": True, "symbol": symbol, "period": period, "prices": prices}
    except Exception as e:
        logger.error("분봉 조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "error": str(e), "prices": []}


async def get_stock_daily_chart(symbol: str, count: int = 15) -> dict:
    """주식 일봉 차트 조회 (시장 국면 판단용 MA 계산 기초 데이터)

    Args:
        symbol: 종목코드 (예: 069500=KODEX200, 229200=KODEX코스닥150)
        count: 조회 일수 (기본 15일, 3일/10일 SMA에 충분)

    Returns:
        {"success": True, "prices": [{"date", "close", "volume", "open", "high", "low"}, ...]}
        최신→과거 순서 (prices[0]이 최근)
    """
    today = datetime.now().strftime("%Y%m%d")

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHKST03010100",
                },
                params={
                    "FID_COND_MRKT_DIV_CODE": "J",
                    "FID_INPUT_ISCD": symbol,
                    "FID_INPUT_DATE_1": (datetime.now() - timedelta(days=count * 2)).strftime("%Y%m%d"),
                    "FID_INPUT_DATE_2": today,
                    "FID_PERIOD_DIV_CODE": "D",
                    "FID_ORG_ADJ_PRC": "0",
                },
            )

            if response.status_code != 200:
                logger.warning("일봉 조회 실패 ({}): HTTP {}", symbol, response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "prices": []}

            result = response.json()

        output = result.get("output2", [])
        if not output:
            return {"success": False, "error": "일봉 데이터 없음", "prices": []}

        prices = []
        for item in output[:count]:
            prices.append({
                "date": item.get("stck_bsop_date", ""),
                "open": item.get("stck_oprc", "0"),
                "high": item.get("stck_hgpr", "0"),
                "low": item.get("stck_lwpr", "0"),
                "close": item.get("stck_clpr", "0"),
                "volume": item.get("acml_vol", "0"),
            })

        logger.debug("일봉 조회 완료 ({}): {}일", symbol, len(prices))
        return {"success": True, "symbol": symbol, "prices": prices}
    except Exception as e:
        logger.error("일봉 조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "error": str(e), "prices": []}


async def get_volume_rank(market: str = "J") -> dict:
    """거래량순위 조회 (KIS REST API 직접 호출)

    Args:
        market: 시장 구분 - "J"(KRX), "NX"(NXT), "UN"(통합)

    Returns:
        {"success": True, "stocks": [{"symbol", "name", "price", "change", "change_rate", "volume", ...}, ...]}
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/volume-rank",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHPST01710000",
                },
                params={
                    "FID_COND_MRKT_DIV_CODE": market,
                    "FID_COND_SCR_DIV_CODE": "20171",
                    "FID_INPUT_ISCD": "0000",           # 전체 종목
                    "FID_DIV_CLS_CODE": "1",             # 보통주만 (우선주 제외)
                    "FID_BLNG_CLS_CODE": "0",            # 평균거래량 기준
                    "FID_TRGT_CLS_CODE": "111111111",    # 전체 대상
                    "FID_TRGT_EXLS_CLS_CODE": "0000000110",  # 관리종목·감리종목 제외
                    "FID_INPUT_PRICE_1": "100",          # 100원 이상 (동전주/폐지위험 차단)
                    "FID_INPUT_PRICE_2": "",
                    "FID_VOL_CNT": "",                   # 거래량 필터 없음 (LLM 판단)
                    "FID_INPUT_DATE_1": "",
                },
            )

            if response.status_code != 200:
                logger.warning("거래량순위 조회 실패: HTTP {}", response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "stocks": []}

            result = response.json()

        output = result.get("output", [])
        if not output:
            return {"success": False, "error": "거래량순위 데이터 없음", "stocks": []}

        stocks = []
        for item in output[:30]:  # 최대 30개
            stocks.append({
                "symbol": item.get("mksc_shrn_iscd", item.get("stck_shrn_iscd", "")),
                "name": item.get("hts_kor_isnm", ""),
                "price": item.get("stck_prpr", "0"),
                "current_price": item.get("stck_prpr", "0"),
                "change": item.get("prdy_vrss", "0"),
                "change_rate": item.get("prdy_ctrt", "0"),
                "volume": item.get("acml_vol", "0"),
                "trade_amount": item.get("acml_tr_pbmn", "0"),
                "prev_volume": item.get("prdy_vol", "0"),
                "volume_increase_rate": item.get("vol_inrt", "0"),
                "change_sign": item.get("prdy_vrss_sign", ""),
            })

        logger.debug("거래량순위 조회 완료: {}건", len(stocks))
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error("거래량순위 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "stocks": []}


async def get_market_index(index_code: str = "0001") -> dict:
    """업종 현재지수 조회 (KIS REST API 직접 호출)

    Args:
        index_code: 업종 코드 - "0001"(KOSPI), "2001"(KOSDAQ)

    Returns:
        {"success": True, "name": "코스피", "price": 2650.32,
         "change": -50.12, "change_rate": -1.85, "volume": 420000000}
    """
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-index-price",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHPUP02100000",
                },
                params={
                    "FID_COND_MRKT_DIV_CODE": "U",
                    "FID_INPUT_ISCD": index_code,
                },
            )

            if response.status_code != 200:
                logger.warning("업종지수 조회 실패 ({}): HTTP {}", index_code, response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}"}

            result = response.json()

        output = result.get("output", {})
        if not output:
            return {"success": False, "error": "업종지수 데이터 없음"}

        name_map = {"0001": "KOSPI", "2001": "KOSDAQ"}
        return {
            "success": True,
            "name": name_map.get(index_code, index_code),
            "price": float(output.get("bstp_nmix_prpr", "0")),
            "change": float(output.get("bstp_nmix_prdy_vrss", "0")),
            "change_rate": float(output.get("bstp_nmix_prdy_ctrt", "0")),
            "volume": int(output.get("acml_vol", "0")),
            "trade_amount": output.get("acml_tr_pbmn", "0"),
        }
    except Exception as e:
        logger.error("업종지수 조회 오류 ({}): {}", index_code, str(e))
        return {"success": False, "error": str(e)}


def _get_trading_domain() -> str:
    """거래 API용 도메인 반환 (모의투자는 별도 도메인)"""
    if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL":
        return VIRTUAL_DOMAIN
    return DOMAIN


def _get_account() -> tuple[str, str]:
    """계좌번호를 (CANO, ACNT_PRDT_CD)로 분리"""
    cano = settings.KIS_PAPER_STOCK if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL" else settings.KIS_ACCT_STOCK
    return cano, settings.KIS_PROD_TYPE


async def get_buying_power(symbol: str, price: int = 0, order_dvsn: str = "01") -> dict:
    """매수가능수량 조회 (KIS REST API 직접 호출)

    Args:
        symbol: 종목코드 (예: 005930)
        price: 주문단가 (0이면 시장가)
        order_dvsn: 주문구분 ("01"=시장가, "00"=지정가)

    Returns:
        {"success": bool, "max_qty": int, "available_cash": int}
    """
    cano, acnt_prdt_cd = _get_account()
    domain = _get_trading_domain()
    tr_id = "VTTC8434R" if settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL" else "TTTC8908R"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{domain}/uapi/domestic-stock/v1/trading/inquire-psbl-order",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": tr_id,
                },
                params={
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "PDNO": symbol,
                    "ORD_UNPR": str(price),
                    "ORD_DVSN": order_dvsn,
                    "CMA_EVLU_AMT_ICLD_YN": "N",
                    "OVRS_ICLD_YN": "N",
                },
            )

            if response.status_code != 200:
                logger.warning("매수가능조회 실패: HTTP {}", response.status_code)
                return {"success": False, "max_qty": 0, "available_cash": 0}

            result = response.json()

        output = result.get("output", {})
        rt_cd = result.get("rt_cd", "1")
        if rt_cd != "0":
            msg = result.get("msg1", "")
            logger.warning("매수가능조회 실패 ({}): {}", rt_cd, msg)
            return {"success": False, "max_qty": 0, "available_cash": 0}

        max_qty = int(output.get("nrcvb_buy_qty", "0"))
        available_cash = int(output.get("ord_psbl_cash", "0"))

        logger.debug("매수가능조회 [{}]: max_qty={}, cash={:,}", symbol, max_qty, available_cash)
        return {"success": True, "max_qty": max_qty, "available_cash": available_cash}
    except Exception as e:
        logger.error("매수가능조회 오류 ({}): {}", symbol, str(e))
        return {"success": False, "max_qty": 0, "available_cash": 0}


async def get_balance_direct(afhr_flpr_yn: str = "N") -> dict:
    """주식 잔고 조회 (KIS REST API 직접 호출)

    공식 규격서 기반 (v1_국내주식-006):
    엔드포인트: /uapi/domestic-stock/v1/trading/inquire-balance
    tr_id: TTTC8434R(실전) / VTTC8434R(모의)

    Args:
        afhr_flpr_yn: 시간외/NXT 구분
            "N" — 기본값 (KRX 종가 기준)
            "Y" — 시간외단일가
            "X" — NXT 정규장 (프리마켓, 메인, 애프터마켓) → NXT 실시간 가격 반영
    """
    cano, acnt_prdt_cd = _get_account()
    domain = _get_trading_domain()
    is_paper = settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL"
    tr_id = "VTTC8434R" if is_paper else "TTTC8434R"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{domain}/uapi/domestic-stock/v1/trading/inquire-balance",
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": tr_id,
                },
                params={
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "AFHR_FLPR_YN": afhr_flpr_yn,
                    "OFL_YN": "",
                    "INQR_DVSN": "01",
                    "UNPR_DVSN": "01",
                    "FUND_STTL_ICLD_YN": "N",
                    "FNCG_AMT_AUTO_RDPT_YN": "N",
                    "PRCS_DVSN": "00",
                    "CTX_AREA_FK100": "",
                    "CTX_AREA_NK100": "",
                },
            )

        if response.status_code != 200:
            logger.warning("잔고 조회 실패: HTTP {}", response.status_code)
            return {"success": False, "error": f"HTTP {response.status_code}"}

        result = response.json()
        if result.get("rt_cd") != "0":
            error_msg = result.get("msg1", "잔고 조회 실패")
            logger.warning("잔고 조회 거부: {}", error_msg)
            return {"success": False, "error": error_msg}

        return {"success": True, **result}
    except Exception as e:
        logger.error("잔고 조회 오류: {}", str(e))
        return {"success": False, "error": str(e)}


async def get_fluctuation_rank(sort: str = "top", market: str = "J") -> dict:
    """등락률순위 조회 (KIS REST API 직접 호출)

    Args:
        sort: "top"(급등 상위) 또는 "bottom"(급락 하위)
        market: 시장 구분 - "J"(KRX), "NX"(NXT)

    Returns:
        {"success": True, "stocks": [{"symbol", "name", "price", "change", "change_rate", "volume", ...}, ...]}
    """
    # sort에 따라 정렬 코드 설정
    # 0: 상승률순, 1: 하락률순 (KIS API 기준)
    rank_sort = "0" if sort == "top" else "1"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/ranking/fluctuation",
                headers={
                    "content-type": "application/json",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": "FHPST01700000",
                },
                params={
                    "fid_cond_mrkt_div_code": market,
                    "fid_cond_scr_div_code": "20170",
                    "fid_input_iscd": "0000",             # 전체 종목
                    "fid_rank_sort_cls_code": rank_sort,
                    "fid_input_cnt_1": "0",               # 조회 종목 수 (0=기본값 30)
                    "fid_prc_cls_code": "0",              # 전체 가격대
                    "fid_input_price_1": "100",           # 100원 이상 (동전주/폐지위험 차단)
                    "fid_input_price_2": "",
                    "fid_vol_cnt": "",                    # 거래량 필터 없음 (LLM 판단)
                    "fid_trgt_cls_code": "111111111",     # 전체 대상
                    "fid_trgt_exls_cls_code": "0000000110",  # 관리종목·감리종목 제외
                    "fid_div_cls_code": "1",              # 보통주만 (우선주 제외)
                    "fid_rsfl_rate1": "",                  # 등락률 필터 없음
                    "fid_rsfl_rate2": "",
                },
            )

            if response.status_code != 200:
                logger.warning("등락률순위 조회 실패: HTTP {}", response.status_code)
                return {"success": False, "error": f"HTTP {response.status_code}", "stocks": []}

            result = response.json()

        output = result.get("output", [])
        if not output:
            return {"success": False, "error": "등락률순위 데이터 없음", "stocks": []}

        stocks = []
        for item in output[:30]:  # 최대 30개
            stocks.append({
                "symbol": item.get("mksc_shrn_iscd", item.get("stck_shrn_iscd", "")),
                "name": item.get("hts_kor_isnm", ""),
                "price": item.get("stck_prpr", "0"),
                "current_price": item.get("stck_prpr", "0"),
                "change": item.get("prdy_vrss", "0"),
                "change_rate": item.get("prdy_ctrt", "0"),
                "volume": item.get("acml_vol", "0"),
                "trade_amount": item.get("acml_tr_pbmn", "0"),
                "change_sign": item.get("prdy_vrss_sign", ""),
            })

        logger.debug("등락률순위({}) 조회 완료: {}건", sort, len(stocks))
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error("등락률순위 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "stocks": []}


async def place_order_direct(
    symbol: str,
    side: str,
    quantity: int,
    price: int = 0,
    excg_cd: str = "KRX",
) -> dict:
    """KIS REST API 직접 주문 (NXT/SOR 지원)

    MCP order-stock이 EXCG_ID_DVSN_CD 미지원이므로 직접 호출.
    엔드포인트: /uapi/domestic-stock/v1/trading/order-cash
    tr_id: TTTC0012U(매수)/TTTC0011U(매도) [실전]
           VTTC0012U(매수)/VTTC0011U(매도) [모의]

    Args:
        symbol: 종목코드 (6자리)
        side: "BUY" 또는 "SELL"
        quantity: 주문수량
        price: 주문단가 (지정가)
        excg_cd: 거래소 구분 - "KRX", "NXT", "SOR"
    """
    cano, acnt_prdt_cd = _get_account()
    domain = _get_trading_domain()
    is_paper = settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL"

    if side == "BUY":
        tr_id = "VTTC0012U" if is_paper else "TTTC0012U"
    else:
        tr_id = "VTTC0011U" if is_paper else "TTTC0011U"

    if price <= 0:
        logger.warning("직접 주문 거부: 가격 미지정 (지정가 필수) [{}] {}", excg_cd, symbol)
        return {"success": False, "error": "지정가 주문에 가격이 필요합니다"}

    ord_dvsn = "00"  # 지정가 (AI가 항상 가격 결정)

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.post(
                f"{domain}/uapi/domestic-stock/v1/trading/order-cash",
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": tr_id,
                },
                json={
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "PDNO": symbol,
                    "ORD_DVSN": ord_dvsn,
                    "ORD_QTY": str(quantity),
                    "ORD_UNPR": str(price),
                    "EXCG_ID_DVSN_CD": excg_cd,
                },
            )

        if response.status_code != 200:
            body = ""
            try:
                body = response.json().get("msg1", response.text[:200])
            except Exception:
                body = response.text[:200]
            logger.warning("직접 주문 실패: HTTP {} — {}", response.status_code, body)
            return {"success": False, "error": f"HTTP {response.status_code}: {body}"}

        result = response.json()
        rt_cd = result.get("rt_cd", "")

        # rt_cd="0"만 성공, 그 외 모두 실패 (mcp_client.py:702 동일 패턴)
        if rt_cd != "0":
            error_msg = result.get("msg1", "KIS 주문 거부")
            logger.warning("직접 주문 거부 [{}] {} (rt_cd={}): {}", excg_cd, symbol, rt_cd, error_msg)
            return {"success": False, "error": error_msg, **result}

        # output 타입 안전 체크 + ODNO 탐색 (mcp_client.py:709-714 동일 패턴)
        output = result.get("output", {}) if isinstance(result.get("output"), dict) else {}
        order_id = (
            result.get("ODNO") or result.get("odno")
            or output.get("ODNO") or output.get("odno")
            or ""
        )
        if not order_id:
            logger.warning("직접 주문 응답에서 주문번호 미발견 [{}] {}, 원본: {}", excg_cd, symbol, str(result)[:500])
        logger.info(
            "직접 주문 성공 [{}] {} {} {}주 @{:,}원 (주문번호: {})",
            excg_cd, side, symbol, quantity, price, order_id,
        )
        return {
            "success": True,
            "order_id": order_id,
            "order_time": output.get("ORD_TMD", ""),
            "excg_cd": excg_cd,
            **result,
        }
    except Exception as e:
        logger.error("직접 주문 오류 [{}] {}: {}", excg_cd, symbol, str(e))
        return {"success": False, "error": str(e)}


async def cancel_order_direct(order_id: str, order_branch: str = "") -> dict:
    """KIS REST API 직접 주문 취소

    엔드포인트: /uapi/domestic-stock/v1/trading/order-rvsecncl
    tr_id: TTTC0013U(실전) / VTTC0013U(모의)

    공식 규격서 기반 (v1_국내주식-003):
    - RVSE_CNCL_DVSN_CD: 01=정정, 02=취소
    - QTY_ALL_ORD_YN: Y=전량, N=일부
    - EXCG_ID_DVSN_CD: KRX/NXT/SOR (미입력시 KRX)
    """
    from scheduler.market_calendar import market_calendar

    cano, acnt_prdt_cd = _get_account()
    domain = _get_trading_domain()
    is_paper = settings.KIS_ACCOUNT_TYPE.upper() == "VIRTUAL"
    tr_id = "VTTC0013U" if is_paper else "TTTC0013U"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            token = await _get_access_token(client)
            response = await client.post(
                f"{domain}/uapi/domestic-stock/v1/trading/order-rvsecncl",
                headers={
                    "content-type": "application/json; charset=utf-8",
                    "authorization": f"Bearer {token}",
                    "appkey": _get_app_key(),
                    "appsecret": _get_app_secret(),
                    "tr_id": tr_id,
                },
                json={
                    "CANO": cano,
                    "ACNT_PRDT_CD": acnt_prdt_cd,
                    "KRX_FWDG_ORD_ORGNO": order_branch or "",
                    "ORGN_ODNO": order_id,
                    "ORD_DVSN": "00",
                    "RVSE_CNCL_DVSN_CD": "02",  # 02=취소
                    "ORD_QTY": "0",
                    "ORD_UNPR": "0",
                    "QTY_ALL_ORD_YN": "Y",  # 잔량 전부
                    "EXCG_ID_DVSN_CD": market_calendar.get_excg_dvsn_cd(),
                },
            )

        if response.status_code != 200:
            return {"success": False, "error": f"HTTP {response.status_code}"}

        result = response.json()
        if result.get("rt_cd") != "0":
            error_msg = result.get("msg1", "취소 실패")
            logger.warning("주문 취소 거부: {} — {}", order_id, error_msg)
            return {"success": False, "error": error_msg}

        logger.info("주문 취소 성공: {}", order_id)
        return {"success": True, **result}
    except Exception as e:
        logger.error("주문 취소 오류: {} — {}", order_id, str(e))
        return {"success": False, "error": str(e)}
