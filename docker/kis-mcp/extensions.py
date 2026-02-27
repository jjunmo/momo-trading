
# ── 확장 도구: 분봉 차트 조회 ──
# 이 코드는 Dockerfile에서 server.py 끝에 직접 추가됨 (순환 import 방지)

@mcp.tool()
async def inquery_minute_chart(symbol: str, period: str = "5") -> dict:
    """국내 주식 분봉 차트 조회

    Args:
        symbol: 종목코드 (예: 005930)
        period: 분봉 간격 - "1"(1분), "5"(5분), "15"(15분), "30"(30분), "60"(60분)
    """
    end_time = datetime.now().strftime("%H%M%S")

    try:
        async with httpx.AsyncClient() as client:
            token = await get_access_token(client)
            response = await client.get(
                f"{DOMAIN}/uapi/domestic-stock/v1/quotations/inquire-time-itemchartprice",
                headers={
                    "content-type": CONTENT_TYPE,
                    "authorization": f"{AUTH_TYPE} {token}",
                    "appkey": os.environ["KIS_APP_KEY"],
                    "appsecret": os.environ["KIS_APP_SECRET"],
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
                return {"success": False, "error": f"HTTP {response.status_code}", "prices": []}

            result = response.json()

        if not result or "output2" not in result:
            return {"success": False, "error": "분봉 데이터 조회 실패", "prices": []}

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
        return {"success": False, "error": str(e), "prices": []}
