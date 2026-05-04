"""
기술지표 검증 스크립트
실행: python test_technical.py

출력값을 TradingView에서 같은 종목 같은 날짜로 비교하세요.
허용 오차: RSI ±0.5, MACD ±소폭 (데이터 길이 차이로 미세 오차 가능)
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from app.services.kis_chart import get_daily_chart
from app.services.technical import calc_indicators


async def verify(ticker: str, name: str):
    print(f"\n{'='*50}")
    print(f"  {name} ({ticker})")
    print(f"{'='*50}")

    chart = await get_daily_chart(ticker, "D")
    if not chart:
        print("  ❌ 차트 데이터 없음")
        return

    chart_sorted = sorted(chart, key=lambda x: x["date"])
    last = chart_sorted[-1]
    print(f"  기준일: {last['date']}  종가: {last['close']:,}")
    print(f"  사용 캔들: {len(chart)}개")

    ind = calc_indicators(chart)

    print(f"\n  [이동평균]")
    print(f"  MA5  = {ind.get('ma5')}")
    print(f"  MA20 = {ind.get('ma20')}")
    print(f"  MA60 = {ind.get('ma60')}")
    print(f"  MA120= {ind.get('ma120')}")

    print(f"\n  [RSI]")
    print(f"  RSI(14) = {ind.get('rsi')}  ← TradingView RSI(14)와 비교")

    print(f"\n  [MACD]  (12, 26, 9)")
    print(f"  MACD   = {ind.get('macd')}")
    print(f"  Signal = {ind.get('macd_signal')}")
    print(f"  Hist   = {ind.get('macd_hist')}  ← TradingView MACD Histogram과 비교")

    print(f"\n  [볼린저밴드]  (20일, 2σ)")
    print(f"  Upper  = {ind.get('bb_upper')}")
    print(f"  Mid    = {ind.get('bb_mid')}")
    print(f"  Lower  = {ind.get('bb_lower')}  ← TradingView BB(20,2)와 비교")

    print(f"\n  [52주 고저]")
    print(f"  고점 대비: {ind.get('gap_from_high')}%")
    print(f"  저점 대비: +{ind.get('rebound_from_low')}%")


TICKERS = [
    ("005930", "삼성전자"),
    ("000660", "SK하이닉스"),
    ("005490", "POSCO홀딩스"),
    ("035420", "NAVER"),
    ("051910", "LG화학"),
]

async def main():
    for ticker, name in TICKERS:
        await verify(ticker, name)
        await asyncio.sleep(0.6)  # KIS rate limit 방지

if __name__ == "__main__":
    asyncio.run(main())
