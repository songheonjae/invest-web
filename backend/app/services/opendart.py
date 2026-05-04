import io
import os
import asyncio
import zipfile
import xml.etree.ElementTree as ET
import httpx
from datetime import datetime

DART_API_KEY = os.getenv("DART_API_KEY", "")
DART_BASE = "https://opendart.fss.or.kr/api"

ANNUAL = "11011"
Q1_RPT = "11013"
HALF   = "11012"
Q3_RPT = "11014"

# stock_code → corp_code 매핑 캐시 (프로세스 수명 동안 유지)
_corp_code_cache: dict[str, str] = {}


async def _load_corp_code_map(client: httpx.AsyncClient) -> None:
    """DART corpCode.xml ZIP 다운로드 → _corp_code_cache 채우기."""
    if _corp_code_cache:
        return
    try:
        res = await client.get(
            f"{DART_BASE}/corpCode.xml",
            params={"crtfc_key": DART_API_KEY},
            timeout=15,
        )
        with zipfile.ZipFile(io.BytesIO(res.content)) as zf:
            xml_bytes = zf.read("CORPCODE.xml")
        root = ET.fromstring(xml_bytes)
        for item in root.findall("list"):
            stock = (item.findtext("stock_code") or "").strip()
            corp  = (item.findtext("corp_code")  or "").strip()
            if stock and corp:
                _corp_code_cache[stock] = corp
    except Exception:
        pass


async def get_corp_code(ticker: str, client: httpx.AsyncClient) -> str | None:
    if not DART_API_KEY:
        return None
    if ticker not in _corp_code_cache:
        await _load_corp_code_map(client)
    return _corp_code_cache.get(ticker)


async def _fetch_stmt(
    corp_code: str, year: int, reprt_code: str, client: httpx.AsyncClient
) -> list[dict]:
    if not DART_API_KEY or not corp_code:
        return []
    # CFS(연결) 우선, 없으면 OFS(별도)
    for fs_div in ("CFS", "OFS"):
        try:
            res = await client.get(
                f"{DART_BASE}/fnlttSinglAcntAll.json",
                params={
                    "crtfc_key": DART_API_KEY,
                    "corp_code": corp_code,
                    "bsns_year": str(year),
                    "reprt_code": reprt_code,
                    "fs_div": fs_div,
                },
                timeout=10,
            )
            data = res.json()
            if data.get("status") == "000" and data.get("list"):
                return data["list"]
        except Exception:
            pass
    return []


def _extract_is(items: list[dict], keywords: list[str], use_add: bool = False) -> float | None:
    """
    IS/CIS에서 첫 번째 매칭 account의 금액.
    keywords에서 "=keyword" prefix는 완전 일치, 나머지는 부분 일치.
    use_add=True → thstrm_add_amount (누적) 사용 (Q4 계산용 9M 누적값)
    """
    val, _ = _extract_is_with_nm(items, keywords, use_add)
    return val


def _extract_is_with_nm(
    items: list[dict], keywords: list[str], use_add: bool = False
) -> tuple[float | None, str | None]:
    """_extract_is와 동일하나 (value, matched_account_nm) 반환."""
    field = "thstrm_add_amount" if use_add else "thstrm_amount"
    for kw in keywords:
        exact = kw.startswith("=")
        kw_text = kw[1:] if exact else kw
        for item in items:
            if item.get("sj_div") not in ("IS", "CIS"):
                continue
            nm = item.get("account_nm", "")
            if (exact and nm == kw_text) or (not exact and kw_text in nm):
                raw = item.get(field, "").replace(",", "").strip()
                try:
                    return float(raw), nm
                except (ValueError, TypeError):
                    pass
    return None, None


def _extract_bs(items: list[dict], keywords: list[str]) -> float | None:
    """BS에서 첫 번째 매칭 account의 thstrm_amount"""
    for kw in keywords:
        for item in items:
            if item.get("sj_div") == "BS" and kw in item.get("account_nm", ""):
                raw = item.get("thstrm_amount", "").replace(",", "").strip()
                try:
                    return float(raw)
                except (ValueError, TypeError):
                    pass
    return None


