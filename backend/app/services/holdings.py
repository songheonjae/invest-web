"""
보유 판단 서비스

종목 분석 데이터를 수집하고 평균 매수가·보유 수량을 합쳐 보유 판단을 결정한다.
판단은 코드가 먼저 결정하고 Claude는 설명만 작성한다.
"""
import asyncio
import logging

from app.services.financials import (
    get_financials, validate_financials, get_company_overview, format_financials_for_prompt
)
from app.services.confidence import calculate_confidence
from app.services.technical import calc_indicators
from app.services.recommender.pipeline import _calc_timing_score

logger = logging.getLogger(__name__)

BUY_REASON_LABELS = {
    "rebound":    "반등 기대",
    "turnaround": "실적 회복",
    "long":       "장기 보유",
    "value":      "저평가",
    "growth":     "성장 기대",
    "other":      "기타",
}

RISK_ORDER = {
    "계속 보유 가능": 0,
    "조건부 보유": 1,
    "일부 비중 축소 고려": 2,
    "정리 고려": 3,
}


def _apply_min_risk_level(current: str, minimum: str) -> str:
    """현재 판단보다 더 보수적인 쪽으로만 조정."""
    if RISK_ORDER.get(minimum, 0) > RISK_ORDER.get(current, 0):
        return minimum
    return current


# ──────────────────────────────────────────────────────────────
# 종목 분석 데이터 수집 (국내/해외 분기)
# ──────────────────────────────────────────────────────────────
async def build_stock_context(ticker: str, market: str = "domestic") -> dict:
    """
    종목 분석에 필요한 모든 데이터를 수집해 반환한다.
    종목 분석 탭과 보유 판단 탭이 모두 이 결과를 재사용할 수 있다.
    """
    if market == "foreign":
        return await _build_foreign_context(ticker)
    return await _build_domestic_context(ticker)


async def _build_domestic_context(ticker: str) -> dict:
    from app.services.kis_fundamental import get_price_and_fundamental
    from app.services.kis_chart import get_daily_chart
    from app.services.stock_search import KRX_STOCKS

    (stock, fundamental), financials, company_overview = await asyncio.gather(
        get_price_and_fundamental(ticker),
        get_financials(ticker),
        get_company_overview(ticker),
    )

    ticker_to_name = {t: n for n, t in KRX_STOCKS}
    name = stock.get("name") or fundamental.get("name") or ticker_to_name.get(ticker, ticker)
    if not stock.get("name"):
        stock["name"] = name

    try:
        chart = await get_daily_chart(ticker, "D")
    except Exception:
        chart = []

    indicators = calc_indicators(chart) if chart else {}
    validation = validate_financials(
        financials,
        sector=fundamental.get("sector", ""),
        industry=fundamental.get("industry", ""),
    )
    confidence = calculate_confidence(fundamental, financials, indicators, [], validation)

    timing_score, timing_flags, timing_summary = 0.0, [], ""
    if indicators:
        timing_score, timing_flags, timing_summary = _calc_timing_score(indicators)

    current_price = None
    try:
        price_raw = stock.get("price", "") or ""
        current_price = int(str(price_raw).replace(",", "")) or None
    except (ValueError, TypeError):
        pass

    return {
        "stock": stock, "fundamental": fundamental, "financials": financials,
        "indicators": indicators, "validation": validation, "confidence": confidence,
        "timing_score": timing_score, "timing_flags": timing_flags,
        "timing_summary": timing_summary, "current_price": current_price, "name": name,
    }


