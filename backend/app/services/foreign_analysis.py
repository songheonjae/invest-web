import math
import yfinance as yf


def get_foreign_stock_and_fundamental(ticker: str) -> tuple[dict, dict]:
    """yf.Ticker 단일 호출로 stock + fundamental 동시 반환."""
    t = yf.Ticker(ticker)
    info = t.info
    stock = {
        "ticker": ticker,
        "name": info.get("longName", ""),
        "price": info.get("currentPrice") or info.get("regularMarketPrice", ""),
        "change": info.get("regularMarketChange", ""),
        "change_rate": info.get("regularMarketChangePercent", ""),
        "volume": info.get("regularMarketVolume", ""),
        "high": info.get("dayHigh", ""),
        "low": info.get("dayLow", ""),
        "open": info.get("regularMarketOpen", ""),
        "market_cap": info.get("marketCap", ""),
        "per": info.get("trailingPE", ""),
    }
    fundamental = _build_foreign_fundamental(t, info)
    return stock, fundamental


def get_foreign_fundamental(ticker: str) -> dict:
    t = yf.Ticker(ticker)
    info = t.info
    return _build_foreign_fundamental(t, info)


def _build_foreign_fundamental(t, info: dict) -> dict:

    # 다음 실적 발표일 + 분기 정보
    earnings_date = ""
    next_quarter = ""
    last_reported_quarter = ""
    try:
        from datetime import datetime, timezone

        # 최근 발표된 분기 (quarterly_financials 첫 컬럼)
        qfin = t.quarterly_financials
        if qfin is not None and not qfin.empty:
            last_col = qfin.columns[0]
            if hasattr(last_col, "month"):
                q = (last_col.month - 1) // 3 + 1
                last_reported_quarter = f"Q{q} {last_col.year}"

        # 다음 실적 발표일
        cal = t.calendar
        if cal is not None:
            # yfinance 버전에 따라 dict 또는 DataFrame 반환
            if hasattr(cal, "get"):
                dates = cal.get("Earnings Date", [])
            elif hasattr(cal, "loc"):
                try:
                    dates = list(cal.loc["Earnings Date"])
                except (KeyError, Exception):
                    dates = []
            else:
                dates = []
            if dates:
                now = datetime.now(timezone.utc)
                future = [d for d in dates if hasattr(d, "timestamp") and d > now]
                target = future[0] if future else dates[0]
                # KST(UTC+9) 기준으로 변환
                from datetime import timedelta
                kst = target + timedelta(hours=9) if hasattr(target, "utcoffset") and target.utcoffset() is not None else target
                earnings_date = kst.strftime("%Y-%m-%d") + " (KST 기준)"

                # 발표월로 분기 역산 (4~5월→Q1, 7~8월→Q2, 10~11월→Q3, 1~2월→Q4)
                m, y = target.month, target.year
                if m in [1, 2]:
                    next_quarter = f"Q4 {y - 1}"
                elif m in [4, 5]:
                    next_quarter = f"Q1 {y}"
                elif m in [7, 8]:
                    next_quarter = f"Q2 {y}"
                elif m in [10, 11]:
                    next_quarter = f"Q3 {y}"
                else:
                    next_quarter = ""
    except Exception:
        pass

    return {
        "per": str(round(info.get("trailingPE", 0) or 0, 2)),
        "pbr": str(round(info.get("priceToBook", 0) or 0, 2)),
        "eps": str(round(info.get("trailingEps", 0) or 0, 2)),
        "market_cap": str(info.get("marketCap", "")),
        "week52_high": str(info.get("fiftyTwoWeekHigh", "")),
        "week52_low": str(info.get("fiftyTwoWeekLow", "")),
        "foreign_rate": "",
        "dividend_yield": str(round(_calc_dividend_yield(info.get("dividendYield")), 2)),
        "sector": info.get("sector", ""),
        "industry": info.get("industry", ""),
        "company_overview": (info.get("longBusinessSummary", "") or "")[:500],
        "earnings_date": earnings_date,
        "next_quarter": next_quarter,
        "last_reported_quarter": last_reported_quarter,
    }


def _calc_dividend_yield(raw) -> float:
    """yfinance dividendYield는 버전마다 소수(0.019) 또는 퍼센트(1.9)로 옴. 1.0 이하면 × 100."""
    if not raw:
        return 0.0
    val = float(raw)
    return round(val * 100, 2) if val <= 1.0 else round(val, 2)


def _safe_val(df, row_candidates: list, col, divisor: float = 1_000_000) -> float | None:
    for row in row_candidates:
        try:
            val = df.loc[row, col]
            if val is not None and not (isinstance(val, float) and math.isnan(val)):
                return round(float(val) / divisor, 1)
        except (KeyError, TypeError):
            pass
    return None


def get_foreign_financials(ticker: str) -> dict:
    """yfinance로 연간/분기 실적 가져오기. 단위: USD 백만(M)."""
    try:
        t = yf.Ticker(ticker)

        def parse_df(df) -> list[dict]:
            if df is None or df.empty:
                return []
            records = []
            for col in reversed(list(df.columns)[:4]):
                try:
                    period = col.strftime("%Y.%m") if hasattr(col, "strftime") else str(col)[:7]
                    rev = _safe_val(df, ["Total Revenue", "Revenue"], col)
                    op  = _safe_val(df, ["Operating Income", "EBIT"], col)
                    net = _safe_val(df, ["Net Income", "Net Income Common Stockholders"], col)
                    op_margin = round(op / rev * 100, 2) if rev and op and abs(rev) > 0 else None
                    records.append({
                        "period": period,
                        "매출액": rev,
                        "영업이익": op,
                        "당기순이익": net,
                        "영업이익률": op_margin,
                    })
                except Exception:
                    pass
            return records

        annual    = parse_df(t.financials)
        quarterly = parse_df(t.quarterly_financials)

        return {"annual": annual, "quarterly": quarterly[-4:], "unit": "USD M"}
    except Exception:
        return {}


def get_foreign_chart(ticker: str, period: str = "6mo") -> list[dict]:
    hist = yf.Ticker(ticker).history(period=period)
    result = []
    for date, row in hist.iterrows():
        result.append({
            "date": date.strftime("%Y%m%d"),
            "open": round(row["Open"], 2),
            "high": round(row["High"], 2),
            "low": round(row["Low"], 2),
            "close": round(row["Close"], 2),
            "volume": int(row["Volume"]),
        })
    return result
