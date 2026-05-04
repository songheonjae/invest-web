def calc_indicators(chart: list[dict]) -> dict:
    if len(chart) < 2:
        return {}

    sorted_chart = sorted(chart, key=lambda x: x["date"])
    closes = [c["close"] for c in sorted_chart]
    volumes = [c["volume"] for c in sorted_chart]
    current = closes[-1]

    # ── EMA (중간 round 없음) ─────────────────────────────────────
    def ema(n, data=None):
        src = data if data is not None else closes
        if len(src) < n:
            return None
        k = 2 / (n + 1)
        val = sum(src[:n]) / n
        for p in src[n:]:
            val = p * k + val * (1 - k)
        return val

    # ── MA ────────────────────────────────────────────────────────
    def ma(n):
        if len(closes) < n:
            return None
        return sum(closes[-n:]) / n

    # ── RSI Wilder 방식 ───────────────────────────────────────────
    def rsi_wilder(n=14):
        if len(closes) < n + 1:
            return None
        gains, losses = [], []
        for i in range(1, len(closes)):
            diff = closes[i] - closes[i - 1]
            gains.append(max(diff, 0))
            losses.append(max(-diff, 0))
        # 초기 SMA
        avg_gain = sum(gains[:n]) / n
        avg_loss = sum(losses[:n]) / n
        # Wilder smoothing
        for i in range(n, len(gains)):
            avg_gain = (avg_gain * (n - 1) + gains[i]) / n
            avg_loss = (avg_loss * (n - 1) + losses[i]) / n
        if avg_loss == 0:
            return 100.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    # ── MACD 전체 시계열 계산 (근사치 제거) ───────────────────────
    def macd_full():
        if len(closes) < 35:
            return None, None, None
        k12, k26, k9 = 2 / 13, 2 / 27, 2 / 10

        # EMA12: SMA 초기화 후 index 25까지 갱신
        e12 = sum(closes[:12]) / 12
        for p in closes[12:26]:
            e12 = p * k12 + e12 * (1 - k12)

        # EMA26: SMA 초기화 (index 25)
        e26 = sum(closes[:26]) / 26

        # MACD 시계열: index 25부터
        macd_series = [e12 - e26]
        for p in closes[26:]:
            e12 = p * k12 + e12 * (1 - k12)
            e26 = p * k26 + e26 * (1 - k26)
            macd_series.append(e12 - e26)

        if len(macd_series) < 9:
            return macd_series[-1], None, None

        # Signal = EMA9 of MACD 시계열
        sig = sum(macd_series[:9]) / 9
        for m in macd_series[9:]:
            sig = m * k9 + sig * (1 - k9)

        macd_val = macd_series[-1]
        return macd_val, sig, macd_val - sig

    # ── 볼린저밴드 (population std, 폭 0 예외처리) ────────────────
    def bollinger(n=20, k=2):
        if len(closes) < n:
            return None, None, None
        recent = closes[-n:]
        mid = sum(recent) / n
        std = (sum((p - mid) ** 2 for p in recent) / (n - 1)) ** 0.5
        return mid - k * std, mid, mid + k * std

    # ── 52주 고저: 최근 252거래일 high/low ───────────────────────
    recent_252 = sorted_chart[-252:] if len(sorted_chart) >= 252 else sorted_chart
    week52_high = max(c.get("high", c["close"]) for c in recent_252)
    week52_low = min(c.get("low", c["close"]) for c in recent_252)
    gap_from_high = (current - week52_high) / week52_high * 100 if week52_high else None
    rebound_from_low = (current - week52_low) / week52_low * 100 if week52_low else None

    # ── 거래량: 이전 20거래일 평균 (오늘 제외) ────────────────────
    prev_vols = volumes[-21:-1]
    avg_vol = sum(prev_vols) / len(prev_vols) if prev_vols else 0
    vol_ratio = volumes[-1] / avg_vol if avg_vol else 0

    # ── 골든/데드 크로스 ─────────────────────────────────────────
    ma5 = ma(5)
    ma20 = ma(20)
    ma60 = ma(60)
    ma120 = ma(120)

    cross = None
    if ma5 is not None and ma20 is not None and len(closes) >= 21:
        prev_ma5 = sum(closes[-6:-1]) / 5
        prev_ma20 = sum(closes[-21:-1]) / 20
        if prev_ma5 < prev_ma20 and ma5 > ma20:
            cross = "골든크로스 (단기 상승 신호)"
        elif prev_ma5 > prev_ma20 and ma5 < ma20:
            cross = "데드크로스 (단기 하락 신호)"

    rsi14 = rsi_wilder(14)
    macd_val, signal_val, hist_val = macd_full()
    bb_lower, bb_mid, bb_upper = bollinger(20)

    # ── 볼린저 위치 ──────────────────────────────────────────────
    bb_position = None
    if bb_upper is not None and bb_lower is not None and bb_mid is not None:
        if current >= bb_upper:
            bb_position = f"상단 돌파 ({current:,.0f} ≥ {bb_upper:,.0f}) — 단기 과열"
        elif current <= bb_lower:
            bb_position = f"하단 이탈 ({current:,.0f} ≤ {bb_lower:,.0f}) — 과매도 가능"
        else:
            band_width = bb_upper - bb_lower
            if band_width > 0:
                pct = (current - bb_lower) / band_width * 100
                bb_position = f"밴드 내 {pct:.1f}% 위치 (하단:{bb_lower:,.0f} / 중간:{bb_mid:,.0f} / 상단:{bb_upper:,.0f})"
            else:
                bb_position = f"밴드 수렴 (상단=하단={bb_upper:,.0f})"

    # ── RSI 해석 ─────────────────────────────────────────────────
    rsi_comment = None
    if rsi14 is not None:
        r = round(rsi14, 2)
        if r >= 70:
            rsi_comment = f"{r} — 과매수 (조정 가능)"
        elif r <= 30:
            rsi_comment = f"{r} — 과매도 (반등 가능)"
        else:
            rsi_comment = f"{r} — 중립"

    # ── MACD 해석 ────────────────────────────────────────────────
    macd_comment = None
    if macd_val is not None and signal_val is not None:
        mv, sv = round(macd_val, 2), round(signal_val, 2)
        if mv > sv:
            macd_comment = f"MACD {mv} > Signal {sv} (상승 모멘텀)"
        else:
            macd_comment = f"MACD {mv} < Signal {sv} (하락 모멘텀)"

    # ── 추세 ─────────────────────────────────────────────────────
    trend = None
    if ma5 is not None and ma20 is not None and ma60 is not None:
        if ma5 > ma20 > ma60:
            trend = "정배열 (상승 추세)"
        elif ma5 < ma20 < ma60:
            trend = "역배열 (하락 추세)"
        else:
            trend = "혼조"

    return {
        "ma5": round(ma5, 2) if ma5 else None,
        "ma20": round(ma20, 2) if ma20 else None,
        "ma60": round(ma60, 2) if ma60 else None,
        "ma120": round(ma120, 2) if ma120 else None,
        "rsi": round(rsi14, 2) if rsi14 is not None else None,
        "rsi_comment": rsi_comment,
        "macd": round(macd_val, 2) if macd_val is not None else None,
        "macd_signal": round(signal_val, 2) if signal_val is not None else None,
        "macd_hist": round(hist_val, 2) if hist_val is not None else None,
        "macd_comment": macd_comment,
        "bb_lower": round(bb_lower, 2) if bb_lower is not None else None,
        "bb_mid": round(bb_mid, 2) if bb_mid is not None else None,
        "bb_upper": round(bb_upper, 2) if bb_upper is not None else None,
        "bb_position": bb_position,
        "trend": trend,
        "cross": cross,
        "vol_ratio": round(vol_ratio, 2),
        "vol_comment": f"오늘 거래량은 이전 20일 평균의 {vol_ratio:.2f}배",
        "gap_from_high": round(gap_from_high, 1) if gap_from_high is not None else None,
        "rebound_from_low": round(rebound_from_low, 1) if rebound_from_low is not None else None,
    }