async def _build_foreign_context(ticker: str) -> dict:
    from app.services.foreign_analysis import (
        get_foreign_stock_and_fundamental, get_foreign_chart, get_foreign_financials
    )

    stock, fundamental = await asyncio.to_thread(get_foreign_stock_and_fundamental, ticker)
    chart, financials = await asyncio.gather(
        asyncio.to_thread(get_foreign_chart, ticker),
        asyncio.to_thread(get_foreign_financials, ticker),
    )

    name = stock.get("name", ticker)
    indicators = calc_indicators(chart) if chart else {}
    validation = validate_financials(
        financials,
        sector=fundamental.get("sector", ""),
        industry=fundamental.get("industry", ""),
    )
    confidence = calculate_confidence(fundamental, financials, indicators, [], validation)

    timing_score, timing_flags, timing_summary = 0.0, [], ""
    if indicators:
        timing_score, timing_flags, timing_summary = _calc_timing_score(indicators)

    current_price = None
    try:
        price_raw = stock.get("price", "") or ""
        # 해외는 USD → KRW 환산 없이 원가 그대로 사용 (사용자도 USD 기준 입력)
        current_price = int(float(str(price_raw).replace(",", ""))) or None
    except (ValueError, TypeError):
        pass

    return {
        "stock": stock, "fundamental": fundamental, "financials": financials,
        "indicators": indicators, "validation": validation, "confidence": confidence,
        "timing_score": timing_score, "timing_flags": timing_flags,
        "timing_summary": timing_summary, "current_price": current_price, "name": name,
    }


# ──────────────────────────────────────────────────────────────
# 포지션 계산
# ──────────────────────────────────────────────────────────────
def _calc_position(avg_buy_price: int, quantity: int, current_price: int) -> dict:
    invested    = avg_buy_price * quantity
    evaluation  = current_price * quantity
    profit_loss = evaluation - invested
    profit_rate = round(((current_price - avg_buy_price) / avg_buy_price) * 100, 2)
    return {
        "invested_amount":   invested,
        "evaluation_amount": evaluation,
        "profit_loss":       profit_loss,
        "profit_rate":       profit_rate,
    }


def _position_summary(profit_rate: float) -> str:
    sign = "+" if profit_rate >= 0 else ""
    if profit_rate >= 20:
        return f"평균 매수가보다 {sign}{profit_rate:.1f}% 높은 상태로, 수익 여유가 있는 포지션입니다."
    if profit_rate >= 5:
        return f"평균 매수가보다 {sign}{profit_rate:.1f}% 높은 상태로, 비교적 안정적인 포지션입니다."
    if profit_rate >= -5:
        return f"평균 매수가와 현재가가 거의 비슷한 상태({sign}{profit_rate:.1f}%)로, 큰 손익 부담은 없습니다."
    if profit_rate >= -15:
        return f"평균 매수가보다 {profit_rate:.1f}% 낮은 상태로, 포지션 부담이 조금 있습니다."
    if profit_rate >= -25:
        return f"평균 매수가보다 {profit_rate:.1f}% 낮은 상태로, 손실 부담이 있는 포지션입니다."
    return f"평균 매수가보다 {profit_rate:.1f}% 낮아, 손실 부담이 상당한 포지션입니다."


