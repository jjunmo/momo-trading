"""KIS REST API 직접 호출 클라이언트

MCP를 거치지 않고 KIS API를 직접 호출하여
분봉, 거래량순위, 등락률순위 등 MCP 미지원 데이터를 조회한다.
"""
import json
from datetime import datetime, timedelta
from pathlib import Path

import httpx
from loguru import logger

from core.config import settings

DOMAIN = "https://openapi.koreainvestment.com:9443"
VIRTUAL_DOMAIN = "https://openapivts.koreainvestment.com:29443"
TOKEN_FILE = Path("data/kis_token.json")


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
    """토큰 발급 (파일 캐싱)"""
    # 캐시된 토큰 확인
    if TOKEN_FILE.exists():
        try:
            token_data = json.loads(TOKEN_FILE.read_text())
            expires_at = datetime.fromisoformat(token_data["expires_at"])
            if datetime.now() < expires_at:
                return token_data["token"]
        except Exception:
            pass

    # 새 토큰 발급
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
        raise Exception(f"KIS 토큰 발급 실패: {resp.text}")

    data = resp.json()
    token = data["access_token"]
    expires_at = datetime.now() + timedelta(hours=23)

    # 캐시 저장
    TOKEN_FILE.parent.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(json.dumps({
        "token": token,
        "expires_at": expires_at.isoformat(),
    }))

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
                    "FID_DIV_CLS_CODE": "0",             # 전체 (보통주+우선주)
                    "FID_BLNG_CLS_CODE": "0",            # 평균거래량 기준
                    "FID_TRGT_CLS_CODE": "111111111",    # 전체 대상
                    "FID_TRGT_EXLS_CLS_CODE": "0000000000",  # 제외 없음
                    "FID_INPUT_PRICE_1": "",              # 가격 필터 없음
                    "FID_INPUT_PRICE_2": "",
                    "FID_VOL_CNT": "",                   # 거래량 필터 없음
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

        logger.info("거래량순위 조회 완료: {}건", len(stocks))
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error("거래량순위 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "stocks": []}


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
                    "fid_input_price_1": "",               # 가격 필터 없음
                    "fid_input_price_2": "",
                    "fid_vol_cnt": "",                    # 거래량 필터 없음
                    "fid_trgt_cls_code": "111111111",     # 전체 대상
                    "fid_trgt_exls_cls_code": "0000000000",  # 제외 없음
                    "fid_div_cls_code": "0",              # 전체 (보통주+우선주)
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

        logger.info("등락률순위({}) 조회 완료: {}건", sort, len(stocks))
        return {"success": True, "stocks": stocks}
    except Exception as e:
        logger.error("등락률순위 조회 오류: {}", str(e))
        return {"success": False, "error": str(e), "stocks": []}
