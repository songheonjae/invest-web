import os
import asyncio
import httpx
from dotenv import load_dotenv
from .kis_auth import get_access_token, BASE_URL

load_dotenv()

APP_KEY = os.getenv("KIS_APP_KEY")
APP_SECRET = os.getenv("KIS_APP_SECRET")

# ── 우선주 → 보통주 종목코드 매핑 ─────────────────────────────────────────────
PREFERRED_TO_COMMON = {
    "005935": "005930",  # 삼성전자우 → 삼성전자
    "000815": "000810",  # 삼성화재우 → 삼성화재
    "009155": "009150",  # 삼성전기우 → 삼성전기
    "006405": "006400",  # 삼성SDI우 → 삼성SDI
    "005385": "005380",  # 현대차우 → 현대차
    "005387": "005380",  # 현대차2우B → 현대차
    "005389": "005380",  # 현대차3우B → 현대차
    "003555": "003550",  # LG우 → LG
    "051915": "051910",  # LG화학우 → LG화학
    "002795": "002790",  # 아모레퍼시픽우 → 아모레퍼시픽
    "000885": "000880",  # 한화솔루션우 → 한화솔루션
    "003605": "003600",  # SK우 → SK
}


def _is_missing_or_zero(val) -> bool:
    if val is None or val == "" or val == "N/A":
        return True
    try:
        return float(str(val).replace(",", "").strip()) == 0
    except (ValueError, TypeError):
        return True


def _is_preferred_stock(name: str) -> bool:
    """종목명 패턴으로 우선주 여부 판별 (ticker 단독 판단 금지)."""
    return (
        name.endswith("우")
        or "우B" in name
        or "1우" in name
        or "2우" in name
    )


def _get_common_ticker(ticker: str, name: str) -> str | None:
    """우선주 종목코드 → 보통주 종목코드 반환. 확인 불가 시 None."""
    if ticker in PREFERRED_TO_COMMON:
        return PREFERRED_TO_COMMON[ticker]
    # 자동 유도: 종목명이 우선주 패턴이고 끝자리가 5인 경우만 0으로 교체
    if _is_preferred_stock(name) and ticker.endswith("5") and len(ticker) == 6:
        return ticker[:-1] + "0"
    return None


async def _raw_price_and_fundamental(ticker: str) -> tuple[dict, dict]:
    """KIS API 단순 호출 (우선주 보정 없음). 내부 전용."""
    token = await get_access_token()
    headers = {
        "authorization": f"Bearer {token}",
        "appkey": APP_KEY,
        "appsecret": APP_SECRET,
        "tr_id": "FHKST01010100",
        "custtype": "P",
    }
    params = {"fid_cond_mrkt_div_code": "J", "fid_input_iscd": ticker}
    o = {}
    async with httpx.AsyncClient(timeout=10) as client:
        for attempt in range(3):
            res = await client.get(
                f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
                headers=headers,
                params=params,
            )
            if res.status_code == 200:
                o = res.json().get("output", {})
                break
            if attempt < 2:
                await asyncio.sleep(0.5)

    stock = {
        "ticker": ticker,
        "name": o.get("hts_kor_isnm", ""),
        "price": o.get("stck_prpr", ""),
        "change": o.get("prdy_vrss", ""),
        "change_rate": o.get("prdy_ctrt", ""),
        "volume": o.get("acml_vol", ""),
        "high": o.get("stck_hgpr", ""),
        "low": o.get("stck_lwpr", ""),
        "open": o.get("stck_oprc", ""),
    }
    sector = o.get("bstp_kor_isnm", "")
    fundamental = {
        "per": o.get("per", ""),
        "pbr": o.get("pbr", ""),
        "eps": o.get("eps", ""),
        "bps": o.get("bps", ""),
        "market_cap": o.get("hts_avls", ""),
        "week52_high": o.get("w52_hgpr", ""),
        "week52_low": o.get("w52_lwpr", ""),
        "foreign_rate": o.get("hts_frgn_ehrt", ""),
        "dividend_yield": o.get("dvnd_yield", ""),
        "name": o.get("hts_kor_isnm", ""),
        "sector": sector,
        "industry": sector,
        "_trading_value_raw": o.get("acml_tr_pbmn", ""),
    }
    return stock, fundamental