# ──────────────────────────────────────────────────────────────
# 판단 결정 로직
# ──────────────────────────────────────────────────────────────
def _judge_decision(
    ctx: dict,
    avg_buy_price: int,
    profit_rate: float,
    buy_reason: str,
    horizon: str,
) -> tuple[str, list[str], list[str]]:
    """
    점수 기반 판단 → decision 결정.
    Returns (decision, reasons, warnings)
    """
    validation   = ctx["validation"] or {}
    confidence   = ctx["confidence"] or {}
    indicators   = ctx["indicators"] or {}
    timing_score = ctx["timing_score"]
    timing_flags = set(ctx["timing_flags"])
    financials   = ctx["financials"] or {}
    fundamental  = ctx["fundamental"] or {}

    score = 0
    reasons: list[str]  = []
    warnings: list[str] = []

    def _f(val):
        try:
            return float(str(val).replace(",", "").replace("%", "").strip()) if val else None
        except (ValueError, TypeError):
            return None

    # ── 하드룰: validation severe ─────────────────────────────
    if validation.get("max_severity") == "severe":
        warnings.append(
            "재무 데이터 검증에서 심각한 문제가 감지되었습니다. "
            "현재 공개된 정보만으로는 편하게 보유하기 어렵습니다."
        )
        score -= 3

    # ── 포지션 부담 ──────────────────────────────────────────
    if profit_rate >= 20:
        score += 2
        reasons.append(f"현재 수익률이 +{profit_rate:.1f}%로 수익 여유가 있는 상태입니다.")
    elif profit_rate >= 5:
        score += 1
        reasons.append(f"현재 수익률이 +{profit_rate:.1f}%로 비교적 안정적인 상태입니다.")
    elif profit_rate >= -5:
        reasons.append(
            f"현재 수익률은 {'+' if profit_rate>=0 else ''}{profit_rate:.1f}%로 "
            "큰 손익 부담은 없는 상태입니다."
        )
    elif profit_rate >= -15:
        score -= 1
        reasons.append(f"현재 수익률이 {profit_rate:.1f}%로 포지션 부담이 있는 상태입니다.")
    elif profit_rate >= -25:
        score -= 2
        if profit_rate <= -20:
            warnings.append(
                f"현재 수익률이 {profit_rate:.1f}%로, "
                "단순 변동을 넘어 포지션 재점검이 필요한 손실 구간입니다."
            )
        else:
            warnings.append(f"현재 수익률이 {profit_rate:.1f}%로 손실 부담이 큰 상태입니다.")
    else:
        score -= 3
        warnings.append(
            f"현재 수익률이 {profit_rate:.1f}%로, "
            "단순 변동을 넘어 포지션 재점검이 필요한 손실 구간입니다."
        )

    # ── 신뢰도 ────────────────────────────────────────────────
    rel_score = confidence.get("reliability", {}).get("score", 50)
    att_score = confidence.get("attractiveness", {}).get("score", 50)

    if rel_score >= 60:
        score += 1
        reasons.append("재무 데이터 신뢰도가 높아 분석 기반이 안정적입니다.")
    elif rel_score < 30:
        score -= 1
        warnings.append("재무 데이터 신뢰도가 낮아 분석에 한계가 있습니다.")

    # ── 타이밍 ───────────────────────────────────────────────
    if timing_score >= 4:
        score += 1
    elif timing_score <= -4:
        score -= 1
        warnings.append("기술적 지표상 단기 하락 압력이 있습니다.")

    rsi = indicators.get("rsi")
    if rsi is not None and rsi >= 75:
        score -= 1
        warnings.append(
            f"RSI가 {rsi:.0f}로 단기 과매수 구간입니다. 단기 조정 가능성에 주의가 필요합니다."
        )

    trend = indicators.get("trend", "")
    if "역배열" in trend:
        score -= 1
        warnings.append("현재 주가가 주요 이동평균선 아래에 있어 상승 흐름이 약합니다.")
    elif "정배열" in trend:
        score += 1

    # ── 매수 이유별 판단 ──────────────────────────────────────
    confirmed_q = [r for r in financials.get("quarterly", []) if "(E)" not in r.get("period", "")]
    confirmed_a = [r for r in financials.get("annual", [])    if "(E)" not in r.get("period", "")]

    if buy_reason == "rebound":
        if timing_score >= 2:
            score += 1
            reasons.append("차트 반등 흐름이 어느 정도 이어지고 있어 매수 이유가 아직 살아 있습니다.")
        elif timing_score <= -3:
            score -= 1
            warnings.append(
                "반등 기대로 매수했지만, 현재 기술적 흐름이 약해 매수 이유 재확인이 필요합니다."
            )

    elif buy_reason == "turnaround":
        if len(confirmed_q) >= 2:
            prev_op = _f(confirmed_q[-2].get("영업이익"))
            curr_op = _f(confirmed_q[-1].get("영업이익"))
            if prev_op is not None and curr_op is not None:
                if curr_op > prev_op:
                    score += 1
                    reasons.append("최근 분기 영업이익이 개선되고 있어 실적 회복 흐름이 이어지고 있습니다.")
                elif curr_op < prev_op:
                    score -= 1
                    warnings.append(
                        "최근 분기 영업이익이 꺾였습니다. "
                        "실적 회복 기대가 약해졌는지 확인이 필요합니다."
                    )

    elif buy_reason == "long":
        if len(confirmed_a) >= 2 and rel_score >= 50:
            score += 1
            reasons.append("장기 보유 관점에서 데이터 기반이 안정적입니다.")
        if validation.get("max_severity") in ("ok", "light", None):
            score += 1

    elif buy_reason == "value":
        per = _f(fundamental.get("per"))
        pbr = _f(fundamental.get("pbr"))
        if per is not None and per > 0:
            if per < 10:
                score += 1
                reasons.append(f"PER이 {per:.1f}배로 이익 대비 주가 부담이 낮은 편입니다.")
            elif per > 25:
                score -= 1
                warnings.append(f"PER이 {per:.1f}배로 저평가로 보기 어려운 수준일 수 있습니다.")
        if pbr is not None and 0 < pbr < 1:
            score += 1
            reasons.append(f"PBR이 {pbr:.2f}배로 장부가치 이하 구간입니다.")

    elif buy_reason == "growth":
        growth_confirmed = False
        # 연간 매출·영업이익 개선 확인
        if len(confirmed_a) >= 2:
            rev_p = _f(confirmed_a[-2].get("매출액"))
            rev_c = _f(confirmed_a[-1].get("매출액"))
            op_p  = _f(confirmed_a[-2].get("영업이익"))
            op_c  = _f(confirmed_a[-1].get("영업이익"))
            if rev_c and rev_p and rev_c > rev_p:
                score += 1
                growth_confirmed = True
                reasons.append("최근 연간 매출이 증가해 성장 흐름이 이어지고 있습니다.")
            elif op_c and op_p and op_c > op_p and op_c > 0:
                score += 1
                growth_confirmed = True
                reasons.append("최근 연간 영업이익이 개선되어 성장 기대가 뒷받침되고 있습니다.")
        # 연간 데이터 부족 시 분기로 보조 확인
        if not growth_confirmed and len(confirmed_q) >= 2:
            rev_p = _f(confirmed_q[-2].get("매출액"))
            rev_c = _f(confirmed_q[-1].get("매출액"))
            op_p  = _f(confirmed_q[-2].get("영업이익"))
            op_c  = _f(confirmed_q[-1].get("영업이익"))
            if rev_c and rev_p and rev_c > rev_p:
                growth_confirmed = True
                reasons.append("최근 분기 매출이 개선되어 성장 기대가 아직 유효합니다.")
            elif op_c and op_p and op_c > op_p:
                growth_confirmed = True
                reasons.append("최근 분기 영업이익이 개선되어 성장 흐름이 이어지고 있습니다.")
        if not growth_confirmed:
            score -= 1
            warnings.append(
                "성장 기대가 유지되는지는 실제 실적에서 매출과 영업이익이 개선되는지 확인해야 합니다. "
                "현재는 손실 폭이 커서, 기대만으로 버티기보다는 숫자 확인이 필요합니다."
            )

    # ── 추가 리스크 ───────────────────────────────────────────
    if "BB과열" in timing_flags or "RSI과매수" in timing_flags:
        warnings.append("단기 과열 신호가 있어, 수익 중이라면 일부 비중 축소를 고려할 수 있습니다.")

    if "데드크로스" in timing_flags:
        warnings.append(
            "데드크로스(단기 이동평균이 장기 이동평균 아래로 꺾임) 신호가 발생했습니다."
        )

    # ── 최종 판단 ─────────────────────────────────────────────
    # severe hard cap: score를 -1 이하로 강제
    if validation.get("max_severity") == "severe" and score > -1:
        score = -1

    if score >= 3:
        decision = "계속 보유 가능"
    elif score >= 1:
        decision = "조건부 보유"
    elif score >= -1:
        decision = "일부 비중 축소 고려"
    else:
        decision = "정리 고려"

    # ── 투자 기간별 손실 보정 (최종 판단 후 적용) ───────────────
    if horizon == "short" and profit_rate <= -20:
        warnings.insert(0, "단기 투자 기준으로는 손실 폭이 커져 기존 계획을 다시 점검해야 합니다.")
        decision = _apply_min_risk_level(decision, "일부 비중 축소 고려")
    elif horizon == "short" and profit_rate <= -15:
        warnings.insert(0, "단기 투자 기준으로 손실 부담이 커진 상태입니다.")
        decision = _apply_min_risk_level(decision, "일부 비중 축소 고려")
    elif horizon == "medium" and profit_rate <= -25:
        warnings.insert(0, "중기 투자 기준에서도 손실 부담이 큰 구간입니다.")
        decision = _apply_min_risk_level(decision, "일부 비중 축소 고려")
    elif horizon == "long" and profit_rate <= -30:
        warnings.insert(0, "장기 보유라도 손실 폭이 커져 투자 논리 재점검이 필요합니다.")
        decision = _apply_min_risk_level(decision, "조건부 보유")

    return decision, reasons[:3], warnings[:4]


