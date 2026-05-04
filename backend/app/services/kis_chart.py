import os
import asyncio
import httpx
from datetime import date, timedelta
from dotenv import load_dotenv
from .kis_auth import get_access_token, BASE_URL

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")


def _parse_output(output: list) -> list[dict]:
    return [
        {
            "date": item.get("stck_bsop_date", ""),
            "open": int(item.get("stck_oprc", 0) or 0),
            "high": int(item.get("stck_hgpr", 0) or 0),
            "low": int(item.get("stck_lwpr", 0) or 0),
            "close": int(item.get("stck_clpr", 0) or 0),
            "volume": int(item.get("acml_vol", 0) or 0),
        }
        for item in output
        if item.get("stck_clpr")
    ]


async def _fetch_chart_range(client: httpx.AsyncClient, headers: dict, ticker: str,
                              period: str, start: date, end: date) -> list[dict]:
    params = {
        "FID_COND_MRKT_DIV_CODE": "J",
        "FID_INPUT_ISCD": ticker,
        "FID_INPUT_DATE_1": start.strftime("%Y%m%d"),
        "FID_INPUT_DATE_2": end.strftime("%Y%m%d"),
        "FID_PERIOD_DIV_CODE": period,
        "FID_ORG_ADJ_PRC": "0",
    }
    res = await client.get(
        f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-daily-itemchartprice",
        headers=headers,
        params=params,
    )
    res.raise_for_status()
    return _parse_output(res.json().get("output2", []))


async def get_daily_chart(ticker: str, period: str = "D") -> list[dict]:
    """
    period: D=일봉, W=주봉, M=월봉
    KIS API 1회 최대 100개 제한 → 3번 호출로 약 300거래일 확보
    (MA120, 52주 고저 계산에 필요)
    """
    token = await get_access_token()
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST03010100",
        "custtype": "P",
    }

    today = date.today()
    # 100거래일 ≈ 140일, 3구간으로 나눔
    ranges = [
        (today - timedelta(days=140), today),
        (today - timedelta(days=280), today - timedelta(days=141)),
        (today - timedelta(days=420), today - timedelta(days=281)),
    ]

    all_candles: dict[str, dict] = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for start, end in ranges:
            try:
                candles = await _fetch_chart_range(client, headers, ticker, period, start, end)
                for c in candles:
                    all_candles[c["date"]] = c
                await asyncio.sleep(0.8)  # KIS rate limit
            except Exception:
                continue  # 일부 구간 실패해도 나머지 구간 시도

    return sorted(all_candles.values(), key=lambda x: x["date"])
