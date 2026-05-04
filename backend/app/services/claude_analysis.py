import os
import logging
import anthropic
from dotenv import load_dotenv
from .technical import calc_indicators

load_dotenv()
client = anthropic.AsyncAnthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
logger = logging.getLogger(__name__)

# 업종별 적정 PER 범위 (KIS bstp_kor_isnm 기준, 한국 시장)
SECTOR_PER_MAP = {
    # KIS 전기·전자 계열 (삼성전자, SK하이닉스 등)
    "전기": (12, 35),
    "반도체": (13, 35),
    "디스플레이": (10, 25),
    "소프트웨어": (20, 40),
    "IT": (18, 45),
    "인터넷": (22, 50),
    # 금융
    "은행": (4, 9),
    "보험": (6, 12),
    "증권": (6, 14),
    "금융": (5, 12),
    # 바이오/헬스
    "바이오": (30, 100),
    "의약품": (20, 50),
    "의료기기": (25, 60),
    "헬스케어": (25, 60),
    "제약": (20, 50),
    # 소비재
    "유통": (10, 20),
    "식품": (14, 25),
    "화장품": (20, 40),
    "섬유": (8, 18),
    # 자동차/운송
    "운수장비": (6, 14),
    "자동차": (6, 14),
    "항공": (9, 20),
    "운수": (8, 18),
    "조선": (10, 25),
    # 소재/에너지
    "철강": (7, 14),
    "화학": (8, 18),
    "정유": (6, 14),
    "에너지": (8, 18),
    "비철금속": (8, 18),
    # 건설/중공업
    "건설": (7, 15),
    "기계": (10, 20),
    "방위": (15, 30),
    # 게임/엔터/미디어
    "게임": (18, 45),
    "엔터": (20, 50),
    "미디어": (15, 35),
    "방송": (12, 30),
    # 통신/유틸리티
    "통신": (9, 18),
    "전기가스": (9, 20),
    # 영문 업종명 (yfinance 해외 주식)
    "Technology": (20, 45),
    "Healthcare": (20, 55),
    "Financial": (8, 15),
    "Consumer Cyclical": (14, 30),
    "Consumer Defensive": (14, 25),
    "Communication": (15, 35),
    "Energy": (8, 18),
    "Industrials": (12, 25),
    "Basic Materials": (8, 18),
    "Real Estate": (15, 30),
    "Utilities": (12, 22),
    "Semiconductor": (15, 40),
    "Software": (22, 50),
    "Biotechnology": (30, 100),
    "Pharmaceutical": (18, 45),
}


def _get_sector_per_range(sector: str, industry: str = "") -> tuple[int, int] | None:
    """industry 우선 매칭 → sector fallback → None."""
    import re
    def _clean(s: str) -> str:
        return re.sub(r"[^\w가-힣a-zA-Z]", "", s).lower()

    def _match(text: str) -> tuple[int, int] | None:
        if not text:
            return None
        cleaned = _clean(text)
        for key, rng in SECTOR_PER_MAP.items():
            if _clean(key) in cleaned:
                return rng
        return None

    return _match(industry) or _match(sector)


# SECTOR_PER_MAP 키워드와 동일한 체계를 공유 — 두 곳이 따로 놀지 않도록
SECTOR_TYPE_KEYWORDS: dict[str, list[str]] = {
    "financial":    ["bank", "financ", "insurance", "은행", "보험", "증권", "금융",
                     "Financial"],
    "bio":          ["bio", "pharma", "drug", "바이오", "제약", "의약", "의료기기", "헬스케어",
                     "Biotechnology", "Pharmaceutical", "Healthcare"],
    "semiconductor":["반도체", "디스플레이", "Semiconductor"],
    "growth":       ["software", "saas", "cloud", "소프트웨어", "인터넷", "플랫폼", "게임",
                     "IT", "Software", "Technology"],
}


def _detect_sector_type(sector: str, industry: str) -> str:
    """금융/바이오/반도체/성장주/일반 분류. SECTOR_TYPE_KEYWORDS 단일 소스."""
    combined = (sector + " " + industry).lower()
    for stype, keywords in SECTOR_TYPE_KEYWORDS.items():
        if any(k.lower() in combined for k in keywords):
            return stype
    return "general"