# ──────────────────────────────────────────────────────────────
# 조건 생성
# ──────────────────────────────────────────────────────────────
def _build_conditions(
    ctx: dict,
    decision: str,
    buy_reason: str,
    horizon: str,
    profit_rate: float,
) -> tuple[list[str], list[str], list[str], list[str]]:
    indicators = ctx["indicators"] or {}
    timing_flags = set(ctx["timing_flags"])
    validation   = ctx["validation"] or {}

    hold_conditions:   list[str] = []
    reduce_conditions: list[str] = []
    exit_conditions:   list[str] = []
    check_first:       list[str] = []

    trend = indicators.get("trend", "")
    in_downtrend = "역배열" in trend

    # 보유 유지 조건
    if buy_reason in ("turnaround", "growth"):
        hold_conditions.append("다음 실적에서 매출과 영업이익 개선이 이어질 때")
    if buy_reason == "rebound":
        if in_downtrend:
            hold_conditions.append(
                "현재 주가가 주요 이동평균선 아래에 있습니다. "
                "평균선 위로 회복하는지 확인이 필요합니다."
            )
        else:
            hold_conditions.append("주가가 주요 이동평균선 위를 유지하고 반등 흐름이 이어질 때")
    if buy_reason == "long":
        hold_conditions.append("연간 실적 흐름이 안정적으로 유지될 때")
    if buy_reason == "value":
        hold_conditions.append("PER/PBR이 현재 수준을 유지하며 실적이 뒷받침될 때")
    hold_conditions.append("리스크 이벤트가 추가로 악화되지 않을 때")
    if not in_downtrend:
        hold_conditions.append("주가가 주요 이동평균선 위를 유지할 때")

    # 비중 축소 조건
    if profit_rate >= 10:
        reduce_conditions.append("수익이 나고 있지만 단기 과열 신호가 강해질 때")
    reduce_conditions.append("처음 매수한 이유가 약해지거나 실적 흐름이 꺾일 때")
    reduce_conditions.append("시장 전체 변동성이 높아지거나 외부 리스크가 커질 때")

    # 정리 조건
    exit_conditions.append("처음 매수한 핵심 이유가 완전히 깨졌을 때")
    if indicators.get("ma60") or indicators.get("ma120"):
        exit_conditions.append("주요 지지선(60일 또는 120일 이동평균)을 명확히 이탈할 때")
    if validation.get("max_severity") == "severe":
        exit_conditions.append("재무 데이터 검증 이슈가 해소되지 않을 때")
    else:
        exit_conditions.append("재무 데이터 검증에서 심각한 이슈가 새로 발생할 때")

    # 먼저 확인할 것
    if buy_reason in ("turnaround", "growth", "long"):
        check_first.append("다음 분기 실적에서 매출과 영업이익이 개선되는지")
    if in_downtrend:
        check_first.append(
            "현재 주가가 주요 이동평균선 아래에 있습니다. 평균선 위로 회복하는지 확인해야 합니다."
        )
    else:
        check_first.append("주가가 주요 이동평균선 위를 유지하는지")
    check_first.append("관련 리스크 이벤트가 추가로 악화되는지")

    return (
        hold_conditions[:3],
        reduce_conditions[:3],
        exit_conditions[:3],
        check_first[:3],
    )


