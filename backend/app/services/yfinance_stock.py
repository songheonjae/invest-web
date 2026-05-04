import yfinance as yf


def get_foreign_stock(ticker: str) -> dict:
    stock = yf.Ticker(ticker)
    info = stock.info
    return {
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
