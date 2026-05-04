"""
해외 주식 재무 데이터 단위 sanity check
실행: python test_foreign_financials.py

검증 대상 (최근 연매출 상식값):
- AAPL: ~$391B → USD M으로 391,000M
- MSFT: ~$245B → USD M으로 245,000M
- NVDA: ~$130B → USD M으로 130,000M
- JPM:  ~$162B → USD M으로 162,000M (은행은 revenue 정의 다를 수 있음)
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from app.services.foreign_analysis import get_foreign_financials

TARGETS = [
    ("AAPL",  "Apple",      380_000, 420_000),
    ("MSFT",  "Microsoft",  220_000, 270_000),
    ("NVDA",  "NVIDIA",     100_000, 160_000),
    ("JPM",   "JPMorgan",   140_000, 180_000),
]

def main():
    print(f"\n{'종목':<8} {'이름':<12} {'최근 연매출 (USD M)':>20} {'기대 범위':>25} {'판정'}")
    print("-" * 80)
    for ticker, name, lo, hi in TARGETS:
        data = get_foreign_financials(ticker)
        annual = data.get("annual", [])
        unit = data.get("unit", "?")
        if not annual:
            print(f"{ticker:<8} {name:<12} {'데이터 없음':>20} {f'{lo:,}~{hi:,}':>25} ❌")
            continue
        latest = annual[-1]
        rev = latest.get("매출액")
        op = latest.get("영업이익")
        op_margin = latest.get("영업이익률")
        period = latest.get("period", "?")

        if rev is None:
            verdict = "❌ 매출 없음"
        elif lo <= rev <= hi:
            verdict = "✅ 정상"
        elif rev * 1000 >= lo:
            verdict = "🔴 단위 1000배 차이 (나누기 부족?)"
        elif rev / 1000 >= lo / 1000:
            verdict = "🔴 단위 1/1000 차이 (너무 많이 나눔?)"
        else:
            verdict = f"⚠️ 범위 벗어남 ({rev:,.0f})"

        print(f"{ticker:<8} {name:<12} {f'{rev:,.0f}':>20} {f'{lo:,}~{hi:,}':>25} {verdict}")
        print(f"         └ {period} | 영업이익: {op:,.0f if op else 'N/A'} | OPM: {op_margin}% | unit={unit}")

    print()

if __name__ == "__main__":
    main()