def _sector_rules(sector_type: str) -> str:
    if sector_type == "financial":
        return (
            "[업종 특수 규칙 — 금융주]\n"
            "- 영업이익률 규칙 적용 금지. 금융주는 이자수익/비이자수익 구조가 다르다.\n"
            "- 핵심 지표: ROE, NIM(순이자마진), 대손비율, BIS비율. 이 항목이 없으면 '확인 필요'로 명시.\n"
            "- PBR이 밸류에이션 1순위 지표다. PER은 유효할 때만 보조 지표로 참고해라.\n"
            "- PER이 0 또는 N/A이면 직접 계산하거나 추산하지 마라. 'PER: 데이터 없음 — PBR 기준으로 판단'으로 표기해라.\n"
            "- 고PBR(2배 초과)은 금융주에서 부담이나, 수익성 개선 추세와 함께 해석해라."
        )
    if sector_type == "bio":
        return (
            "[업종 특수 규칙 — 바이오/제약]\n"
            "- 영업적자는 임상 단계 기업의 일반적 특성이므로 'severe' 단정 금지.\n"
            "- 핵심 판단 기준: 파이프라인 임상 단계, 기술이전 실적, 현금 소진율(runway).\n"
            "- 매출 없는 적자도 임상 진행 중이면 중립으로 해석해라.\n"
            "- PER 의미 없음(적자). PBR과 파이프라인 가치 중심으로 판단해라.\n"
            "- 임상 결과·허가·기술이전 뉴스가 가장 중요한 이벤트다."
        )
    if sector_type == "semiconductor":
        return (
            "[업종 특수 규칙 — 반도체/디스플레이]\n"
            "- 업황 사이클(업턴/다운턴)을 최우선 맥락으로 언급해라.\n"
            "- E값 급등은 사이클 회복 반영일 수 있으므로 파싱 오류로 단정하지 마라.\n"
            "- 핵심 지표: 재고 수준, 수요처(모바일/서버/PC) 비중, CAPEX 방향성.\n"
            "- PER이 높아도 저점 대비 실적 개선 추세가 확인되면 사이클 매수 구간일 수 있음을 언급해라."
        )
    if sector_type == "growth":
        return (
            "[업종 특수 규칙 — 성장주/소프트웨어]\n"
            "- 고PER 자체를 고평가로 단정하지 마라. 성장률이 밸류에이션을 정당화할 수 있다.\n"
            "- 핵심 지표: 매출 성장률, 영업 레버리지(마진 개선 추세), 반복매출(구독/라이선스) 비중.\n"
            "- 적자라도 매출 고성장 + 마진 개선이면 긍정적 신호로 해석해라.\n"
            "- PEG(PER/성장률) 관점에서 해석하되, 데이터가 없으면 성장률만으로 언급해라."
        )
    return ""


def _sanitize(text: str, max_len: int = 500) -> str:
    """스크래핑 텍스트에서 HTML 태그·과도한 공백 제거 후 길이 제한."""
    if not text:
        return ""
    import re
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:max_len]