# ──────────────────────────────────────────────────────────────
# Claude 설명 리포트
# ──────────────────────────────────────────────────────────────
async def _claude_holding_report(
    decision: str, ctx: dict,
    avg_buy_price: int, quantity: int,
    profit_loss: int, profit_rate: float,
    position_summary: str,
    reasons: list[str], hold_conditions: list[str],
    reduce_conditions: list[str], exit_conditions: list[str],
    check_first: list[str], warnings: list[str],
    buy_reason: str, horizon: str,
) -> str:
    from app.services.claude_analysis import client as _claude_client

    name          = ctx["name"]
    current_price = ctx["current_price"]
    timing_summary = ctx["timing_summary"]
    horizon_label  = {"short": "단기(3개월 이내)", "medium": "중기(3~6개월)", "long": "장기(1년 이상)"}.get(horizon, horizon)
    buy_reason_label = BUY_REASON_LABELS.get(buy_reason, "기타")

    big_loss_short = (
        profit_rate <= -20
        and horizon == "short"
        and decision in ("조건부 보유", "일부 비중 축소 고려")
    )
    extra_instruction = ""
    if big_loss_short:
        extra_instruction = (
            f"\n[특별 주의] 단기 투자에서 손실 폭이 {profit_rate:.1f}%로 큰 상태다. "
            "decision이 '조건부 보유'라도 리포트에서 반드시 이 문장을 포함해라: "
            "'조건부 보유이지만, 단기 투자 기준으로는 손실 폭이 커진 상태입니다. "
            "계속 들고 가려면 다음 실적과 주가 회복 신호가 반드시 확인되어야 합니다.'"
        )

    system = (
        "너는 보유 중인 주식에 대해 보유 판단 결과를 설명하는 역할만 한다.\n"
        "독자는 주식 초보자 또는 대학생이다.\n"
        "decision, 수익률, 현재가, 평균 매수가, 보유 수량은 절대 바꾸지 마라.\n"
        "'무조건 팔아라', '무조건 들고 있어라'처럼 단정하지 마라. 조건부 판단으로 설명하라.\n"
        "초보 투자자가 이해할 수 있게 쉽게 쓰되, 숫자 근거는 유지하라.\n"
        "매수 추천, 수익 보장 표현은 절대 금지.\n"
        f"한국어로 작성하라.{extra_instruction}"
    )

    profit_str = f"+{profit_rate:.1f}%" if profit_rate >= 0 else f"{profit_rate:.1f}%"
    pl_str     = f"+{profit_loss:,}원" if profit_loss >= 0 else f"{profit_loss:,}원"

    user_msg = f"""[보유 현황]
종목: {name}
평균 매수가: {avg_buy_price:,}원 | 현재가: {current_price:,}원 | 보유 수량: {quantity}주
평가 손익: {pl_str} | 수익률: {profit_str}
투자 기간: {horizon_label} | 매수 이유: {buy_reason_label}

[포지션 요약]
{position_summary}

[알고리즘 판단 결과 — 이 값을 바꾸지 마라]
보유 판단: {decision}

[판단 근거]
{chr(10).join(f'- {r}' for r in reasons) or '- 데이터 기반 판단'}

[경고]
{chr(10).join(f'- {w}' for w in warnings) if warnings else '없음'}

[보유 유지 조건]
{chr(10).join(f'- {c}' for c in hold_conditions)}

[비중 축소 조건]
{chr(10).join(f'- {c}' for c in reduce_conditions)}

[정리 조건]
{chr(10).join(f'- {c}' for c in exit_conditions)}

[먼저 확인할 것]
{chr(10).join(f'{i+1}. {c}' for i, c in enumerate(check_first))}

[기술적 흐름]
{timing_summary or '데이터 없음'}

위 데이터를 그대로 사용해서 아래 형식으로 보유 판단 설명을 작성하라.
보유 판단({decision})은 변경 불가. 수치도 변경 불가.

## 내 포지션 상태
(1~2문장. 현재 포지션 상태를 쉽게 설명)

## 왜 이렇게 판단했나요?
(판단 근거를 2~3문장으로 쉽게 설명. 없는 근거 추가 금지)

## 계속 보유하려면
(hold_conditions를 자연스럽게 bullets로 표현)

## 비중을 줄이는 걸 고려할 때
(reduce_conditions를 자연스럽게 bullets로 표현)

## 정리를 고려할 때
(exit_conditions를 자연스럽게 bullets로 표현)

## 먼저 확인할 것
(check_first를 번호 목록으로)

이 결과는 판단 보조 도구이며, 투자 결정의 책임은 투자자 본인에게 있습니다."""

    try:
        msg = await _claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=1500,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return msg.content[0].text
    except Exception:
        logger.exception("보유 판단 Claude 리포트 생성 실패")
        return ""


