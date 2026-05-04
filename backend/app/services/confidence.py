def _to_float(val):
    if val is None:
        return None
    try:
        return float(str(val).replace(",", "").replace("%", "").strip())
    except (ValueError, TypeError):
        return None


def _has_val(d: dict, key: str) -> bool:
    """dict에서 key가 실질적인 값(0, 빈문자열, N/A 제외)을 가지면 True."""
    v = d.get(key)
    if not v:
        return False
    s = str(v).strip()
    if s in ("", "0", "N/A"):
        return False
    try:
        return float(s.replace(",", "")) != 0
    except (ValueError, TypeError):
        return False


def _detect_turnaround(annual: list, quarterly: list) -> bool:
    """적자 → 흑자 전환 감지 (확정 실적 기준)"""
    confirmed = [r for r in annual if "(E)" not in r.get("period", "")]
    if len(confirmed) >= 2:
        prev_op = _to_float(confirmed[-2].get("영업이익"))
        curr_op = _to_float(confirmed[-1].get("영업이익"))
        if prev_op is not None and curr_op is not None:
            if prev_op < 0 and curr_op > 0:
                return True
    # 분기 기준 보조 판정 (연간 데이터 부족 시)
    confirmed_q = [r for r in quarterly if "(E)" not in r.get("period", "")]
    if len(confirmed_q) >= 2:
        prev_q = _to_float(confirmed_q[-2].get("영업이익"))
        curr_q = _to_float(confirmed_q[-1].get("영업이익"))
        if prev_q is not None and curr_q is not None:
            if prev_q < 0 and curr_q > 0:
                return True
    return False


