import httpx

BASE = "http://localhost:8000"


def test_domestic(ticker="000660", label="SK하이닉스"):
    print(f"\n=== 국내 ({label} {ticker}) ===")
    r = httpx.get(f"{BASE}/stock/analyze/domestic/{ticker}", timeout=120)
    d = r.json()
    print("last_reported_quarter:", d["fundamental"].get("last_reported_quarter"))
    print("next_quarter         :", d["fundamental"].get("next_quarter"))
    print("industry             :", d["fundamental"].get("industry"))
    print("trading_value        :", d["fundamental"].get("investor_trend", {}).get("trading_value"))
    print("\n--- report 앞 500자 ---")
    print(d["report"][:500])


def test_foreign(ticker="AAPL", label="Apple"):
    print(f"\n=== 해외 ({label} {ticker}) ===")
    r = httpx.get(f"{BASE}/stock/analyze/foreign/{ticker}", timeout=120)
    d = r.json()
    print("last_reported_quarter:", d["fundamental"].get("last_reported_quarter"))
    print("next_quarter         :", d["fundamental"].get("next_quarter"))
    print("earnings_date        :", d["fundamental"].get("earnings_date"))
    print("industry             :", d["fundamental"].get("industry"))
    print("\n--- report 앞 500자 ---")
    print(d["report"][:500])


if __name__ == "__main__":
    test_domestic("000660", "SK하이닉스")
    test_domestic("068270", "셀트리온")   # 바이오 업종 규칙 확인
    test_foreign("AAPL", "Apple")