async def _fix_preferred_stock_valuation(
    ticker: str, name: str, current_price, fundamental: dict
) -> dict:
    """우선주 PER/PBR이 0이면 보통주 EPS/BPS로 재계산."""
    per = fundamental.get("per")
    pbr = fundamental.get("pbr")

    if not _is_missing_or_zero(per) and not _is_missing_or_zero(pbr):
        return fundamental

    if not _is_preferred_stock(name):
        return fundamental

    common_ticker = _get_common_ticker(ticker, name)
    if not common_ticker:
        if _is_missing_or_zero(per):
            fundamental["per"] = "N/A"
            fundamental["per_source_note"] = "우선주이나 대응 보통주 코드를 확인할 수 없어 N/A 처리"
        if _is_missing_or_zero(pbr):
            fundamental["pbr"] = "N/A"
            fundamental["pbr_source_note"] = "우선주이나 대응 보통주 코드를 확인할 수 없어 N/A 처리"
        fundamental["preferred_stock_note"] = (
            "이 종목은 우선주라 일부 API에서 PER/PBR이 0으로 내려올 수 있습니다. "
            "대응 보통주 코드를 확인할 수 없어 N/A로 표시합니다."
        )
        return fundamental

    try:
        _, common_fundamental = await _raw_price_and_fundamental(common_ticker)
    except Exception:
        if _is_missing_or_zero(per):
            fundamental["per"] = "N/A"
        if _is_missing_or_zero(pbr):
            fundamental["pbr"] = "N/A"
        fundamental["preferred_stock_note"] = (
            "이 종목은 우선주입니다. 보통주 데이터 조회에 실패하여 PER/PBR을 N/A로 표시합니다."
        )
        return fundamental

    try:
        price_f = float(str(current_price).replace(",", "").strip())
    except (ValueError, TypeError):
        return fundamental

    eps = common_fundamental.get("eps")
    bps = common_fundamental.get("bps")
    fixed_any = False

    if _is_missing_or_zero(per):
        try:
            eps_f = float(str(eps).replace(",", "").strip()) if eps else 0
            if eps_f > 0:
                fundamental["per"] = round(price_f / eps_f, 2)
                fundamental["per_source_note"] = f"우선주 지표 보정: {common_ticker} EPS 기준 계산"
                fixed_any = True
            else:
                fundamental["per"] = "N/A"
                fundamental["per_source_note"] = f"우선주이나 보통주({common_ticker}) EPS가 0 또는 음수라 계산 불가"
        except (ValueError, TypeError):
            fundamental["per"] = "N/A"

    if _is_missing_or_zero(pbr):
        try:
            bps_f = float(str(bps).replace(",", "").strip()) if bps else 0
            if bps_f > 0:
                fundamental["pbr"] = round(price_f / bps_f, 2)
                fundamental["pbr_source_note"] = f"우선주 지표 보정: {common_ticker} BPS 기준 계산"
                fixed_any = True
            else:
                fundamental["pbr"] = "N/A"
                fundamental["pbr_source_note"] = f"우선주이나 보통주({common_ticker}) BPS가 0 또는 음수라 계산 불가"
        except (ValueError, TypeError):
            fundamental["pbr"] = "N/A"

    if fixed_any:
        fundamental["preferred_stock_note"] = (
            "이 종목은 우선주라 일부 API에서 PER/PBR이 0으로 내려올 수 있습니다. "
            f"현재 값은 보통주({common_ticker}) 재무 데이터와 우선주 현재가를 기준으로 보정 계산했습니다."
        )
    elif _is_missing_or_zero(fundamental.get("per")) or _is_missing_or_zero(fundamental.get("pbr")):
        fundamental["preferred_stock_note"] = (
            "이 종목은 우선주입니다. PER/PBR 계산에 필요한 보통주 재무 데이터를 확인할 수 없어 N/A로 표시합니다."
        )

    return fundamental


async def get_fundamental(ticker: str) -> dict:
    """get_stock_price와 같은 엔드포인트 사용 — 라우터에서 이미 호출한 결과를 재활용하므로 여기선 빈 dict 반환"""
    return {}


async def get_price_and_fundamental(ticker: str) -> tuple[dict, dict]:
    """주가 + 재무지표를 단일 API 호출로 가져옴 (중복 호출 방지). 우선주는 자동 보정."""
    stock, fundamental = await _raw_price_and_fundamental(ticker)
    name = fundamental.get("name", "")
    current_price = stock.get("price", "")
    fundamental = await _fix_preferred_stock_valuation(ticker, name, current_price, fundamental)
    return stock, fundamental


async def get_investor_trend(ticker: str) -> dict:
    """외국인/기관 5일·20일 순매수 데이터"""
    try:
        token = await get_access_token()
        headers = {
            "authorization": f"Bearer {token}",
            "appkey": APP_KEY,
            "appsecret": APP_SECRET,
            "tr_id": "FHKST01010900",
            "custtype": "P",
        }
        params = {
            "fid_cond_mrkt_div_code": "J",
            "fid_input_iscd": ticker,
        }
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.get(
                f"{BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-investor",
                headers=headers,
                params=params,
            )
        data = res.json()
        output = data.get("output", [])
        if not output:
            return {}

        def sum_qty(rows, key, days):
            total = 0
            for r in rows[:days]:
                try:
                    total += int(r.get(key, 0) or 0)
                except Exception:
                    pass
            return total

        return {
            "foreign_5d": sum_qty(output, "frgn_ntby_qty", 5),
            "foreign_20d": sum_qty(output, "frgn_ntby_qty", 20),
            "institution_5d": sum_qty(output, "orgn_ntby_qty", 5),
            "institution_20d": sum_qty(output, "orgn_ntby_qty", 20),
        }
    except Exception:
        return {}