def build_prompt(stock: dict, fundamental: dict, chart: list, news: list,
                 validation: dict | None = None, confidence: dict | None = None,
                 sector_naver_per: float | None = None) -> tuple[str, str]:
    indicators = calc_indicators(chart) if chart else {}
    candle_count = len(chart) if chart else 0
    price_raw = stock.get("price", "")
    price = str(price_raw)

    def fmt(v, suffix=""):
        if v is None:
            return "N/A"
        if isinstance(v, float):
            return f"{v:,.2f}{suffix}"
        if isinstance(v, int):
            return f"{v:,}{suffix}"
        return f"{v}{suffix}"

    # ── 신뢰도 기반 언어 강도 ────────────────────────────────────
    rel_score = confidence["reliability"]["score"] if confidence else 80
    if rel_score < 25:
        tone_rule = (
            "⛔ 신뢰도 매우 낮음 (25점 미만) — 강제 규칙:\n"
            "- 한줄 결론에서 '사도 됨/사지 마' 출력 금지. 반드시 '⛔ 데이터 부족 — 투자 판단 불가'로 출력해라.\n"
            "- 모든 섹션에서 확신형 표현('좋습니다', '주목할 만합니다', '매수 고려') 금지.\n"
            "- 시나리오·조언 섹션은 '신뢰할 수 있는 데이터 확보 후 재분석 권장'으로 대체해라."
        )
    elif rel_score < 45:
        tone_rule = (
            "⚠️ 신뢰도 낮음 (45점 미만) — 언어 강도 규칙:\n"
            "- 한줄 결론 앞에 반드시 '⚠️ 제한된 데이터 기준:'을 붙여라.\n"
            "- '사도 됩니다', '매수 추천' 등 강한 매수 표현 금지. '가능성 있으나 추가 확인 필요'로 완화해라.\n"
            "- 각 섹션 결론마다 '(추가 검증 권장)' 표시를 붙여라."
        )
    elif rel_score < 65:
        tone_rule = (
            "ℹ️ 신뢰도 보통 (65점 미만):\n"
            "- 불확실한 항목에 '(확인 필요)' 또는 '(추정)' 표시를 붙여라.\n"
            "- 강한 확신 표현은 반드시 근거와 함께 제시해라."
        )
    else:
        tone_rule = "✅ 신뢰도 충분: 데이터 근거 기반으로 명확하게 분석해라."

    # ── 업종 감지 ─────────────────────────────────────────────
    sector = fundamental.get("sector", "")
    industry = fundamental.get("industry", "")
    sector_type = _detect_sector_type(sector, industry)
    sector_rule = _sector_rules(sector_type)
    sector_per_range = _get_sector_per_range(sector, industry)
    if sector_naver_per:
        sector_per_str = f"네이버 업종 평균 PER: {sector_naver_per:.1f}"
        if sector_per_range:
            sector_per_str += f" | 역사적 참고 범위: {sector_per_range[0]}~{sector_per_range[1]}"
    elif sector_per_range:
        sector_per_str = f"업종 적정 PER 참고 범위: {sector_per_range[0]}~{sector_per_range[1]} ({sector})"
    else:
        sector_per_str = "업종 PER 기준 없음 (업종 미분류)"

    # ── 기술적 지표 (6번 수정: 기준 명시) ────────────────────────
    tech_lines = []
    if indicators:
        i = indicators
        ma20, ma60 = i.get("ma20"), i.get("ma60")
        price_val = price_raw if isinstance(price_raw, (int, float)) else None
        if price_val is not None and ma20 is not None and ma60 is not None:
            ma_status = f"현재가 MA20({'위' if price_val > ma20 else '아래'}) / MA60({'위' if price_val > ma60 else '아래'})"
        elif price_val is not None and ma20 is not None:
            ma_status = f"현재가 MA20({'위' if price_val > ma20 else '아래'})"
        else:
            ma_status = f"MA5={fmt(i.get('ma5'))} / MA20={fmt(ma20)} / MA60={fmt(ma60)} / MA120={fmt(i.get('ma120'))}"

        gap = i.get("gap_from_high")
        rebound = i.get("rebound_from_low")
        gap_str = f"{gap}%" if gap is not None else "N/A"
        rebound_str = f"+{rebound}%" if rebound is not None else "N/A"

        tech_lines += [
            f"※ 일봉 종가 기준 | 사용 캔들: {candle_count}개 | RSI: 14거래일 | MACD: 12/26/9 | BB: 20일 2σ",
            f"- 추세: {i.get('trend') or 'N/A'}",
            f"- 이동평균: {ma_status}",
            f"- RSI(14): {i.get('rsi_comment') or 'N/A'}",
            f"- MACD: {i.get('macd_comment') or 'N/A'}",
            f"- 볼린저밴드: {i.get('bb_position') or 'N/A'}",
            f"- 거래량: {i.get('vol_comment') or 'N/A'}",
            f"- 크로스 신호: {i.get('cross') or '없음'}",
            f"- 52주 고점 대비: {gap_str} / 저점 대비: {rebound_str}",
        ]
    tech_text = "\n".join(tech_lines) if tech_lines else "데이터 없음"

    # ── 뉴스 (3번 수정: n.get('title')) ──────────────────────────
    relevance_order = {"직접관련": 0, "간접관련": 1, "무관": 2}
    sorted_news = sorted(news, key=lambda n: relevance_order.get(n.get("relevance", "간접관련"), 1))
    news_lines = []
    for idx, n in enumerate(sorted_news, 1):
        relevance = n.get("relevance", "간접관련")
        grade = n.get("grade", "B")
        # 제외 기준: C급 전체 | 무관 전체
        if grade == "C" or relevance == "무관":
            continue
        sentiment_emoji = {"긍정": "🟢", "부정": "🔴", "중립": "🟡"}.get(n.get("sentiment", ""), "⚪")
        relevance_mark = "★" if relevance == "직접관련" else ("△" if relevance == "간접관련" else "✕")
        title = _sanitize(n.get("title", ""), max_len=150)
        summary = _sanitize(n.get("summary") or n.get("body", ""), max_len=80) or "(본문 없음)"
        news_lines.append(
            f"[{relevance_mark}{relevance}|{grade}급|{sentiment_emoji}{n.get('sentiment','중립')}] {title}\n"
            f"({n.get('source','')} {n.get('date','')}) {summary}"
        )
    news_text = "\n\n".join(news_lines) if news_lines else "뉴스 없음"

    # ── 데이터 부족 경고 (AI에게 명확히 알림) ────────────────────
    data_warnings = []
    if len(news_lines) == 0:
        data_warnings.append("⚠️ 뉴스 0건 — 뉴스/이벤트 섹션은 '수집된 뉴스 없음'으로 명시하고 시장 분위기 판단을 보류해라.")
    elif len(news_lines) <= 2:
        data_warnings.append(f"⚠️ 뉴스 {len(news_lines)}건 — 뉴스가 매우 적어 시장 분위기 판단에 한계가 있음을 명시해라.")
    if candle_count < 60:
        data_warnings.append(f"⚠️ 차트 데이터 {candle_count}개 — 기술지표 신뢰도가 낮음. RSI/MACD 해석 시 '데이터 부족으로 참고용'임을 명시해라.")
    if not indicators:
        data_warnings.append("⚠️ 기술지표 계산 불가 — 차트 섹션 전체를 '데이터 없음'으로 처리해라.")
    financials_raw = fundamental.get("financials_text", "")
    if "실적 데이터 없음" in financials_raw or not financials_raw:
        data_warnings.append("⚠️ 실적 데이터 없음 — 실적 기반 판단을 하지 말고 '실적 확인 불가'로 명시해라.")
    elif "연간 실적" not in financials_raw:
        data_warnings.append("⚠️ 연간 실적 없음 — 분기 데이터만으로 연간 추세 판단을 하지 마라.")
    elif "최근 분기" not in financials_raw:
        data_warnings.append("⚠️ 분기 실적 없음 — 최근 실적 흐름 판단에 한계가 있음을 명시해라.")
    per = fundamental.get("per", "")
    pbr = fundamental.get("pbr", "")

    preferred_note = fundamental.get("preferred_stock_note", "")
    if preferred_note:
        data_warnings.append(
            f"ℹ️ [우선주] {preferred_note} "
            "PER/PBR이 N/A이면 밸류에이션 수치 없이 고/저평가를 단정하지 마라. "
            "보정된 수치라면 '보통주 재무 데이터 기준으로 보정 계산한 값'임을 리포트에 명시해라."
        )

    if sector_type == "semiconductor":
        data_warnings.append(
            "ℹ️ [반도체/디스플레이] 재고 수준·수요처(모바일/서버/PC) 비중·CAPEX 방향성 데이터 없음 — "
            "업황 사이클 판단에 한계가 있습니다. 관련 항목은 '업황 정보 미확인'으로 명시하고, "
            "공식 IR·실적발표 자료 확인을 권장해라."
        )

    if sector_type == "financial":
        # 금융주: PER 부재 정상, PBR만 필수
        if not pbr:
            data_warnings.append("⚠️ [금융주] PBR 없음 — PBR이 핵심 밸류에이션 지표. PER은 참고용이며 부재여도 무방.")
        elif not per:
            data_warnings.append("ℹ️ [금융주] PER 없음 — 금융주는 PBR·ROE 중심으로 평가. PER 부재는 정상.")
        # 금융주 매출 비표준 경고 (표에서 비표준 표기와 연동)
        data_warnings.append("ℹ️ [금융주] 매출액 = 영업수익(이자+비이자). 영업이익률은 비표준 개념으로 비교 주의.")
    else:
        if not per and not pbr:
            data_warnings.append("⚠️ PER/PBR 없음 — 밸류에이션 수치 없이 고/저평가를 단정하지 마라.")

    # E값 현황 경고
    estimate_periods = validation.get("estimate_periods", []) if validation else []
    e_issue_severe = False  # if 블록 밖에서 초기화 (summary에서 참조)
    if estimate_periods:
        e_str = ", ".join(estimate_periods[:4])
        if validation:
            for iss in validation.get("issues", []):
                msg = iss.get("message", "")
                if any(ep in msg for ep in estimate_periods) and iss["severity"] in ("severe", "medium"):
                    e_issue_severe = True
                    break
        if e_issue_severe:
            data_warnings.append(
                f"🔴 추정치(E) 신뢰도 낮음: {e_str} — 검증 경고 발생. "
                "이 기간 수치 인용 전면 금지. 시나리오에도 쓰지 마라."
            )
        else:
            data_warnings.append(
                f"ℹ️ 추정치(E) 포함: {e_str} — "
                "핵심 이유·좋은점·걱정되는점·한줄결론 근거로 사용 금지. "
                "시나리오·밸류에이션·조언에서만 '(증권사 추정 기준)' 표기 후 참고 가능."
            )
    else:
        data_warnings.append(
            "ℹ️ 추정치(E) 없음 — 미래 전망은 생략하거나, "
            "확정 시계열이 충분할 때만 제한적으로 언급해라. "
            "시계열이 부족하면 '판단 보류'로 처리해라."
        )

    data_warning_text = "\n".join(data_warnings) if data_warnings else "이상 없음"

    name = stock.get("name") or fundamental.get("name") or stock.get("ticker", "")

    # 5번 수정: 기업 개요 sanitize
    overview_section = _sanitize(fundamental.get("company_overview", ""), max_len=500) or "정보 없음"

    # ── 수급 ─────────────────────────────────────────────────────
    supply_lines = []
    investor = fundamental.get("investor_trend", {})
    if investor:
        for label, key in [("외국인", "foreign"), ("기관", "institution")]:
            d5 = investor.get(f"{key}_5d")
            d20 = investor.get(f"{key}_20d")
            if d5 is not None or d20 is not None:
                supply_lines.append(f"- {label}: 5일 순매수 {fmt(d5)}주 / 20일 순매수 {fmt(d20)}주")
        if investor.get("trading_value"):
            supply_lines.append(f"- 거래대금: {fmt(investor['trading_value'])}억")
    supply_text = "\n".join(supply_lines) if supply_lines else "수급 데이터 없음"

    # ── 1번 수정: 검증 상태 3분류 ────────────────────────────────
    if validation is None:
        validation_section = "【데이터 검증】 검증 미실행 — 이 분석의 실적 수치는 별도 확인이 필요합니다."
    elif not validation.get("has_issues"):
        validation_section = "【데이터 검증】 통과 (이상 없음)"
    else:
        issues = validation.get("issues", [])
        lines = []
        for iss in issues:
            sev = iss["severity"]
            icon = "🔴" if sev == "severe" else ("🟡" if sev == "medium" else "🔵")
            lines.append(f"  {icon}[{sev}] {iss['message']}")
        validation_section = "【데이터 검증 경고】\n" + "\n".join(lines)

    # ── 2번 수정: 확신도 섹션 + 지침 분기 ───────────────────────
    if confidence:
        rel = confidence.get("reliability", {})
        att = confidence.get("attractiveness", {})
        ta = "✅ 턴어라운드 종목 감지됨" if confidence.get("is_turnaround") else ""

        rel_lines = "\n".join(f"  - {d}" for d in rel.get("details", []))
        att_lines = "\n".join(
            f"  - {k}: {'+' if v['score']>0 else ''}{v['score']} ({v['range']}) — {v['reason']}"
            for k, v in att.get("breakdown", {}).items()
        )
        confidence_section = (
            f"{ta}\n" if ta else ""
        ) + (
            f"【분석 신뢰도: {rel.get('score',0)}/80 ({rel.get('label','')})】\n{rel_lines}\n\n"
            f"【투자 매력도: {att.get('score',0)}/100 ({att.get('label','')})】\n{att_lines}"
        )
        confidence_instruction = (
            "- 확신도: 점수는 보고서에 출력하지 마라 (UI에서 별도 표시). "
            "턴어라운드 종목이면 PER 해석 시 반드시 이를 명시해라."
        )
        confidence_output = ""
    else:
        confidence_section = "【확신도】 계산값 없음"
        confidence_instruction = "- 확신도: 계산값이 없으므로 확신도 섹션을 출력하지 마라."
        confidence_output = ""

    # ── 추세 표현 허용 여부 판단 ────────────────────────────────
    financials_for_trend = fundamental.get("financials", {}) or {}
    _confirmed_annual = [
        r for r in financials_for_trend.get("annual", []) if "(E)" not in r.get("period", "")
    ]
    _confirmed_quarterly = [
        r for r in financials_for_trend.get("quarterly", []) if "(E)" not in r.get("period", "")
    ]
    has_enough_history = len(_confirmed_annual) >= 2 and len(_confirmed_quarterly) >= 2

    # ── user prompt 상단 분석 조건 요약 ───────────────────────────
    if rel_score < 25:
        tone_summary_str = f"신뢰도 {rel_score}/80 → 투자 판단 불가 강제 적용"
    elif rel_score < 45:
        tone_summary_str = f"신뢰도 {rel_score}/80 → 매수 표현 금지"
    elif rel_score < 65:
        tone_summary_str = f"신뢰도 {rel_score}/80 → 불확실 항목 (확인 필요) 표시"
    else:
        tone_summary_str = f"신뢰도 {rel_score}/80 → 정상"

    if estimate_periods:
        e_summary_str = (
            f"있음 ({', '.join(estimate_periods[:3])}) — 신뢰도 낮음, 전면 금지"
            if e_issue_severe else
            f"있음 ({', '.join(estimate_periods[:3])}) — 시나리오/조언만 허용"
        )
    else:
        e_summary_str = "없음 — 확정 데이터만으로 분석"

    _max_sev = validation.get("max_severity", "미실행") if validation else "미실행"
    _issue_cnt = len(validation.get("issues", [])) if validation else 0
    val_summary_str = f"{_max_sev} ({_issue_cnt}건)" if validation else "미실행"

    trend_summary_str = (
        "확정 시계열 충분 — 추세 표현 가능"
        if has_enough_history else
        "확정 시계열 부족 — 추세/회복/턴어라운드 표현 금지, 판단 보류"
    )

    system_prompt = f"""너는 주식 분석 보고서를 작성하는 어시스턴트다.
이 보고서의 독자는 주식 초보자 또는 대학생이다.

이 보고서는 전문가 흉내를 내는 게 목적이 아니다.
주식 지식이 많지 않은 성인도 읽고 이해할 수 있어야 하지만, 말투가 가볍거나 유치해서는 안 된다.
친절한 대학생 선배가 차분하게 설명해주는 느낌으로 써라.
증권사 리포트보다 쉽게, 경제 유튜브보다 진중하게.

[문체 규칙 — 모든 섹션에 적용]
- 문장은 짧고 분명하게 쓴다.
- 한 문장 안에 가능하면 "숫자 + 그 숫자가 의미하는 바"를 함께 쓴다.
- 어려운 용어는 꼭 필요할 때만 쓰고, 처음 나올 때 한 번만 짧게 설명한다. (예: ROE(자기자본이익률))
- PER, PBR, ROE, RSI, MACD, 이동평균선 같은 지표는 설명 없이 나열하지 않는다.
- 불필요하게 멋을 낸 표현은 쓰지 않는다.
- 같은 말을 반복하지 않는다.
- 숫자를 나열만 하지 말고, 그 숫자가 무엇을 뜻하는지 함께 쓴다.
- 감정적 표현보다 차분한 설명을 우선한다.
- 결론은 분명하게, 근거는 숫자로 뒷받침한다.
- 투자 권유 표현("매수 추천", "수익 기대", "반드시 사야 합니다") 사용 금지.

[표현 가이드 — 어려운 말을 쉽게]
- "밸류에이션 부담" → "주가가 싼 편은 아니다" 또는 "이미 기대가 어느 정도 반영된 가격이다"
- "턴어라운드 국면" → "실적이 나빠졌다가 다시 좋아지는 과정"
- "모멘텀 유지" → "최근 상승 흐름은 아직 살아 있다"
- "수급 개선" → "외국인·기관이 최근 순매수하고 있다"
- "밸류에이션 매력" → "현재 주가 수준이 다른 종목에 비해 부담이 적다"
- "실적 가시성 높음" → "앞으로 실적이 얼마나 나올지 예측하기 비교적 쉬운 상황이다"
- "업황 개선" → "이 업종 전체 분위기가 좋아지고 있다"
- "정배열" → "단기·중기·장기 이동평균선이 모두 위로 정렬된 상승 흐름"
- "역배열" → "이동평균선이 하락 방향으로 정렬된 하락 흐름"
- "골든크로스" → "단기 이동평균이 장기 이동평균을 아래서 위로 돌파 — 상승 신호"
- "데드크로스" → "단기 이동평균이 장기 이동평균 아래로 꺾임 — 하락 신호"
- "RSI 과매수" → "단기간에 너무 빠르게 올랐을 때 나타나는 신호 (RSI 70 초과)"
- "EPS" → "주당순이익(주식 1주당 얼마의 이익을 냈는지)"

[최우선 규칙 — 아래 어떤 규칙보다 우선한다]
1. 신뢰도 분기 규칙은 모든 판단에 최우선으로 적용된다.
2. [추정] 수치는 핵심 이유·좋은 점·걱정되는 점·한줄 결론 근거로 사용 금지.
3. 데이터 부족·충돌 시 "판단 보류/확인 필요"를 우선한다.
4. 제공된 데이터는 투자 권유가 아닌 해석 대상이다.
5. 숫자 충돌 시 항상 더 보수적으로 표현한다.

[언어 강도 — 최우선 준수]
{tone_rule}

[금지 규칙]
- [추정] 수치를 핵심 이유/좋은 점/걱정되는 점/한줄 결론에 사용 금지
- 신뢰도 <25에서 투자 판단 제시 금지
- 턴어라운드 종목에 trailing PER 단독으로 고평가 단정 금지
- 배당 없음만으로 약점 강조 금지
- 뉴스 내용을 원인으로 단정 금지 — 확인된 사실만 서술
- 분기 1~2개만으로 연간 추세 단정 금지
- 업황 전체 호재를 이 회사 호재로 직결 금지
- 52주 고저 "X% 상승/하락" 표현 금지
- 확정 연간 2개 미만 또는 확정 분기 2개 미만이면 "추세/회복/개선 지속/턴어라운드 진행" 표현 금지. 반드시 "확인 필요" 또는 "판단 보류"로 완화해라.

[허용 규칙]
- [추정]은 시나리오/조언/Forward PER 맥락에서만 허용. 반드시 "(증권사 추정 기준)" 표기
- 뉴스: A급·직접관련 우선. 없으면 B급·간접관련 보조 허용
- 뉴스 인용은 뉴스/이벤트 섹션에서만. 핵심 이유·좋은 점·걱정되는 점에서 뉴스 인용 금지

[표현 규칙]
- PER: 업종 평균/참고 범위가 있을 때만 상대 표현("X배는 업종 평균 XX~XX 대비...") 사용. 데이터가 약하거나 없으면 trailing PER의 성격과 해석 유의점만 서술해라. 절대값 단정 금지.
- 52주: "고점 대비 -X%, 저점 대비 약 X배 수준" 형식
- 외국인: 보유 비율보다 최근 순매수/순매도 추세를 우선 언급
- 뉴스 인용: "〇〇(언론사) 보도에 따르면" 형식으로 출처 명시
- 핵심 이유: 수치·사실만. "효과/신호/판단/기대/의미/구조적/반영된 것으로/볼 수 있다" 금지
- 숫자 시점: (trailing)=과거 기반, (실시간)=현재 시세 명확히 구분
{confidence_instruction}

{sector_rule}

[추정치(E) 사용 제한]
▶ [추정] = 증권사 컨센서스. 공시 확정이 아니므로 틀릴 수 있다.
▶ 금지: 핵심 이유 / 좋은 점 / 걱정되는 점 / 한줄 결론
▶ 허용: 밸류에이션 Forward PER / 시나리오 / 조언 — "(증권사 추정 기준)" 표기 필수
▶ [추정]만으로 결론 방향 변경 금지. 확정 추세 뒷받침 시에만 참고.
▶ [추정] 없으면 미래 전망 생략 또는 "추정 데이터 없음" 표기.

[섹션별 데이터 사용 규칙]
한줄 결론:          [확정] + 신뢰도 규칙만. [추정] 금지.
쉽게 말하면:        [확정] 기반 상황 설명. 쉬운 문장으로. 수치 나열 금지.
핵심 숫자:          [확정] 수치 우선. [추정]은 "(증권사 추정 기준)" 표기 후 허용. 3~6개만.
좋은 점:            [확정] 수치·구조적 사실만. [추정] 금지. 최대 3개.
조심할 점:          [확정] 수치·구조적 사실만. [추정] 금지. 최대 3개.
지금은 어떻게 볼까: [추정] 방향성 참고 허용. "(증권사 추정 기준)" 표기 필수. 2~4문장.
꼭 확인할 것:       검증 필요 항목만. 투자 권유 금지. 최대 3개.

[분량 규칙 — 글자 수 제한이 아닌 항목 수로 조절]
- 한줄 결론: 1~2문장
- 쉽게 말하면: 3~5문장
- 핵심 숫자: 3~6개 (초과 금지)
- 좋은 점 / 조심할 점 / 꼭 확인할 것: 각각 최대 3개
- 지금은 어떻게 볼까?: 2~4문장
- 뉴스: 종목과 직접 관련 있는 것 1~2개만 반영

[출력 형식] 반드시 아래 순서와 형식으로만 한국어로 답해라:

## 🎯 한줄 결론
(1~2문장. "사도 됨 / 좀 더 지켜봐 / 사지 마" 중 하나 포함)
※ 신뢰도 <25이면 반드시 '⛔ 데이터 부족 — 투자 판단 불가'로 대체
※ "좀 더 지켜봐"라면 바로 아래에:
  - 📌 매수 고려 조건: 방향성으로 표현. AI 임의 수치 금지.
  - 🚫 포기 조건: 차트 기준(실제 계산값)만.

## 💬 쉽게 말하면
(3~5문장. 이 회사가 지금 어떤 상태인지 쉬운 말로 설명. 숫자 나열 금지. 상황 설명 위주)

## 📊 핵심 숫자
(3~6개만 선별. 초과 금지)
형식: **지표명 수치**: 쉬운 해석 한 줄
예시: **PER 9.0배**: 이익에 비해 주가가 아주 비싼 편은 아닙니다.
- 각 숫자에는 반드시 해석을 붙여라
- 어려운 용어는 처음 나올 때만 짧게 설명 (예: ROE(자기자본이익률))
- [추정] 수치는 "(증권사 추정 기준)" 표기 후 사용 가능

## ✅ 좋은 점
(최대 3개. 각 1~2문장. [확정] 기반만. [추정] 금지)

## ⚠️ 조심할 점
(최대 3개. 각 1~2문장. [확정] 기반만. [추정] 금지)

## 🔍 지금은 어떻게 볼까?
(2~4문장. 시나리오·단기/중기/장기 조언을 통합해 한 단락으로. "지켜보기", "확인 필요" 표현 우선)
※ [추정] 방향성 참고 허용 — "(증권사 추정 기준)" 표기 필수

## 📋 꼭 확인할 것
(최대 3개. 번호 목록. 검증 필요 항목만. 투자 권유 금지)

[출력 전 자기검사 — 답변 작성 전 내부 확인용. 이 블록은 최종 응답에 절대 포함하지 마라.]
□ 한줄 결론·좋은 점·조심할 점에 [추정] 수치가 없는가?
□ 신뢰도 분기 규칙을 어기지 않았는가?
□ trailing 수치와 forward 수치를 혼용하지 않았는가?
□ 뉴스 내용을 원인으로 단정하지 않았는가?
□ 이동평균·52주 수치가 제공된 데이터와 일치하는가?
□ 업황 호재를 이 회사와 직접 연결 없이 쓰지 않았는가?
□ 확정 시계열이 부족한데도 추세/회복/턴어라운드 표현을 쓰지 않았는가?
□ 핵심 숫자가 6개를 초과하지 않는가?
□ 좋은 점·조심할 점·꼭 확인할 것이 각 3개를 초과하지 않는가?
□ 시나리오·단기/중기/장기 조언 섹션을 별도로 만들지 않았는가?"""

    _per_note = f" [{fundamental['per_source_note']}]" if fundamental.get("per_source_note") else ""
    _pbr_note = f" [{fundamental['pbr_source_note']}]" if fundamental.get("pbr_source_note") else ""
    _pref_note_line = (f"※ {fundamental['preferred_stock_note']}\n") if fundamental.get("preferred_stock_note") else ""

    user_prompt = f"""[분석 조건 요약 — 먼저 읽고 이 조건으로 분석해라]
• {tone_summary_str}
• 추정치(E): {e_summary_str}
• 검증: {val_summary_str}
• 실적 추세: {trend_summary_str}

━━━━━━━━━━━━ 데이터 부족 경고 ━━━━━━━━━━━━
{data_warning_text}

━━━━━━━━━━━━ 데이터 검증 결과 ━━━━━━━━━━━━
{validation_section}

━━━━━━━━━━━━ 기업 개요 ━━━━━━━━━━━━
{overview_section}

━━━━━━━━━━━━ 종목 정보 ━━━━━━━━━━━━
종목: {name} ({stock.get('ticker', '')})
※ 현재가·기술지표는 실시간/최근 기준, 재무 데이터는 최근 공시(연간·분기) 기준
현재가: {price} | 등락률: {stock.get('change_rate')}% | 거래량: {stock.get('volume')}
{(lambda ed, nq, lq: f"실적 일정: {lq}까지 발표 완료 | 다음 발표일: {ed} ({nq} 실적)" if ed and nq and lq else (f"다음 실적 발표일: {ed}" if ed else "다음 실적 발표일: 미확인 (직접 확인 권장)"))(fundamental.get('earnings_date',''), fundamental.get('next_quarter',''), fundamental.get('last_reported_quarter',''))}

━━━━━━━━━━━━ 재무지표 ━━━━━━━━━━━━
※ 숫자 유형 표기: (trailing)=과거 실적 기반 | (실시간)=현재 시세 | (1년 기준)=최근 52주
- PER: {fundamental.get('per', 'N/A')}배 (trailing){_per_note} | PBR: {fundamental.get('pbr', 'N/A')}배{_pbr_note}
{_pref_note_line}- {sector_per_str}
- EPS: {fundamental.get('eps', 'N/A')} (trailing) | 시가총액: {fundamental.get('market_cap', 'N/A')} (실시간)
- 52주 최고: {fundamental.get('week52_high', 'N/A')} / 최저: {fundamental.get('week52_low', 'N/A')} (1년 기준)
- 외국인 보유율: {fundamental.get('foreign_rate', 'N/A')}%
- 배당수익률: {fundamental.get('dividend_yield', 'N/A')}%
- 섹터: {fundamental.get('sector', 'N/A')} / {fundamental.get('industry', 'N/A')}

━━━━━━━━━━━━ 실적 데이터 ━━━━━━━━━━━━
{fundamental.get('financials_text', '실적 데이터 없음')}

━━━━━━━━━━━━ 기술적 지표 ━━━━━━━━━━━━
{tech_text}

━━━━━━━━━━━━ 수급 동향 ━━━━━━━━━━━━
{supply_text}

━━━━━━━━━━━━ 최근 뉴스 ━━━━━━━━━━━━
{news_text}

━━━━━━━━━━━━ 확신도 (코드 계산값) ━━━━━━━━━━━━
{confidence_section}"""

    return system_prompt, user_prompt