def _to_eok(val: float | None) -> str | None:
    """원(KRW) → 억원 포맷 문자열 (콤마 포함)"""
    if val is None:
        return None
    return f"{round(val / 100_000_000):,}"


def _margin(op: float | None, rev: float | None) -> str | None:
    """원 단위 두 값의 비율(%) — 단위 소거되므로 변환 불필요"""
    if op is None or not rev:
        return None
    return f"{op / rev * 100:.1f}"


def _roe(net: float | None, equity: float | None) -> str | None:
    if net is None or not equity:
        return None
    return f"{net / equity * 100:.1f}"


# 완전 일치("=prefix") → 부분 일치 순서 보장
# 영업이익: "계속영업이익(손실)"이 부분 일치로 먼저 잡히는 버그 방지 → 완전 일치 우선
# 영업수익: "영업수익"이 "보험영업수익"을 부분 일치로 잡는 연도간 불일치 방지 → 완전 일치 우선
_REV_KW = ["매출액", "수익(매출액)", "=영업수익", "=영업수익(매출액)", "영업수익", "=매출"]
_OP_KW  = ["=영업이익(손실)", "=영업이익", "영업이익(손실)", "영업이익"]
_NET_KW = ["=당기순이익(손실)", "=분기순이익(손실)", "=당기순손익", "=분기순손익", "당기순이익", "분기순이익", "당기순손익", "분기순손익"]


def _amounts(items: list[dict]) -> tuple[float | None, float | None, float | None]:
    rev = _extract_is(items, _REV_KW)
    op  = _extract_is(items, _OP_KW)
    net = _extract_is(items, _NET_KW)
    return rev, op, net


