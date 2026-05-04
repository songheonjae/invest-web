import os
import httpx
from dotenv import load_dotenv
from .kis_auth import get_access_token, BASE_URL

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
IS_MOCK = os.getenv("KIS_IS_MOCK", "true").lower() == "true"


async def get_stock_price(ticker: str) -> dict:
    token = await get_access_token()
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100",
        "custtype": "P",
    }
    params = {
        "fid_cond_mrkt_div_code": "J",
        "fid_input_iscd": ticker,
    }
    async with httpx.AsyncClient() as client:
        res = await client.get(
            f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            headers=headers,
            params=params,
        )
        data = res.json() if res.status_code == 200 else {}
        output = data.get("output", {})
        return {
            "ticker": ticker,
            "name": output.get("hts_kor_isnm", ""),
            "price": output.get("stck_prpr", ""),
            "change": output.get("prdy_vrss", ""),
            "change_rate": output.get("prdy_ctrt", ""),
            "volume": output.get("acml_vol", ""),
            "high": output.get("stck_hgpr", ""),
            "low": output.get("stck_lwpr", ""),
            "open": output.get("stck_oprc", ""),
        }
