import os
import json
import time
import httpx
from dotenv import load_dotenv

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")
IS_MOCK = os.getenv("KIS_IS_MOCK", "true").lower() == "true"

BASE_URL = "https://openapivts.koreainvestment.com:29443" if IS_MOCK else "https://openapi.koreainvestment.com:9443"

TOKEN_FILE = os.path.join(os.path.dirname(__file__), "../../.kis_token.json")


def _load_cached_token() -> str | None:
    try:
        with open(TOKEN_FILE) as f:
            data = json.load(f)
        if data.get("expires_at", 0) > time.time() + 60:
            return data["access_token"]
    except Exception:
        pass
    return None


def _save_token(token: str, expires_in: int):
    with open(TOKEN_FILE, "w") as f:
        json.dump({"access_token": token, "expires_at": time.time() + expires_in}, f)


async def get_access_token() -> str:
    cached = _load_cached_token()
    if cached:
        return cached

    async with httpx.AsyncClient() as client:
        res = await client.post(
            f"{BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": APP_KEY,
                "appsecret": APP_SECRET,
            },
        )
        res.raise_for_status()
        body = res.json()
        token = body["access_token"]
        expires_in = int(body.get("expires_in", 86400))
        _save_token(token, expires_in)
        return token