def _calc_reliability(fundamental: dict, financials: dict, news: list, validation: dict,
                      is_financial: bool = False, is_bio: bool = False,
                      is_semiconductor: bool = False) -> dict:
    """
    분석 신뢰도: 80점에서 감산
    "이 리포트를 얼마나 믿어도 되는가"
    - 데이터 완성도, 정합성, 추정치 비중, 뉴스 직접성
    - 차트/수급/밸류에이션은 포함하지 않음
    """
    import logging
    _log = logging.getLogger(__name__)

    score = 80
    details = []
    is_capped = False

    if is_financial:
        details.append("[금융주 모드] PER 부재 패널티 제거, PBR 중심 평가")
        _log.info("[_calc_reliability] 금융주 모드 활성화 — PBR 중심, PER 패널티 제거")
    if is_bio:
        details.append("[바이오 모드] 적자/추정치비중 패널티 완화, 뉴스 직접관련 기준 완화")
        _log.info("[_calc_reliability] 바이오/제약 모드 활성화 — 구조적 적자·E값 의존 정상 처리")
    if is_semiconductor:
        details.append("[반도체 모드] 사이클 특성 반영 — 추정치 비중·뉴스 패널티 완화")
        _log.info("[_calc_reliability] 반도체/디스플레이 모드 활성화 — 사이클 E값 의존 정상 처리")

    annual = financials.get("annual", [])
    quarterly = financials.get("quarterly", [])

    # ── validation 게이트 ────────────────────────────────────────
    issues = validation.get("issues", []) if validation else []
    severe_count = sum(1 for i in issues if i["severity"] == "severe")
    medium_count = sum(1 for i in issues if i["severity"] == "medium")
    light_count  = sum(1 for i in issues if i["severity"] == "light")

    if severe_count > 0:
        score = min(score, 20)
        is_capped = True
        details.append(f"🔴 심각한 데이터 오류 {severe_count}건 → 신뢰도 20점 상한 강제")
    else:
        penalty = min(25, medium_count * 12) + min(10, light_count * 4)
        if penalty:
            score -= penalty
            details.append(f"검증 경고: medium {medium_count}건(-{min(25,medium_count*12)}), light {light_count}건(-{min(10,light_count*4)})")

    # ── 핵심 데이터 누락 계층화 ──────────────────────────────────
    # 매우 중요
    if not annual:
        score -= 25
        details.append("연간 실적 없음 (-25, 핵심)")
    elif len([r for r in annual if "(E)" not in r.get("period", "")]) == 0:
        score -= 15
        details.append("확정 연간 실적 없음 (-15)")

    if not quarterly:
        score -= 10
        details.append("분기 실적 없음 (-10, 핵심)")

    if not fundamental.get("market_cap") and not fundamental.get("price"):
        score -= 12
        details.append("현재가/시총 없음 (-12, 핵심)")

    # 중요 — 금융주·바이오는 PER 부재가 정상
    if is_financial:
        if not _has_val(fundamental, "pbr"):
            score -= 3
            details.append("PBR 없음 (-3, 금융주 핵심 밸류에이션 지표)")
    elif is_bio:
        # 바이오: PER은 적자기업 특성상 의미 없음, PBR도 파이프라인 가치 미반영이라 참고용
        if not _has_val(fundamental, "pbr") and not _has_val(fundamental, "per"):
            score -= 2
            details.append("PER/PBR 모두 없음 (-2, 바이오 임상 단계 기업 특성)")
    else:
        if not _has_val(fundamental, "per") and not _has_val(fundamental, "pbr"):
            score -= 5
            details.append("PER/PBR 모두 없음 (-5)")

    # 추정치 비중 과다 — 바이오는 확정 실적이 적어 E값 의존이 정상
    all_periods = annual + quarterly
    if all_periods:
        est_ratio = sum(1 for r in all_periods if "(E)" in r.get("period", "")) / len(all_periods)
        if is_bio:
            if est_ratio > 0.8:
                score -= 4
                details.append(f"추정치 비중 {est_ratio*100:.0f}% (바이오 특성 고려, 완화 적용 -4)")
        elif is_semiconductor:
            if est_ratio > 0.8:
                score -= 4
                details.append(f"추정치 비중 {est_ratio*100:.0f}% (반도체 사이클 특성 고려, 완화 적용 -4)")
        else:
            if est_ratio > 0.5:
                score -= 8
                details.append(f"추정치 비중 {est_ratio*100:.0f}% 과다 (-8)")

    # 뉴스 직접 관련도 — 바이오는 임상/기술이전 뉴스가 희소해 A급 직접관련 없어도 과도 감점 방지
    a_direct = [n for n in news if n.get("grade") == "A" and n.get("relevance") == "직접관련"]
    if not a_direct:
        if is_bio:
            score -= 2
            details.append("A급 직접관련 뉴스 없음 (-2, 바이오 이벤트 희소성 반영)")
        elif is_semiconductor:
            score -= 3
            details.append("A급 직접관련 뉴스 없음 (-3, 반도체 공시/실적 중심 특성 반영)")
        else:
            score -= 5
            details.append("A급 직접관련 뉴스 없음 (-5)")

    score = max(0, min(80, score))

    return {
        "score": score,
        "max": 80,
        "is_capped": is_capped,
        "details": details,
        "label": (
            "높음" if score >= 65
            else "보통" if score >= 45
            else "낮음" if score >= 25
            else "매우 낮음"
        ),
    }