def _sub(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return a - b


def _annual_record(year: int, items: list[dict]) -> dict:
    import logging
    _log = logging.getLogger(__name__)

    rev, rev_nm = _extract_is_with_nm(items, _REV_KW)
    op,  op_nm  = _extract_is_with_nm(items, _OP_KW)
    net, net_nm = _extract_is_with_nm(items, _NET_KW)
    eq = _extract_bs(items, ["자본총계"])

    _log.info(
        "[DART %d] 매출매핑: '%s'=%s억 | 영업이익: '%s'=%s억 | 순이익: '%s'=%s억",
        year,
        rev_nm, _to_eok(rev),
        op_nm,  _to_eok(op),
        net_nm, _to_eok(net),
    )

    return {
        "period": f"{year}.12",  # Naver 포맷과 통일 (YYYY.12)
        "매출액": _to_eok(rev),
        "영업이익": _to_eok(op),
        "당기순이익": _to_eok(net),
        "영업이익률": _margin(op, rev),
        "순이익률": _margin(net, rev),
        "ROE": _roe(net, eq),
        "부채비율": None,
        # 원천 계정명 — format_financials_for_prompt에서 금융주 표기에 활용
        "_source_account_rev": rev_nm,
    }


def _quarter_record(period: str, rev, op, net) -> dict:
    return {
        "period": period,
        "매출액": _to_eok(rev),
        "영업이익": _to_eok(op),
        "당기순이익": _to_eok(net),
        "영업이익률": _margin(op, rev),
        "순이익률": _margin(net, rev),
        "ROE": None,
        "부채비율": None,
    }


async def get_dart_confirmed_financials(ticker: str) -> dict | None:
    """
    OpenDART에서 확정 실적만 가져옴.
    - 연간: 최근 3개년
    - 분기: 최근 1년치 4분기 (standalone)
    반환 형식: {"annual": [...], "quarterly": [...], "unit": "억원"}
    실패 시 None.
    """
    if not DART_API_KEY:
        return None

    now = datetime.now()
    # 4월 기준: 전년도 사업보고서(3월 제출)는 이미 나와 있음
    recent_year = now.year - 1

    async with httpx.AsyncClient(timeout=12) as client:
        corp_code = await get_corp_code(ticker, client)
        if not corp_code:
            return None

        annual_years = [recent_year, recent_year - 1, recent_year - 2]

        # 연간 3개년 + 분기 3종 병렬 조회
        tasks = (
            [_fetch_stmt(corp_code, y, ANNUAL, client) for y in annual_years]
            + [
                _fetch_stmt(corp_code, recent_year, Q1_RPT, client),
                _fetch_stmt(corp_code, recent_year, HALF,   client),
                _fetch_stmt(corp_code, recent_year, Q3_RPT, client),
            ]
        )
        results = await asyncio.gather(*tasks, return_exceptions=True)

    annual_items_list = [r if not isinstance(r, Exception) else [] for r in results[:3]]
    q1_items  = results[3] if not isinstance(results[3], Exception) else []
    h1_items  = results[4] if not isinstance(results[4], Exception) else []
    q3_items  = results[5] if not isinstance(results[5], Exception) else []
    fy_items  = annual_items_list[0]  # recent_year 사업보고서 = 연간 합계

    # 연간 records (확정값만, 오래된 순)
    annual = []
    for y, items in zip(reversed(annual_years), reversed(annual_items_list)):
        if not items:
            continue
        rec = _annual_record(y, items)
        if rec["매출액"] is not None:
            annual.append(rec)

    # 분기 standalone 계산
    # thstrm_amount = 해당 보고서 기간의 standalone 값 (Q2 반기 보고서도 Q2 단독값)
    # thstrm_add_amount = 연초부터 누적 (Q4 계산에 활용)
    REV_KW = ["매출액", "수익(매출액)", "=영업수익", "=영업수익(매출액)", "영업수익"]
    OP_KW  = ["=영업이익(손실)", "=영업이익", "영업이익(손실)", "영업이익"]
    NET_KW = ["=당기순이익(손실)", "=분기순이익(손실)", "=당기순손익", "=분기순손익", "당기순이익", "분기순이익", "당기순손익", "분기순손익"]

    def _q(items):
        return (
            _extract_is(items, REV_KW),
            _extract_is(items, OP_KW),
            _extract_is(items, NET_KW),
        )

    def _q_cum(items):
        """9M 누적값 (thstrm_add_amount) — Q4 = FY - 9M에 사용"""
        return (
            _extract_is(items, REV_KW, use_add=True),
            _extract_is(items, OP_KW,  use_add=True),
            _extract_is(items, NET_KW, use_add=True),
        )

    q1r, q1o, q1n = _q(q1_items)
    q2r, q2o, q2n = _q(h1_items)   # 반기 보고서 thstrm_amount = Q2 standalone
    q3r, q3o, q3n = _q(q3_items)   # 3분기 보고서 thstrm_amount = Q3 standalone
    fyr, fyo, fyn = _amounts(fy_items)
    # Q4 = FY - 9M누적 (thstrm_add_amount of Q3 report)
    cum9mr, cum9mo, cum9mn = _q_cum(q3_items)

    quarterly = []
    if q1r is not None:
        quarterly.append(_quarter_record(f"{recent_year}.03", q1r, q1o, q1n))
    if q2r is not None:
        quarterly.append(_quarter_record(f"{recent_year}.06", q2r, q2o, q2n))
    if q3r is not None:
        quarterly.append(_quarter_record(f"{recent_year}.09", q3r, q3o, q3n))
    if fyr is not None and cum9mr is not None:
        q4r = _sub(fyr, cum9mr)
        q4o = _sub(fyo, cum9mo)
        q4n = _sub(fyn, cum9mn)
        quarterly.append(_quarter_record(f"{recent_year}.12", q4r, q4o, q4n))

    if not annual and not quarterly:
        return None

    return {"annual": annual, "quarterly": quarterly, "unit": "억원"}
