"""
종목 배치 테스트 스크립트 (AI 분석 없음 — 비용 0)
실행: python test_batch.py

확인 항목:
- 주가/재무지표 수집 성공 여부
- 실적 파싱 (연간/분기 데이터)
- validation 결과 (severe/medium/light/ok)
- 기술지표 계산
- needs_review 플래그
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.services.kis_fundamental import get_price_and_fundamental
from app.services.financials import get_financials, validate_financials
from app.services.kis_chart import get_daily_chart
from app.services.technical import calc_indicators

# ── 카테고리별 30개 종목 ─────────────────────────────────────────
TICKERS = [
    # 대형 우량주 (6)
    ("삼성전자",       "005930", "대형주"),
    ("SK하이닉스",     "000660", "대형주"),
    ("현대차",         "005380", "대형주"),
    ("POSCO홀딩스",    "005490", "대형주"),
    ("KB금융",         "105560", "대형주"),
    ("NAVER",          "035420", "대형주"),

    # 적자 기업 (5)
    ("한국전력",       "015760", "적자"),
    ("아시아나항공",   "020560", "적자"),
    ("카카오",         "035720", "적자"),
    ("롯데케미칼",     "011170", "적자"),
    ("HLB",            "028300", "적자"),

    # 턴어라운드 (4)
    ("두산에너빌리티", "034020", "턴어라운드"),
    ("HMM",            "011200", "턴어라운드"),
    ("에코프로",       "086520", "턴어라운드"),
    ("한화솔루션",     "009830", "턴어라운드"),

    # 바이오/헬스 (4)
    ("삼성바이오로직스","207940", "바이오"),
    ("셀트리온",       "068270", "바이오"),
    ("유한양행",       "000100", "바이오"),
    ("알테오젠",       "196170", "바이오"),

    # 게임/엔터 (3)
    ("크래프톤",       "259960", "게임"),
    ("엔씨소프트",     "036570", "게임"),
    ("하이브",         "352820", "엔터"),

    # 건설/중공업 (3)
    ("삼성중공업",     "010140", "중공업"),
    ("한화에어로스페이스","012450","방산"),
    ("현대건설",       "000720", "건설"),

    # 소재/화학 (3)
    ("LG화학",         "051910", "소재"),
    ("고려아연",       "010130", "소재"),
    ("금호석유",       "011780", "소재"),

    # 소형/변동성 (2)
    ("위메이드",       "112040", "소형"),
    ("데브시스터즈",   "194480", "소형"),
]


async def test_ticker(ticker: str, name: str, category: str) -> dict:
    result = {
        "name": name,
        "ticker": ticker,
        "category": category,
        "price": False,
        "fundamental": False,
        "annual": 0,
        "quarterly": 0,
        "validation": "ok",
        "val_issues": 0,
        "rsi": None,
        "ma120": None,
        "needs_review": False,
        "review_reason": [],
        "error": None,
    }
    try:
        # 주가 + 재무지표
        (stock, fundamental), financials, chart = await asyncio.gather(
            get_price_and_fundamental(ticker),
            get_financials(ticker),
            get_daily_chart(ticker, "D"),
        )

        result["price"] = bool(stock.get("price"))
        result["fundamental"] = bool(fundamental.get("per") or fundamental.get("pbr") or fundamental.get("market_cap"))
        result["annual"] = len(financials.get("annual", []))
        result["quarterly"] = len(financials.get("quarterly", []))

        # validation
        val = validate_financials(financials)
        result["validation"] = val.get("max_severity", "ok")
        result["val_issues"] = len(val.get("issues", []))

        # 기술지표
        if chart:
            ind = calc_indicators(chart)
            result["rsi"] = ind.get("rsi")
            result["ma120"] = ind.get("ma120")

        # needs_review 판단
        reasons = []
        if result["validation"] == "severe":
            reasons.append("severe validation")
        if result["annual"] == 0 and result["quarterly"] == 0:
            reasons.append("실적 데이터 없음")
        if not result["price"]:
            reasons.append("주가 수집 실패")
        if result["rsi"] is None and chart:
            reasons.append("RSI 계산 불가")

        result["needs_review"] = len(reasons) > 0
        result["review_reason"] = reasons

    except Exception as e:
        result["error"] = str(e)[:60]
        result["needs_review"] = True
        result["review_reason"] = [f"에러: {str(e)[:40]}"]

    return result


def print_results(results: list[dict]):
    print("\n" + "=" * 100)
    print(f"{'종목':<14} {'카테고리':<10} {'주가':^4} {'재무':^4} {'연간':^4} {'분기':^4} {'validation':<8} {'RSI':>6} {'MA120':>8} {'검토필요'}")
    print("-" * 100)

    needs_review = []
    for r in results:
        price_mark = "✅" if r["price"] else "❌"
        fund_mark  = "✅" if r["fundamental"] else "⚠️"
        review_mark = "🔴 " + ", ".join(r["review_reason"]) if r["needs_review"] else ""
        val_display = {"severe": "🔴severe", "medium": "🟡medium", "light": "🔵light", "ok": "✅ok"}.get(r["validation"], r["validation"])
        rsi_str = f"{r['rsi']:.1f}" if r["rsi"] is not None else "N/A"
        ma120_str = f"{int(r['ma120']):,}" if r["ma120"] is not None else "N/A"

        print(f"{r['name']:<14} {r['category']:<10} {price_mark:^6} {fund_mark:^6} {r['annual']:^4} {r['quarterly']:^4} {val_display:<10} {rsi_str:>6} {ma120_str:>9}  {review_mark}")

        if r["needs_review"]:
            needs_review.append(r)

    print("=" * 100)
    print(f"\n총 {len(results)}개 종목 | needs_review: {len(needs_review)}개\n")

    if needs_review:
        print("── 검토 필요 케이스 ──────────────────────────")
        for r in needs_review:
            print(f"  {r['name']} ({r['ticker']}) [{r['category']}]")
            for reason in r["review_reason"]:
                print(f"    → {reason}")
            if r["error"]:
                print(f"    에러: {r['error']}")
        print()


async def main():
    print(f"배치 테스트 시작 — {len(TICKERS)}개 종목 (AI 분석 없음)")
    print("KIS rate limit으로 종목당 약 3~4초 소요, 총 약 2분 예상\n")

    results = []
    for i, (name, ticker, category) in enumerate(TICKERS, 1):
        print(f"[{i:02d}/{len(TICKERS)}] {name} ({ticker})...", end=" ", flush=True)
        r = await test_ticker(ticker, name, category)
        status = "🔴 needs_review" if r["needs_review"] else "✅"
        print(status)
        results.append(r)
        await asyncio.sleep(1.2)  # KIS rate limit

    print_results(results)

if __name__ == "__main__":
    asyncio.run(main())