def _calc_attractiveness(fundamental: dict, financials: dict, indicators: dict,
                         news: list, is_turnaround: bool,
                         is_financial: bool = False, is_bio: bool = False,
                         is_semiconductor: bool = False) -> dict:
    """
    투자 매력도: 50점 기준 가감
    "지금 이 종목이 얼마나 매력적인가"
    - 실적 흐름, 밸류에이션, 뉴스 질, 수급/차트
    """
    score = 50
    breakdown = {}

    annual = financials.get("annual", [])
    quarterly = financials.get("quarterly", [])
    confirmed_a = [r for r in annual if "(E)" not in r.get("period", "")]
    confirmed_q = [r for r in quarterly if "(E)" not in r.get("period", "")]

    # ── 1. 실적 흐름 (-15 ~ +20) ─────────────────────────────────
    perf = 0
    perf_parts = []

    if is_bio:
        # 바이오: 구조적 적자 정상 → 적자 자체 패널티 최소화, 매출 성장·손실 속도 중심
        perf_parts.insert(0, "[바이오 모드]")
        if len(confirmed_a) >= 2:
            ops = [_to_float(r.get("영업이익")) for r in confirmed_a[-2:]]
            revs = [_to_float(r.get("매출액")) for r in confirmed_a[-2:]]
            if all(v is not None for v in ops):
                if ops[-1] > 0:
                    perf += 10; perf_parts.append("흑자 전환 달성")
                elif ops[-1] < 0 and ops[-2] < 0:
                    loss_rate = (ops[-1] - ops[-2]) / abs(ops[-2]) * 100
                    if loss_rate < -50:
                        perf -= 8; perf_parts.append(f"영업손실 {abs(loss_rate):.0f}% 확대(캐시번 가속)")
                    elif loss_rate < -20:
                        perf -= 4; perf_parts.append(f"영업손실 {abs(loss_rate):.0f}% 확대")
                    else:
                        perf -= 1; perf_parts.append("영업적자 지속(바이오 구조적 정상)")
            if all(v is not None for v in revs):
                if revs[-1] > revs[-2]:
                    perf += 6; perf_parts.append("매출 성장")
                elif revs[-1] < revs[-2] * 0.7:
                    perf -= 3; perf_parts.append("매출 30%+ 감소(기술이전 수익 종료 가능성)")

        if len(confirmed_q) >= 2:
            q_ops = [_to_float(r.get("영업이익")) for r in confirmed_q[-2:]]
            if all(v is not None for v in q_ops) and q_ops[-1] > 0:
                perf += 5; perf_parts.append("분기 흑자 달성")

    elif is_semiconductor:
        # 반도체: 업황 사이클 기반 해석 — 다운사이클 적자 패널티 완화
        perf_parts.insert(0, "[반도체 모드]")
        if len(confirmed_a) >= 2:
            ops = [_to_float(r.get("영업이익")) for r in confirmed_a[-2:]]
            revs = [_to_float(r.get("매출액")) for r in confirmed_a[-2:]]
            if all(v is not None for v in ops):
                if ops[-1] > 0 and ops[-2] > 0 and ops[-1] > ops[-2]:
                    perf += 10; perf_parts.append("연간 이익 성장(사이클 업턴)")
                elif ops[-1] > 0:
                    perf += 4; perf_parts.append("연간 흑자 유지")
                elif ops[-1] <= 0 and ops[-2] <= 0:
                    # 다운사이클 연속 적자는 일반(-12)보다 패널티 완화
                    perf -= 5; perf_parts.append("연간 적자 지속(다운사이클 가능성, 패널티 완화)")
                elif ops[-1] <= 0 and ops[-2] > 0:
                    perf -= 8; perf_parts.append("연간 적자 전환(다운사이클 진입 가능성)")
            if all(v is not None for v in revs):
                if revs[-1] > revs[-2]:
                    perf += 4; perf_parts.append("매출 성장")
                elif revs[-1] < revs[-2] * 0.7:
                    perf -= 3; perf_parts.append("매출 30%+ 감소(다운사이클)")
        if len(confirmed_q) >= 2:
            q_ops = [_to_float(r.get("영업이익")) for r in confirmed_q[-2:]]
            if all(v is not None for v in q_ops):
                if q_ops[-1] > q_ops[-2] and q_ops[-1] > 0:
                    perf += 6; perf_parts.append("분기 이익 우상향(업턴 확인)")
                elif q_ops[-1] > q_ops[-2]:
                    perf += 3; perf_parts.append("분기 손실 축소(회복 신호)")
                elif q_ops[-1] <= 0 and q_ops[-2] <= 0:
                    perf -= 3; perf_parts.append("분기 적자 지속(다운사이클)")

    elif is_turnaround:
        # 턴어라운드: 절대 성장률보다 흑자 지속성이 중요
        if len(confirmed_q) >= 2:
            recent_ops = [_to_float(r.get("영업이익")) for r in confirmed_q[-2:]]
            if all(v is not None for v in recent_ops):
                if all(v > 0 for v in recent_ops):
                    perf += 10
                    perf_parts.append("턴어라운드 후 분기 흑자 지속")
                    if recent_ops[-1] > recent_ops[-2]:
                        perf += 5
                        perf_parts.append("분기 이익 추가 성장")
                else:
                    perf -= 5
                    perf_parts.append("턴어라운드 불안정 (일부 분기 적자)")
        perf_parts.insert(0, "[턴어라운드 종목]")
    else:
        # 일반 종목
        if len(confirmed_a) >= 2:
            ops = [_to_float(r.get("영업이익")) for r in confirmed_a[-2:]]
            revs = [_to_float(r.get("매출액")) for r in confirmed_a[-2:]]
            if all(v is not None for v in ops):
                if ops[-1] > 0 and ops[-2] > 0 and ops[-1] > ops[-2]:
                    perf += 10; perf_parts.append("연간 이익 성장")
                elif ops[-1] > 0:
                    perf += 4; perf_parts.append("연간 흑자 유지")
                elif ops[-1] <= 0:
                    perf -= 12; perf_parts.append("연간 적자")
            if all(v is not None for v in revs) and revs[-1] > revs[-2]:
                perf += 4; perf_parts.append("매출 성장")

        if len(confirmed_q) >= 2:
            q_ops = [_to_float(r.get("영업이익")) for r in confirmed_q[-2:]]
            if all(v is not None for v in q_ops):
                if q_ops[-1] > q_ops[-2] and q_ops[-1] > 0:
                    perf += 6; perf_parts.append("분기 이익 우상향")
                elif q_ops[-1] <= 0:
                    perf -= 6; perf_parts.append("최근 분기 적자")

    perf = min(20, max(-15, perf))
    breakdown["실적 흐름"] = {"score": perf, "range": "-15~+20",
                              "reason": ", ".join(perf_parts) or "데이터 부족", "is_turnaround": is_turnaround}
    score += perf

    # ── 2. 밸류에이션 (-15 ~ +10) ────────────────────────────────
    val = 0
    val_parts = []
    per = _to_float(fundamental.get("per"))
    pbr = _to_float(fundamental.get("pbr"))

    if is_financial:
        # 금융주: PER 부재 정상, PBR이 핵심 지표
        val_parts.append("[금융주 밸류에이션 모드]")
        if per is not None and per > 0:
            if per < 10:
                val += 4; val_parts.append(f"PER {per}(금융주 저PER)")
            elif per < 15:
                val += 1; val_parts.append(f"PER {per}(금융주 적정)")
            else:
                val_parts.append(f"PER {per}(금융주 참고)")
        # PBR: 금융주 핵심 밸류에이션 — 가중치 상향
        if pbr is not None:
            if pbr < 0.5:
                val += 8; val_parts.append(f"PBR {pbr}(심층 저평가)")
            elif pbr < 1.0:
                val += 5; val_parts.append(f"PBR {pbr}(자산 이하, 저평가)")
            elif pbr < 1.5:
                val += 2; val_parts.append(f"PBR {pbr}(적정)")
            elif pbr >= 3:
                val -= 5; val_parts.append(f"PBR {pbr}(고PBR, 부담)")
        else:
            val -= 2; val_parts.append("PBR 없음(금융주 핵심 지표 미확인)")
    elif is_semiconductor:
        # 반도체: 고PER은 사이클 회복 기대치 반영 가능 → 패널티 완화
        val_parts.append("[반도체 밸류에이션 모드]")
        if per is not None and per > 0:
            if per < 15:
                val += 7; val_parts.append(f"PER {per}(저PER, 사이클 저점 가능성)")
            elif per < 25:
                val += 3; val_parts.append(f"PER {per}(적정)")
            elif per < 50:
                val_parts.append(f"PER {per}(사이클 회복 기대 반영 가능, 확인 필요)")
            else:
                val -= 3; val_parts.append(f"PER {per}(고PER, 회복 기대 과도 반영 주의)")
        elif per is None:
            val -= 2; val_parts.append("PER 없음")
        else:
            val -= 3; val_parts.append(f"PER {per}(적자, 다운사이클 가능성)")
        if pbr is not None:
            if pbr < 1:
                val += 5; val_parts.append(f"PBR {pbr}(자산 이하, 다운사이클 저평가)")
            elif pbr < 2:
                val += 2; val_parts.append(f"PBR {pbr}(양호)")
            elif pbr >= 4:
                val -= 4; val_parts.append(f"PBR {pbr}(부담)")
    elif is_bio:
        # 바이오: PER 적자/부재 정상, 고PBR도 파이프라인 가치 반영으로 해석 주의
        val_parts.append("[바이오 밸류에이션 모드]")
        if per is not None and per > 0:
            val_parts.append(f"PER {per}(바이오 참고, 흑자 기업)")
            if per < 30:
                val += 3
            elif per > 100:
                val -= 2; val_parts.append("(PER 100배 초과, 고평가 주의)")
        # PER 없음·음수: 적자 바이오 정상, 패널티 없음
        elif per is None:
            val_parts.append("PER N/A(바이오 적자 기업 정상)")
        # PBR: 파이프라인 가치가 장부에 반영 안 되므로 고PBR도 일부 정상
        if pbr is not None:
            if pbr < 1:
                val += 5; val_parts.append(f"PBR {pbr}(자산 이하, 파이프라인 저평가 가능)")
            elif pbr < 5:
                val_parts.append(f"PBR {pbr}(바이오 적정 범위)")
            elif pbr < 10:
                val -= 2; val_parts.append(f"PBR {pbr}(고PBR, 성장 기대치 높음)")
            else:
                val -= 5; val_parts.append(f"PBR {pbr}(매우 고PBR, 고위험)")
        else:
            val_parts.append("PBR 없음(바이오 파이프라인 가치 직접 평가 필요)")
    else:
        if per is not None:
            if per <= 0:
                if is_turnaround:
                    val -= 2; val_parts.append(f"PER {per}(턴어라운드 구간 왜곡, 감점 완화)")
                else:
                    val -= 6; val_parts.append(f"PER {per}(적자/음수)")
            elif per < 10:
                val += 7; val_parts.append(f"PER {per}(저평가)")
            elif per < 20:
                val += 3; val_parts.append(f"PER {per}(적정)")
            elif per < 35:
                val_parts.append(f"PER {per}(다소 높음)")
            else:
                if is_turnaround:
                    val -= 3; val_parts.append(f"PER {per}(고PER, 턴어라운드 성장 기대 반영 가능)")
                else:
                    val -= 8; val_parts.append(f"PER {per}(고평가)")
        else:
            val -= 2; val_parts.append("PER 없음")

        if pbr is not None:
            if pbr < 1:
                val += 5; val_parts.append(f"PBR {pbr}(자산 이하)")
            elif pbr < 2:
                val += 2; val_parts.append(f"PBR {pbr}(양호)")
            elif pbr >= 4:
                val -= 4; val_parts.append(f"PBR {pbr}(부담)")

    val = min(10, max(-15, val))
    breakdown["밸류에이션"] = {"score": val, "range": "-15~+10", "reason": ", ".join(val_parts) or "데이터 없음"}
    score += val

    # ── 3. 뉴스 직접성/질 (-10 ~ +15) ───────────────────────────
    a_direct = [n for n in news if n.get("grade") == "A" and n.get("relevance") == "직접관련"]
    a_indirect = [n for n in news if n.get("grade") == "A" and n.get("relevance") != "직접관련"]
    pos_direct = sum(1 for n in a_direct if n.get("sentiment") == "긍정")
    neg_direct = sum(1 for n in a_direct if n.get("sentiment") == "부정")

    news_score = min(15, pos_direct * 5) - min(10, neg_direct * 6)
    news_score += min(5, len(a_indirect) * 2)
    news_score = min(15, max(-10, news_score))
    news_reason = f"A급 직접관련 긍정 {pos_direct}건/부정 {neg_direct}건, A급 간접 {len(a_indirect)}건"
    breakdown["뉴스 직접성"] = {"score": news_score, "range": "-10~+15", "reason": news_reason}
    score += news_score

    # ── 4. 수급/차트 (-10 ~ +10) ─────────────────────────────────
    chart_score = 0
    chart_parts = []

    if not indicators:
        chart_score -= 3; chart_parts.append("차트 데이터 없음")
    else:
        trend = indicators.get("trend", "")
        if "정배열" in trend:
            chart_score += 5; chart_parts.append("정배열")
        elif "역배열" in trend:
            chart_score -= 5; chart_parts.append("역배열")

        rsi = _to_float(indicators.get("rsi"))
        if rsi is not None:
            if rsi > 70:
                chart_score -= 4; chart_parts.append(f"RSI {rsi} 과매수")
            elif rsi < 30:
                chart_score += 3; chart_parts.append(f"RSI {rsi} 과매도")
            elif 40 <= rsi <= 60:
                chart_score += 2; chart_parts.append(f"RSI {rsi} 중립")

        vol_ratio = _to_float(indicators.get("vol_ratio"))
        if vol_ratio is not None:
            if vol_ratio > 1.5:
                chart_score += 2; chart_parts.append(f"거래량 {vol_ratio}배")
            elif vol_ratio < 0.5:
                chart_score -= 2; chart_parts.append(f"거래량 저조 {vol_ratio}배")

    chart_score = min(10, max(-10, chart_score))
    breakdown["수급/차트"] = {"score": chart_score, "range": "-10~+10",
                              "reason": ", ".join(chart_parts) or "해석 불가"}
    score += chart_score

    return {
        "score": min(100, max(0, score)),
        "max": 100,
        "breakdown": breakdown,
        "is_turnaround": is_turnaround,
        "label": (
            "매력적" if score >= 65
            else "보통" if score >= 45
            else "비매력적" if score >= 30
            else "주의"
        ),
    }


def calculate_confidence(
    fundamental: dict,
    financials: dict,
    indicators: dict,
    news: list,
    validation: dict,
) -> dict:
    annual = financials.get("annual", [])
    quarterly = financials.get("quarterly", [])
    is_turnaround = _detect_turnaround(annual, quarterly)
    is_financial = validation.get("is_financial", False) if validation else False
    is_bio = validation.get("is_bio", False) if validation else False
    is_semiconductor = validation.get("is_semiconductor", False) if validation else False

    reliability = _calc_reliability(fundamental, financials, news, validation,
                                    is_financial=is_financial, is_bio=is_bio,
                                    is_semiconductor=is_semiconductor)
    attractiveness = _calc_attractiveness(fundamental, financials, indicators, news, is_turnaround,
                                          is_financial=is_financial, is_bio=is_bio,
                                          is_semiconductor=is_semiconductor)

    # 하위 호환: 기존 total/breakdown 유지
    combined = round((reliability["score"] / 80 * 50) + (attractiveness["score"] / 100 * 50))

    return {
        "total": combined,
        "reliability": reliability,
        "attractiveness": attractiveness,
        "is_turnaround": is_turnaround,
        # 하위 호환용
        "breakdown": attractiveness["breakdown"],
    }