def _validate_report_numbers(report: str, stock: dict, fundamental: dict) -> list[str]:
    """AI 보고서 본문의 핵심 수치가 원본 데이터와 충돌하는지 검사."""
    import re
    warnings = []

    def _extract_numbers(pattern: str, text: str) -> list[float]:
        matches = re.findall(pattern, text)
        results = []
        for m in matches:
            try:
                results.append(float(str(m).replace(",", "")))
            except (ValueError, TypeError):
                pass
        return results

    def _to_float(v) -> float | None:
        try:
            return float(str(v).replace(",", "").replace("%", "").strip())
        except (ValueError, TypeError):
            return None

    # PER 검사 — Forward PER 제외 (trailing PER만 원본과 비교)
    src_per = _to_float(fundamental.get("per"))
    if src_per and src_per > 0:
        per_in_report = []
        for m in re.finditer(r"PER[^\d]{0,5}([\d,]+\.?\d*)\s*배", report):
            ctx = report[max(0, m.start() - 40):m.start()]
            if re.search(r"[Ff]orward|포워드|선행|추정|컨센서스|증권사|\d{4}E", ctx):
                continue
            try:
                per_in_report.append(float(m.group(1).replace(",", "")))
            except (ValueError, TypeError):
                pass
        for v in per_in_report:
            if abs(v - src_per) / src_per > 0.15:
                warnings.append(f"⚠️ [숫자불일치] 보고서 trailing PER {v}배 ↔ 원본 {src_per}배 (15% 초과 차이)")

    # PBR 검사
    src_pbr = _to_float(fundamental.get("pbr"))
    if src_pbr and src_pbr > 0:
        pbr_in_report = _extract_numbers(r"PBR[^\d]{0,5}([\d,]+\.?\d*)\s*배", report)
        for v in pbr_in_report:
            if abs(v - src_pbr) / src_pbr > 0.15:
                warnings.append(f"⚠️ [숫자불일치] 보고서 PBR {v}배 ↔ 원본 {src_pbr}배 (15% 초과 차이)")

    # 52주 고점 검사 — 최고/최저 명시적 구분
    src_high = _to_float(fundamental.get("week52_high"))
    if src_high and src_high > 0:
        pattern_high = r"52주\s*(?:최고|고점)[^\d]{0,5}([\d,]+\.?\d*)"
        high_in_report = _extract_numbers(pattern_high, report)
        for v in high_in_report:
            if v > 100 and abs(v - src_high) / src_high > 0.05:
                warnings.append(f"⚠️ [숫자불일치] 보고서 52주최고 {v:,.2f} ↔ 원본 {src_high:,.2f} (5% 초과 차이)")

    src_low = _to_float(fundamental.get("week52_low"))
    if src_low and src_low > 0:
        pattern_low = r"52주\s*(?:최저|저점)[^\d]{0,5}([\d,]+\.?\d*)"
        low_in_report = _extract_numbers(pattern_low, report)
        for v in low_in_report:
            if v > 100 and abs(v - src_low) / src_low > 0.05:
                warnings.append(f"⚠️ [숫자불일치] 보고서 52주최저 {v:,.2f} ↔ 원본 {src_low:,.2f} (5% 초과 차이)")

    if warnings:
        logger.warning("보고서 숫자 불일치 감지: %s", warnings)

    return warnings


