import asyncio
from fastapi import APIRouter, HTTPException, Query
from app.services.kis_stock import get_stock_price
from app.services.yfinance_stock import get_foreign_stock
from app.services.stock_search import search_domestic, search_foreign
from app.services.kis_chart import get_daily_chart
from app.services.kis_fundamental import get_fundamental, get_investor_trend, get_price_and_fundamental
from app.services.news import get_stock_news, get_foreign_news
from app.services.claude_analysis import analyze_stock
from app.services.foreign_analysis import get_foreign_chart, get_foreign_financials, get_foreign_stock_and_fundamental
from app.services.financials import get_financials, format_financials_for_prompt, get_company_overview, validate_financials, get_sector_per_naver, get_quarter_meta
from app.services.confidence import calculate_confidence
from app.services.analysis_logger import log_analysis

router = APIRouter(prefix="/stock", tags=["stock"])


@router.get("/search/domestic")
def search_domestic_stock(q: str = Query(..., min_length=1)):
    try:
        return search_domestic(q)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/search/foreign")
def search_foreign_stock(q: str = Query(..., min_length=1)):
    try:
        return search_foreign(q)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/chart/domestic/{ticker}")
async def domestic_chart(ticker: str, period: str = Query("D")):
    try:
        return await get_daily_chart(ticker, period)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/analyze/domestic/{ticker}")
async def analyze_domestic(ticker: str):
    validation = None
    confidence = None
    news = []
    ai_success = False
    error_msg = None
    chart = []
    try:
        (stock, fundamental), financials, company_overview, investor_trend = await asyncio.gather(
            get_price_and_fundamental(ticker),
            get_financials(ticker),
            get_company_overview(ticker),
            get_investor_trend(ticker),
        )
        from app.services.stock_search import KRX_STOCKS
        ticker_to_name = {t: n for n, t in KRX_STOCKS}
        name = stock.get("name") or fundamental.get("name") or ticker_to_name.get(ticker, "")
        if not stock.get("name"):
            stock["name"] = name
        news, sector_naver_per = await asyncio.gather(
            get_stock_news(ticker, name),
            get_sector_per_naver(fundamental.get("sector", "")),
        )
        try:
            chart = await get_daily_chart(ticker, "D")
        except Exception:
            chart = []
        from app.services.technical import calc_indicators
        indicators = calc_indicators(chart) if chart else {}
        validation = validate_financials(financials, sector=fundamental.get("sector", ""), industry=fundamental.get("industry", ""))
        confidence = calculate_confidence(fundamental, financials, indicators, news, validation)
        trading_raw = fundamental.pop("_trading_value_raw", "")
        if trading_raw and investor_trend is not None:
            try:
                investor_trend["trading_value"] = round(int(trading_raw) / 100_000_000, 1)
            except (ValueError, TypeError):
                pass
        fundamental["financials_text"] = format_financials_for_prompt(
            financials,
            is_financial=validation.get("is_financial", False),
            is_bio=validation.get("is_bio", False),
            is_semiconductor=validation.get("is_semiconductor", False),
        )
        fundamental["financials"] = financials
        fundamental["company_overview"] = company_overview
        fundamental["investor_trend"] = investor_trend
        fundamental.update(get_quarter_meta(financials))
        report = await analyze_stock(stock, fundamental, chart, news, validation=validation, confidence=confidence, sector_naver_per=sector_naver_per)
        ai_success = True

        # 수동 검토 플래그 조건
        needs_review = False
        review_reason = None
        val_max = validation.get("max_severity", "ok") if validation else "ok"
        if val_max == "severe":
            needs_review = True
            review_reason = f"severe validation: {[i['message'] for i in validation.get('issues',[]) if i['severity']=='severe']}"
        elif not financials.get("annual") and not financials.get("quarterly"):
            needs_review = True
            review_reason = "실적 데이터 완전 누락"

        log_analysis(
            ticker=ticker,
            market="domestic",
            collection={
                "price": bool(stock.get("price") or stock.get("current_price")),
                "fundamental": bool(fundamental.get("per") or fundamental.get("pbr")),
                "financials_annual": len(financials.get("annual", [])),
                "financials_quarterly": len(financials.get("quarterly", [])),
                "chart_days": len(chart),
                "news": len(news),
            },
            validation=validation,
            news_count=len(news),
            ai_success=ai_success,
            confidence=confidence,
            needs_review=needs_review,
            review_reason=review_reason,
        )

        return {
            "report": report,
            "fundamental": fundamental,
            "news": news,
            "financials": financials,
            "validation": validation,
            "confidence": confidence,
            "indicators": indicators,
        }
    except RuntimeError as e:
        error_msg = str(e)
        log_analysis(ticker=ticker, market="domestic",
                     collection={}, validation=validation,
                     news_count=len(news), ai_success=False,
                     confidence=confidence, error=error_msg,
                     needs_review=True, review_reason="RuntimeError")
        raise HTTPException(status_code=503, detail=error_msg)
    except Exception as e:
        error_msg = str(e)
        log_analysis(ticker=ticker, market="domestic",
                     collection={}, validation=validation,
                     news_count=len(news), ai_success=False,
                     confidence=confidence, error=error_msg)
        raise HTTPException(status_code=400, detail=error_msg)


@router.get("/domestic/{ticker}")
async def domestic_stock(ticker: str):
    try:
        return await get_stock_price(ticker)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/analyze/foreign/{ticker}")
async def analyze_foreign(ticker: str):
    news = []
    validation = None
    confidence = None
    try:
        stock, fundamental = get_foreign_stock_and_fundamental(ticker)
        name = stock.get("name", "")

        chart, news, financials = await asyncio.gather(
            asyncio.to_thread(get_foreign_chart, ticker),
            get_foreign_news(ticker, name),
            asyncio.to_thread(get_foreign_financials, ticker),
        )

        from app.services.technical import calc_indicators
        indicators = calc_indicators(chart) if chart else {}
        validation = validate_financials(financials, sector=fundamental.get("sector", ""), industry=fundamental.get("industry", ""))
        confidence = calculate_confidence(fundamental, financials, indicators, news, validation)
        fundamental["financials_text"] = format_financials_for_prompt(
            financials,
            is_financial=validation.get("is_financial", False),
            is_bio=validation.get("is_bio", False),
            is_semiconductor=validation.get("is_semiconductor", False),
        )
        fundamental["financials"] = financials
        fundamental["company_overview"] = fundamental.get("company_overview", "")

        report = await analyze_stock(stock, fundamental, chart, news,
                                     validation=validation, confidence=confidence)

        log_analysis(ticker=ticker, market="foreign",
                     collection={
                         "chart_days": len(chart),
                         "news": len(news),
                         "financials_annual": len(financials.get("annual", [])),
                         "financials_quarterly": len(financials.get("quarterly", [])),
                     },
                     validation=validation, news_count=len(news),
                     ai_success=True, confidence=confidence)

        return {
            "report": report,
            "fundamental": fundamental,
            "news": news,
            "financials": financials,
            "validation": validation,
            "confidence": confidence,
            "indicators": indicators,
        }
    except Exception as e:
        log_analysis(ticker=ticker, market="foreign",
                     collection={}, validation=None,
                     news_count=len(news), ai_success=False,
                     confidence=None, error=str(e))
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/chart/foreign/{ticker}")
def foreign_chart(ticker: str):
    try:
        return get_foreign_chart(ticker)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/foreign/{ticker}")
def foreign_stock(ticker: str):
    try:
        return get_foreign_stock(ticker)
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))