# ──────────────────────────────────────────────────────────────
# 메인 함수
# ──────────────────────────────────────────────────────────────
async def judge_holding(
    ticker: str,
    avg_buy_price: int,
    quantity: int,
    horizon: str = "medium",
    buy_reason: str = "other",
    market: str = "domestic",
) -> dict:
    """
    보유 판단 메인 함수.
    종목 분석 데이터를 수집하고 보유 판단 결과를 반환한다.
    """
    ctx = await build_stock_context(ticker, market)

    current_price = ctx["current_price"]
    if not current_price:
        raise ValueError(f"현재가를 가져올 수 없습니다: {ticker}")

    pos              = _calc_position(avg_buy_price, quantity, current_price)
    profit_rate      = pos["profit_rate"]
    position_summary = _position_summary(profit_rate)

    decision, reasons, warnings = _judge_decision(
        ctx, avg_buy_price, profit_rate, buy_reason, horizon
    )

    hold_cond, reduce_cond, exit_cond, check_first = _build_conditions(
        ctx, decision, buy_reason, horizon, profit_rate
    )

    report = await _claude_holding_report(
        decision=decision, ctx=ctx,
        avg_buy_price=avg_buy_price, quantity=quantity,
        profit_loss=pos["profit_loss"], profit_rate=profit_rate,
        position_summary=position_summary,
        reasons=reasons, hold_conditions=hold_cond,
        reduce_conditions=reduce_cond, exit_conditions=exit_cond,
        check_first=check_first, warnings=warnings,
        buy_reason=buy_reason, horizon=horizon,
    )

    validation = ctx["validation"] or {}
    confidence = ctx["confidence"] or {}

    return {
        "ticker":            ticker,
        "name":              ctx["name"],
        "current_price":     current_price,
        "avg_buy_price":     avg_buy_price,
        "quantity":          quantity,
        "invested_amount":   pos["invested_amount"],
        "evaluation_amount": pos["evaluation_amount"],
        "profit_loss":       pos["profit_loss"],
        "profit_rate":       profit_rate,
        "decision":          decision,
        "position_summary":  position_summary,
        "reasons":           reasons,
        "hold_conditions":   hold_cond,
        "reduce_conditions": reduce_cond,
        "exit_conditions":   exit_cond,
        "check_first":       check_first,
        "warnings":          warnings,
        "timing_score":      ctx["timing_score"],
        "timing_summary":    ctx["timing_summary"],
        "report":            report,
        "validation_severity": validation.get("max_severity"),
        "confidence_score":  confidence.get("reliability", {}).get("score"),
    }