async def analyze_stock(stock: dict, fundamental: dict, chart: list, news: list,
                        validation: dict | None = None, confidence: dict | None = None,
                        sector_naver_per: float | None = None) -> str:
    system_prompt, user_prompt = build_prompt(stock, fundamental, chart, news, validation=validation, confidence=confidence, sector_naver_per=sector_naver_per)
    try:
        message = await client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=4000,
            timeout=120,
            system=system_prompt,
            messages=[{"role": "user", "content": user_prompt}],
        )
        report = message.content[0].text
        mismatches = _validate_report_numbers(report, stock, fundamental)
        if mismatches:
            warning_block = "\n\n---\n**⚠️ 자동 검증 경고 (보고서 내 수치 불일치 감지)**\n" + "\n".join(mismatches)
            report += warning_block
        return report
    except anthropic.APITimeoutError:
        logger.error("Claude API 타임아웃")
        raise RuntimeError("분석 시간이 초과되었습니다. 잠시 후 다시 시도해주세요.")
    except anthropic.APIStatusError as e:
        logger.error("Claude API 오류: %s %s", e.status_code, e.message)
        raise RuntimeError(f"분석 서비스 오류 (HTTP {e.status_code}). 잠시 후 다시 시도해주세요.")
    except Exception as e:
        logger.exception("Claude 분석 중 예외 발생")
        raise RuntimeError("분석 중 알 수 없는 오류가 발생했습니다.") from e
