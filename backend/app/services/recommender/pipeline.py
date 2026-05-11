"""
추천 파이프라인 오케스트레이터

흐름:
  1. build_candidate_shortlist()  - 정적 풀 + 사전 점수
  2. _light_analyze_batch()       - 뉴스/차트/Claude 제외, 재무+밸류만 빠르게
  3. post_score() 정렬 → apply_diversity()  - 최종 3~5개 선정
  4. _generate_reasons()          - 규칙 기반 추천/리스크/제외 이유
  5. _claude_recommend_report()   - Claude로 최종 리포트 다듬기
  6. calc_split_buy()             - 분할매수 초안
"""
import asyncio
import re
from dataclasses import dataclass, field

from app.services.financials import get_financials, validate_financials
from app.services.confidence import calculate_confidence
from app.services.recommender.candidate_pool import Candidate
from app.services.recommender.scorer import (
    UserInput,
    build_candidate_shortlist,
    post_score,
    apply_diversity,
    calc_split_buy,
)

# Claude 클라이언트는 claude_analysis에서 공유
from app.services.claude_analysis import client as _claude_client

# KR/US 분기 임포트
try:
    from app.services.kis_fundamental import get_price_and_fundamental as _kr_fundamental
    _KIS_AVAILABLE = True
except Exception:
    _KIS_AVAILABLE = False

try:
    from app.services.foreign_analysis import get_foreign_stock_and_fundamental as _us_fundamental
    from app.services.foreign_analysis import get_foreign_financials as _us_financials
    from app.services.foreign_analysis import get_foreign_chart as _get_us_chart
    _YFINANCE_AVAILABLE = True
except Exception:
    _YFINANCE_AVAILABLE = False

try:
    from app.services.kis_chart import get_daily_chart as _get_kr_chart
    _KIS_CHART_AVAILABLE = True
except Exception:
    _KIS_CHART_AVAILABLE = False

from app.services.technical import calc_indicators as _calc_indicators
from app.services.recommender.candidate_pool import matches_sector


# ──────────────────────────────────────────────
# 경량 분석 결과
# ──────────────────────────────────────────────
@dataclass
class LightResult:
    ticker: str
    confidence_score: int | None
    attractiveness_score: int | None
    validation_severity: str | None
    fundamental: dict
    financials: dict = field(default_factory=dict)   # DART/yfinance 원본 재무 데이터
    current_price: int | None = None
    error: bool = False
    timing_score: float = 0.0
    timing_flags: list = field(default_factory=list)
    timing_summary: str = ""


# ──────────────────────────────────────────────
# timing_score 계산 (calc_indicators 결과 기반)
# ──────────────────────────────────────────────
def _calc_timing_score(ind: dict) -> tuple[float, list[str], str]:
    """
    calc_indicators() 결과 → (timing_score, flags, summary)
    범위: -12 ~ +12 (보조 점수, post_score를 뒤집지 않음)
    """
    if not ind:
        return 0.0, [], ""

    score = 0.0
    flags: list[str] = []

    trend      = ind.get("trend", "") or ""
    macd       = ind.get("macd")
    macd_sig   = ind.get("macd_signal")
    rsi        = ind.get("rsi")
    bb_pos     = ind.get("bb_position", "") or ""
    vol_ratio  = ind.get("vol_ratio") or 0.0
    cross      = ind.get("cross", "") or ""
    gap_high   = ind.get("gap_from_high")   # 음수: 고점 대비 하락%

    # 추세 (±3)
    if "정배열" in trend:
        score += 3; flags.append("정배열")
    elif "역배열" in trend:
        score -= 3; flags.append("역배열")

    # MACD (±2)
    if macd is not None and macd_sig is not None:
        if macd > macd_sig:
            score += 2; flags.append("MACD상승")
        else:
            score -= 2; flags.append("MACD하락")

    # RSI (−3 / +1)
    if rsi is not None:
        if rsi >= 70:
            score -= 3; flags.append("RSI과매수")
        elif rsi <= 30:
            score += 1; flags.append("RSI과매도")
        elif 45 <= rsi <= 60:
            score += 1; flags.append("RSI적정")

    # 볼린저 (±3 / ±1)
    if "상단 돌파" in bb_pos:
        score -= 3; flags.append("BB과열")
    elif "하단 이탈" in bb_pos:
        score += 1; flags.append("BB하단반등")
    elif bb_pos:
        m = re.search(r"밴드 내 ([\d.]+)%", bb_pos)
        if m:
            pct = float(m.group(1))
            if pct > 80:
                score -= 1; flags.append("BB상단근접")
            elif pct < 30:
                score += 1; flags.append("BB하단권")

    # 거래량 (±1)
    if vol_ratio >= 1.5:
        score += 1; flags.append("거래량급증")
    elif vol_ratio < 0.5:
        score -= 1; flags.append("거래량부재")

    # 크로스 (±2)
    if "골든" in cross:
        score += 2; flags.append("골든크로스")
    elif "데드" in cross:
        score -= 2; flags.append("데드크로스")

    # 52주 고점 대비 위치 (±1)
    if gap_high is not None:
        if gap_high > -5:          # 고점 근접 (과열 우려)
            score -= 1; flags.append("52주고점근접")
        elif -30 <= gap_high <= -10:  # 건강한 조정 구간
            score += 1; flags.append("조정구간")
        elif gap_high < -40:       # 장기 하락 구간
            score -= 1; flags.append("장기하락")

    score = max(-12.0, min(12.0, score))

    pos = [f for f in flags if f in {
        "정배열", "MACD상승", "RSI적정", "골든크로스", "거래량급증",
        "조정구간", "BB하단반등", "BB하단권", "RSI과매도",
    }]
    neg = [f for f in flags if f in {
        "역배열", "MACD하락", "RSI과매수", "BB과열", "데드크로스",
        "거래량부재", "52주고점근접", "장기하락", "BB상단근접",
    }]

    if score >= 4:
        summary = f"기술적 진입 타이밍 양호 ({', '.join(pos[:2])})"
    elif score <= -4:
        summary = f"기술적 진입 부담 ({', '.join(neg[:2])})"
    elif score > 0:
        summary = "타이밍 중립 (긍정 신호 우세)"
    elif score < 0:
        summary = "타이밍 중립 (부정 신호 우세)"
    else:
        summary = "타이밍 중립"

    return score, flags, summary


# ──────────────────────────────────────────────
# timing 배치 조회 (Phase 2.5)
# ──────────────────────────────────────────────
async def _fetch_timing_batch(
    targets: list[tuple[Candidate, float, "LightResult"]],
    concurrency: int = 3,
) -> dict[str, tuple[float, list[str], str]]:
    """
    상위 후보에 한해 chart → calc_indicators → _calc_timing_score.
    실패한 종목은 (0, [], "") 반환 — 점수 변동 없음.
    """
    sem = asyncio.Semaphore(concurrency)

    async def _one(c: Candidate):
        async with sem:
            try:
                if c.market == "KR" and _KIS_CHART_AVAILABLE:
                    chart = await _get_kr_chart(c.ticker)
                elif c.market == "US" and _YFINANCE_AVAILABLE:
                    chart = await asyncio.to_thread(_get_us_chart, c.ticker, "1y")
                else:
                    return c.ticker, (0.0, [], "")
                ind = _calc_indicators(chart)
                return c.ticker, _calc_timing_score(ind)
            except Exception:
                return c.ticker, (0.0, [], "")

    results = await asyncio.gather(*[_one(c) for c, _, _ in targets])
    return dict(results)


# ──────────────────────────────────────────────
# 경량 분석: KR
# ──────────────────────────────────────────────
async def _light_kr(ticker: str, sector: str) -> LightResult:
    try:
        if _KIS_AVAILABLE:
            (stock, fundamental), financials = await asyncio.gather(
                _kr_fundamental(ticker),
                get_financials(ticker),
            )
        else:
            stock, fundamental, financials = {}, {}, await get_financials(ticker)

        validation = validate_financials(financials, sector=sector)
        conf = calculate_confidence(fundamental, financials, {}, [], validation)
        severity = validation.get("max_severity")
        try:
            current_price = int(str(stock.get("price", "") or "").replace(",", "") or 0) or None
        except (ValueError, TypeError):
            current_price = None
        return LightResult(
            ticker=ticker,
            confidence_score=conf["reliability"]["score"],
            attractiveness_score=conf["attractiveness"]["score"],
            validation_severity=severity,
            fundamental=fundamental,
            financials=financials,
            current_price=current_price,
        )
    except Exception:
        return LightResult(ticker=ticker, confidence_score=None,
                           attractiveness_score=None, validation_severity=None,
                           fundamental={}, error=True)


# ──────────────────────────────────────────────
# 경량 분석: US
# ──────────────────────────────────────────────
async def _light_us(ticker: str, sector: str) -> LightResult:
    try:
        if not _YFINANCE_AVAILABLE:
            return LightResult(ticker=ticker, confidence_score=None,
                               attractiveness_score=None, validation_severity=None,
                               fundamental={}, error=True)

        stock, fundamental = await asyncio.to_thread(_us_fundamental, ticker)
        financials = await asyncio.to_thread(_us_financials, ticker)

        validation = validate_financials(financials, sector=sector)
        conf = calculate_confidence(fundamental, financials, {}, [], validation)
        severity = validation.get("max_severity")
        try:
            price_raw = stock.get("price", "") or ""
            current_price = int(float(str(price_raw).replace(",", "")) * 1400) if price_raw else None
        except (ValueError, TypeError):
            current_price = None
        return LightResult(
            ticker=ticker,
            confidence_score=conf["reliability"]["score"],
            attractiveness_score=conf["attractiveness"]["score"],
            validation_severity=severity,
            fundamental=fundamental,
            financials=financials,
            current_price=current_price,
        )
    except Exception:
        return LightResult(ticker=ticker, confidence_score=None,
                           attractiveness_score=None, validation_severity=None,
                           fundamental={}, error=True)


# ──────────────────────────────────────────────
# 배치 경량 분석 (병렬, 동시 5개 제한)
# ──────────────────────────────────────────────
async def _light_analyze_batch(
    shortlist: list[tuple[Candidate, float]],
) -> list[tuple[Candidate, float, LightResult]]:
    sem = asyncio.Semaphore(5)

    async def _with_sem(c: Candidate, pre: float):
        async with sem:
            if c.market == "KR":
                res = await _light_kr(c.ticker, c.sector)
            else:
                res = await _light_us(c.ticker, c.sector)
            final = post_score(pre, res.confidence_score, res.attractiveness_score,
                               res.validation_severity)
            return c, final, res

    tasks = [_with_sem(c, pre) for c, pre in shortlist]
    return await asyncio.gather(*tasks)


# ──────────────────────────────────────────────
# 규칙 기반 추천 이유 / 리스크 문장 생성
# ──────────────────────────────────────────────
_REASON_RULES: list[tuple[set, str, str]] = [
    # (태그 조건, risk 조건 or "", 문장)
    ({"배당"},         "conservative", "배당 이력이 있어 보수적 투자 성향에 적합합니다."),
    ({"배당"},         "long",         "장기 보유 시 배당 수익으로 안정적 수익 확보가 가능합니다."),
    ({"방어주"},       "conservative", "경기 하락 국면에서도 상대적으로 안정적인 종목입니다."),
    ({"AI"},           "",             "AI 투자 흐름과 연관성이 있는 업종으로 분류됩니다."),
    ({"성장주"},       "aggressive",   "성장성이 높은 종목으로 공격적 투자 성향과 맞습니다."),
    ({"턴어라운드"},   "",             "실적 개선 흐름이 감지되어 반등 가능성을 주목할 수 있습니다."),
    ({"수출"},         "",             "글로벌 시장을 대상으로 한 수출 기반 사업 구조입니다."),
    ({"저PER"},        "",             "현재 밸류에이션 부담이 상대적으로 낮은 편입니다."),
    ({"2차전지"},      "",             "전기차·에너지저장 시장 성장의 수혜 포지션입니다."),
    ({"방산"},         "",             "글로벌 방산 수요 확대 국면에서 수혜가 기대됩니다."),
    ({"반도체"},       "",             "반도체 업황 회복 사이클에서 주목할 수 있는 종목입니다."),
]

# tag → 사업 성격 설명 (tag 매칭 시 "일부 보유" 대신 구체적 표현에 사용)
_TAG_BUSINESS_DESC: dict[str, str] = {
    "게임":     "게임 콘텐츠/IP 사업",
    "AI":       "AI 서비스/솔루션 사업",
    "SaaS":     "SaaS 구독 서비스 사업",
    "핀테크":   "핀테크/디지털금융 사업",
    "스트리밍": "스트리밍 콘텐츠 사업",
    "바이오":   "바이오/의약품 사업",
    "방산":     "방산 장비 사업",
    "2차전지":  "2차전지 소재·부품 사업",
    "반도체":   "반도체 관련 사업",
    "로봇":     "로봇/자동화 사업",
    "엔터":     "엔터테인먼트/IP 사업",
    "뷰티":     "뷰티/화장품 사업",
    "전력기기": "전력기기/에너지 인프라 사업",
}

_RISK_RULES: list[tuple[set, str]] = [
    ({"고PER"},        "현재 밸류에이션 부담이 있어 실적 확인이 필요합니다."),
    ({"변동성높음"},   "단기 변동성이 높아 손실 위험을 감안해야 합니다."),
    ({"경기민감"},     "경기 사이클에 민감하게 반응하는 종목입니다."),
    ({"바이오"},       "임상 결과에 따라 급등·급락 위험이 존재합니다."),
    ({"블록체인", "암호화폐"}, "규제 리스크와 가격 변동성이 매우 큰 자산군입니다."),
    ({"턴어라운드"},   "실적 개선이 가시화되지 않으면 반등이 지연될 수 있습니다."),
]

# 업종별 기본 리스크 fallback — tags에 리스크 근거가 없을 때 사용
_SECTOR_FALLBACK_RISKS: dict[str, str] = {
    "반도체":   "업황 사이클에 따라 실적 변동성이 클 수 있으며, 재고 조정 리스크가 존재합니다.",
    "2차전지":  "전기차 수요 둔화 및 원자재(리튬·니켈) 가격 변동에 노출되어 있습니다.",
    "바이오":   "임상 결과 및 규제 승인 여부에 따라 주가 변동이 클 수 있습니다.",
    "IT플랫폼": "광고 경기 민감도가 있으며, 플랫폼 규제 리스크가 잠재합니다.",
    "테크":     "고밸류에이션 구간에서 금리 상승 또는 실적 미스 시 조정 폭이 클 수 있습니다.",
    "건설":     "부동산 경기 및 금리 변동에 민감하게 반응하는 업종입니다.",
    "자동차":   "글로벌 수요 사이클과 원자재 비용 상승 리스크에 노출되어 있습니다.",
    "게임":     "신작 흥행 여부, 기존 IP 매출 둔화, 업데이트 성과에 따라 실적 변동성이 클 수 있습니다.",
    "화학":     "원유·원자재 가격 변동 및 수요 둔화 시 마진 압축 리스크가 있습니다.",
    "조선":     "수주 사이클과 환율 변동에 따라 실적 편차가 큰 업종입니다.",
    "엔터":     "아티스트 활동 공백·콘텐츠 흥행 실패 시 실적이 급락할 수 있습니다.",
    "항공":     "유가 변동과 수요 경기에 민감하게 반응하는 업종입니다.",
    "금융":     "금리 사이클 및 부실채권 리스크에 영향을 받는 업종입니다.",
    "방산":     "정책 예산 및 수출 계약 성사 여부에 따라 실적 변동이 발생할 수 있습니다.",
    "에너지":   "유가 및 에너지 정책 변화에 따라 수익성 변동이 클 수 있습니다.",
    "디스플레이": "세트 수요 사이클과 단가 경쟁에 의해 실적이 크게 변동할 수 있습니다.",
    "IT서비스": "기업 IT 투자 사이클 둔화, 클라우드 경쟁 심화, 대형 고객 의존도 리스크가 있습니다.",
}


def _sub_sector_reason(candidate: Candidate) -> str | None:
    """태그 기반 sub-sector 세분화 이유 — sector만으로 구분 불가한 역할을 설명."""
    tags = set(candidate.tags)
    sector = candidate.sector

    if sector == "반도체" or "반도체" in tags:
        if "장비" in tags:
            return "반도체 장비 포지션으로, 전방 업체 설비 투자 확대 시 직접 수혜가 기대됩니다."
        if "소재" in tags:
            return "반도체 소재 공급망 포지션으로, 업황 회복 시 안정적 수주 증가가 기대됩니다."
        if "설계" in tags:
            return "팹리스(설계 전문) 포지션으로, AI·고성능 칩 수요 확대의 수혜를 받습니다."
        if "패키징" in tags:
            return "반도체 패키징·후공정 수요 증가의 직접 수혜가 기대됩니다."
        if "AI" in tags:
            return "AI 가속기·HBM 수요 확대와 연계된 반도체 포지션입니다."

    if sector == "2차전지" or "2차전지" in tags:
        if "소재" in tags:
            return "2차전지 소재 공급망 포지션으로, 배터리 수요 확대 시 소재 수주가 증가합니다."
        if "수소" in tags or "클린에너지" in tags:
            return "수소·클린에너지 전환 흐름의 수혜가 기대되는 포지션입니다."

    if sector == "IT플랫폼":
        if "게임" in tags:
            return "게임 IP 다변화 및 글로벌 퍼블리싱 확대로 성장 가능성이 있는 후보입니다."
        if "SaaS" in tags:
            return "기업용 SaaS 구독 모델로 안정적 반복 수익 구조를 보유합니다."
        if "스트리밍" in tags:
            return "AI 기반 스트리밍 플랫폼 성장 흐름에서 수혜가 기대됩니다."

    if "방산" in tags and "항공" in tags:
        return "항공·방산 복합 포지션으로, 방산 수출과 MRO 수요 양쪽에서 수혜가 가능합니다."

    if "전력기기" in tags:
        return "AI 데이터센터·전력망 현대화 수요와 맞닿은 전력기기 포지션입니다."

    if "핀테크" in tags:
        return "디지털 금융 서비스 확장으로 성장이 기대되는 핀테크 포지션입니다."

    return None


def _sector_match_strength(candidate: Candidate, ps: str) -> str:
    """
    관심 업종 ps에 대해 매칭 강도를 반환.
    "direct"  : candidate.sector 자체가 ps 또는 ps의 핵심 alias에 해당
    "tag"     : sector는 다르지만 tags에 ps 또는 ps alias가 포함
    "none"    : 매칭 없음
    """
    from app.services.recommender.candidate_pool import SECTOR_ALIASES
    core_aliases: set[str] = SECTOR_ALIASES.get(ps, set()) | {ps}

    if candidate.sector in core_aliases:
        return "direct"

    tags = set(candidate.tags)
    if ps in tags or bool(core_aliases & tags):
        return "tag"

    return "none"


# ──────────────────────────────────────────────
# Phase 2 데이터에서 검증된 사실 추출
# 추가 API 호출 없음 — LightResult에 이미 있는 데이터만 사용
# ──────────────────────────────────────────────
_POSITIVE_TIMING: set[str] = {
    "정배열", "MACD상승", "RSI적정", "골든크로스", "거래량급증",
    "조정구간", "BB하단반등", "BB하단권", "RSI과매도",
}
_NEGATIVE_TIMING: set[str] = {
    "역배열", "MACD하락", "RSI과매수", "BB과열", "데드크로스",
    "거래량부재", "52주고점근접", "장기하락", "BB상단근접",
}


def _build_verified_facts(
    candidate: Candidate,
    res: LightResult,
    weight_pct: float | None = None,
) -> list[dict]:
    """
    Phase 2에서 가져온 fundamental/financials/timing 데이터만 사용.
    compact key: t=type, f=fact text, src=source, conf=confidence, v=numeric value
    """
    facts: list[dict] = []

    def _n(val) -> float | None:
        if val is None:
            return None
        try:
            return float(str(val).replace(",", "").replace("%", "").strip())
        except (ValueError, TypeError):
            return None

    fund = res.fundamental
    src = "KIS" if candidate.market == "KR" else "yfinance"
    annual = [r for r in res.financials.get("annual", []) if "(E)" not in r.get("period", "")]

    # 밸류에이션
    per = _n(fund.get("per"))
    if per is not None and 0 < per < 500:
        facts.append({"t": "valuation", "f": f"PER {per:.1f}배", "src": src, "conf": "high", "v": per})
    pbr = _n(fund.get("pbr"))
    if pbr is not None and 0 < pbr < 50:
        facts.append({"t": "valuation", "f": f"PBR {pbr:.1f}배", "src": src, "conf": "high", "v": pbr})
    div = _n(fund.get("dividend_yield"))
    if div is not None and div > 0:
        facts.append({"t": "dividend", "f": f"배당수익률 {div:.1f}%", "src": src, "conf": "high", "v": div})

    # 재무 시계열 — DART/yfinance 확정치만
    if len(annual) >= 2:
        curr, prev = annual[-1], annual[-2]
        period = curr.get("period", "")

        def _yoy(key: str) -> float | None:
            c, p = _n(curr.get(key)), _n(prev.get(key))
            if c is None or p is None or p == 0:
                return None
            return round((c - p) / abs(p) * 100, 1)

        op_g = _yoy("영업이익")
        if op_g is not None and abs(op_g) >= 5:
            sign = "+" if op_g > 0 else ""
            facts.append({"t": "financial", "f": f"영업이익 YoY {sign}{op_g}% ({period})", "src": "DART", "conf": "high", "v": op_g})
        rev_g = _yoy("매출액")
        if rev_g is not None and abs(rev_g) >= 5:
            sign = "+" if rev_g > 0 else ""
            facts.append({"t": "financial", "f": f"매출액 YoY {sign}{rev_g}% ({period})", "src": "DART", "conf": "high", "v": rev_g})

    if annual:
        latest = annual[-1]
        period = latest.get("period", "")
        op_m = _n(latest.get("영업이익률"))
        if op_m is not None:
            facts.append({"t": "financial", "f": f"영업이익률 {op_m:.1f}% ({period})", "src": "DART", "conf": "high", "v": op_m})
        roe = _n(latest.get("ROE"))
        if roe is not None:
            facts.append({"t": "financial", "f": f"ROE {roe:.1f}% ({period})", "src": "DART", "conf": "high", "v": roe})
        debt = _n(latest.get("부채비율"))
        if debt is not None:
            facts.append({"t": "financial", "f": f"부채비율 {debt:.1f}% ({period})", "src": "DART", "conf": "high", "v": debt})

    # 기술적 지표 (chart → calc_indicators)
    for flag in res.timing_flags:
        facts.append({"t": "timing", "f": flag, "src": "chart", "conf": "medium", "v": res.timing_score})

    # 재무 검증 결과
    if res.validation_severity:
        facts.append({"t": "validation", "f": f"재무검증:{res.validation_severity}", "src": "validation", "conf": "high", "v": res.validation_severity})

    # 포트폴리오 집중 리스크
    if weight_pct is not None and weight_pct >= 60:
        facts.append({"t": "risk_flag", "f": f"포트폴리오 비중 {weight_pct:.1f}% — 집중도 높음", "src": "allocation", "conf": "high", "v": weight_pct})

    # 종목 속성 (업종 특성 — 재무 데이터 아님)
    _ATTR = {"경기민감", "변동성높음", "고PER", "바이오", "블록체인", "암호화폐"}
    for tag in set(candidate.tags) & _ATTR:
        facts.append({"t": "sector_attr", "f": f"종목속성:{tag}", "src": "candidate_pool", "conf": "medium", "v": tag})

    return facts


# timing 플래그 → 사용자 친화적 레이블 (주어 조사 포함)
_TIMING_FLAG_LABELS: dict[str, str] = {
    "정배열":    "단기 이동평균 정배열이",
    "MACD상승":  "MACD 단기 모멘텀 개선 신호가",
    "RSI적정":   "RSI 적정 구간이",
    "골든크로스": "이동평균 골든크로스가",
    "거래량급증": "거래량 급증이",
    "조정구간":  "52주 고점 대비 조정 구간이",
    "BB하단반등": "볼린저밴드 하단 반등 신호가",
    "BB하단권":  "볼린저밴드 하단권 위치가",
    "RSI과매도":  "RSI 과매도 구간이",
}
_TIMING_NEG_LABELS: dict[str, str] = {
    "역배열":      "단기 이동평균 역배열이",
    "MACD하락":    "MACD 하락 모멘텀이",
    "RSI과매수":   "RSI 과매수 구간이",
    "BB과열":      "볼린저밴드 상단 돌파 과열이",
    "데드크로스":  "이동평균 데드크로스가",
    "거래량부재":  "거래량 부족 상태가",
    "52주고점근접": "52주 신고가 근접 구간이",
    "장기하락":    "장기 하락 추세가",
    "BB상단근접":  "볼린저밴드 상단 근접이",
}
# 업종별 사업 특성 문장 (4순위 — 단정 금지, "영향을 받을 수 있습니다" 수준)
_SECTOR_CHAR: dict[str, str] = {
    "조선":     "글로벌 선박 발주 및 수주 사이클에 영향을 받을 수 있습니다.",
    "반도체":   "반도체 업황 사이클 및 고객사 설비 투자에 영향을 받을 수 있습니다.",
    "2차전지":  "전기차 수요 및 배터리 소재 가격 변동에 영향을 받을 수 있습니다.",
    "방산":     "글로벌 방산 예산 및 수출 계약 성사 여부에 영향을 받을 수 있습니다.",
    "바이오":   "임상 진행 상황 및 규제 당국 결정에 영향을 받을 수 있습니다.",
    "금융":     "금리 변동 및 신용 사이클에 영향을 받을 수 있습니다.",
    "자동차":   "글로벌 자동차 수요 및 EV 전환 속도에 영향을 받을 수 있습니다.",
    "IT플랫폼": "광고 경기 및 플랫폼 규제 환경에 영향을 받을 수 있습니다.",
    "테크":     "AI 투자 사이클 및 기술 수요 변동에 영향을 받을 수 있습니다.",
    "에너지":   "유가 및 에너지 정책 변화에 영향을 받을 수 있습니다.",
    "화학":     "원유 및 원자재 가격 변동에 영향을 받을 수 있습니다.",
    "건설":     "부동산 경기 및 금리 변동에 영향을 받을 수 있습니다.",
    "항공":     "유가 및 여객 수요 변동에 영향을 받을 수 있습니다.",
    "엔터":     "아티스트 활동 및 콘텐츠 흥행 결과에 영향을 받을 수 있습니다.",
    "뷰티":     "중국 소비 경기 및 글로벌 소비 트렌드에 영향을 받을 수 있습니다.",
    "소비재":   "소비 경기 및 원자재 비용 변동에 영향을 받을 수 있습니다.",
    "통신":     "가입자 경쟁 및 설비 투자 사이클에 영향을 받을 수 있습니다.",
    "로봇":     "자동화 투자 수요 및 기술 상용화 속도에 영향을 받을 수 있습니다.",
    "의료기기": "수출 허가 및 글로벌 의료 수요에 영향을 받을 수 있습니다.",
    "기계":     "설비 투자 사이클 및 수출 수요에 영향을 받을 수 있습니다.",
    "철강":     "철강 가격 및 글로벌 수요 변동에 영향을 받을 수 있습니다.",
    "해운":     "글로벌 물동량 및 운임 사이클에 영향을 받을 수 있습니다.",
    "리츠":     "금리 변동 및 부동산 시장 환경에 영향을 받을 수 있습니다.",
    "산업재":   "글로벌 제조업 투자 및 경기 사이클에 영향을 받을 수 있습니다.",
}


def _generate_reasons(
    candidate: Candidate,
    user: UserInput,
    result: LightResult,
    verified_facts: list[dict] | None = None,
    buy_type: str = "확인 후 매수형",
) -> tuple[list[str], list[str]]:
    """
    verified_facts 우선 사용. 없는 내용은 추천 이유로 만들지 않음.
    candidate.tags는 업종/사업 특성 설명 보조 용도로만 사용.
    우선순위: 1) DART/KIS/yfinance 긍정 fact  2) chart timing  3) 업종 특성
    """
    facts = verified_facts or []
    tags = set(candidate.tags)
    reasons: list[str] = []
    risks: list[str] = []

    def _ft(type_: str) -> list[dict]:
        return [f for f in facts if f.get("t") == type_]

    # ── 1순위: DART/KIS/yfinance 확인된 긍정 fact ─────────────────

    # 관심 업종 매칭 (구조적)
    for ps in user.prefer_sectors:
        strength = _sector_match_strength(candidate, ps)
        if strength == "direct":
            reasons.append(f"관심 업종으로 선택한 {ps} 종목입니다.")
            break
        if strength == "tag":
            display = candidate.sector
            if display and display != ps:
                from app.services.recommender.candidate_pool import SECTOR_ALIASES
                core_aliases = SECTOR_ALIASES.get(ps, set()) | {ps}
                matched_tag = next((t for t in candidate.tags if t in core_aliases), None)
                tag_desc = _TAG_BUSINESS_DESC.get(matched_tag or ps, f"{matched_tag or ps} 관련 사업")
                reasons.append(f"{display}으로 분류되어 있으나, {tag_desc} 노출이 있는 후보입니다.")
            else:
                reasons.append(f"관심 업종({ps})과 연관성이 있는 후보입니다.")
            break

    # 영업이익 YoY 개선 (DART 확정치, > 10%)
    if len(reasons) < 4:
        for f in _ft("financial"):
            if "영업이익 YoY" in f["f"] and f.get("v", 0) > 10:
                reasons.append(
                    f"DART 기준 {f['f']}가 확인되어 실적 개선 흐름이 실제 데이터로 확인되었습니다."
                )
                break

    # 매출액 YoY 개선 (> 10%, 영업이익 fact가 없을 때만)
    if len(reasons) < 4:
        op_confirmed = any(
            "영업이익 YoY" in f["f"] and f.get("v", 0) > 10 for f in _ft("financial")
        )
        if not op_confirmed:
            for f in _ft("financial"):
                if "매출액 YoY" in f["f"] and f.get("v", 0) > 10:
                    reasons.append(
                        f"DART 기준 {f['f']}가 확인되어 매출 성장이 데이터로 뒷받침됩니다."
                    )
                    break

    # 밸류에이션 — 수치 언급만, 단정 없음 (업종 비교 없이는 낮다/높다 금지)
    if len(reasons) < 4:
        per_f = next((f for f in _ft("valuation") if "PER" in f["f"]), None)
        if per_f:
            src = per_f.get("src", "KIS")
            reasons.append(
                f"{src} 기준 {per_f['f']}가 확인되어 밸류에이션 참고 지표로 활용되었습니다."
            )

    # 배당 — 2% 이상 확인 시 표현, 단정 없음
    if len(reasons) < 4:
        div_f = next(iter(_ft("dividend")), None)
        if div_f and div_f.get("v", 0) >= 2.0:
            src = div_f.get("src", "KIS")
            reasons.append(
                f"{src} 기준 {div_f['f']}가 확인되어 배당 이력 참고 지표로 활용되었습니다."
            )

    # 선호 스타일 (구조적)
    if len(reasons) < 4:
        matched_styles = [s for s in user.prefer_styles if s in tags]
        if matched_styles:
            reasons.append(f"선호 스타일({', '.join(matched_styles)})에 부합하는 종목입니다.")

    # ── 2순위: chart indicator timing fact ─────────────────────────
    if len(reasons) < 4 and result.timing_flags:
        pos_flags = [f["f"] for f in _ft("timing") if f["f"] in _POSITIVE_TIMING]
        if result.timing_score >= 4 and pos_flags:
            flag = pos_flags[0]
            label = _TIMING_FLAG_LABELS.get(flag, f"{flag}이")
            reasons.append(
                f"차트 지표상 {label} 확인되었지만, "
                f"단기 변동성을 고려해 {buy_type}으로 분류되었습니다."
            )

    # ── 4순위: 업종 특성 (단정 금지 — "영향을 받을 수 있습니다" 수준) ─
    if len(reasons) < 4:
        char_msg = _SECTOR_CHAR.get(candidate.sector)
        if char_msg:
            reasons.append(f"{candidate.sector} 업종 특성상 {char_msg}")

    # ── 리스크 ────────────────────────────────────────────────────

    # 1. 재무 검증 이슈 (실제 validation 데이터)
    val_f = next(iter(_ft("validation")), None)
    if val_f:
        sev = str(val_f.get("v", ""))
        if "severe" in sev:
            risks.append("재무 데이터 검증 이슈(severe) — 수치 신뢰도 확인이 필요합니다.")
        elif "medium" in sev:
            risks.append("재무 데이터 일부 불일치 — 공시 원문 대조를 권장합니다.")

    # 2. 부채비율 (DART, 200% 초과)
    if len(risks) < 2:
        for f in _ft("financial"):
            if "부채비율" in f["f"] and f.get("v", 0) > 200:
                risks.append(
                    f"DART 기준 부채비율이 {f['v']:.1f}%로 확인되어, "
                    "재무 레버리지 부담을 별도로 점검할 필요가 있습니다."
                )
                break

    # 3. 영업이익 감소 (DART, < -10%)
    if len(risks) < 2:
        for f in _ft("financial"):
            if "영업이익 YoY" in f["f"] and f.get("v", 0) < -10:
                risks.append(f"DART 기준 {f['f']}로, 실적 부진이 데이터로 확인됩니다.")
                break

    # 4. 종목 속성 리스크 (업종 구조적 특성)
    if len(risks) < 2:
        attr_vals = {f.get("v", "") for f in _ft("sector_attr")}
        if "변동성높음" in attr_vals:
            risks.append("단기 변동성이 높은 종목 특성이 있어 손실 가능성을 별도로 고려해야 합니다.")
        elif "경기민감" in attr_vals:
            risks.append(
                "경기 사이클에 민감한 업종 특성이 있어 업황 둔화 시 변동성이 커질 수 있습니다."
            )
        elif "바이오" in attr_vals:
            risks.append("임상·규제 결과에 따라 주가 변동이 클 수 있습니다.")
        elif "블록체인" in attr_vals or "암호화폐" in attr_vals:
            risks.append("규제 리스크와 가격 변동성이 매우 큰 자산군입니다.")

    # 5. 기술적 부정 신호 (timing_flags 있을 때만)
    if len(risks) < 2 and result.timing_flags:
        neg_flags = [f["f"] for f in _ft("timing") if f["f"] in _NEGATIVE_TIMING]
        if result.timing_score <= -4 and neg_flags:
            flag = neg_flags[0]
            label = _TIMING_NEG_LABELS.get(flag, f"{flag}이")
            risks.append(f"차트 지표상 {label} 확인되었습니다. 진입 시점 확인이 필요합니다.")

    # 6. 포트폴리오 집중 리스크
    if len(risks) < 2:
        conc_f = next((f for f in _ft("risk_flag") if "집중" in f["f"]), None)
        if conc_f:
            risks.append(conc_f["f"])

    # fallback
    if not risks:
        fallback = _SECTOR_FALLBACK_RISKS.get(candidate.sector)
        if fallback:
            risks.append(f"업종 특성상 확인 필요: {fallback}")
        else:
            risks.append("투자 전 관련 공시 및 시황을 직접 확인하세요.")

    return reasons[:4], risks[:2]


# ──────────────────────────────────────────────
# 소액 기준 추천 개수 상한
# ──────────────────────────────────────────────
def _resolve_recommend_count(amount: int, recommend_count: int | None) -> int:
    if recommend_count is not None:
        return max(1, min(5, recommend_count))
    if amount < 100_000:
        return 2
    if amount < 500_000:
        return 3
    return 5


# ──────────────────────────────────────────────
# 제외 이유 생성
# ──────────────────────────────────────────────
def _exclusion_reason(
    candidate: Candidate,
    user: UserInput,
    result: LightResult,
    reason_type: str,
    final_candidates: list[Candidate] | None = None,
) -> str:
    if reason_type == "sector_overlap":
        if final_candidates:
            same_sector = [c for c in final_candidates if c.sector == candidate.sector]
            if same_sector:
                _GENERIC = {"수출", "내수", "배당", "성장주", "방어주", "경기민감", "저PER", "고PER"}
                c_theme = set(candidate.tags) - _GENERIC
                parts: list[str] = []
                for sc in same_sector[:2]:
                    shared = c_theme & (set(sc.tags) - _GENERIC)
                    theme = f"(공통 테마: {', '.join(list(shared)[:2])})" if shared else ""
                    parts.append(f"{sc.name}{theme}")
                overlap_desc = ", ".join(parts)
                return (
                    f"{overlap_desc}와 {candidate.sector} 업종·역할 중복 — "
                    "포트폴리오 분산 효과 감소로 제외"
                )
        return f"업종({candidate.sector}) 중복으로 분산 효과 감소 — 점수는 높았으나 제외"
    if reason_type == "validation":
        return "재무 데이터 검증 이슈(severe) 감지 — 신뢰도 하락으로 탈락"
    if reason_type == "count_limit":
        return "점수 기준은 충족했으나 추천 개수 제한 및 포트폴리오 구성 기준으로 제외"
    if reason_type == "theme_diversity":
        dominant = ", ".join(t for t in _DOMINANT_TAGS if t in set(candidate.tags))
        return (
            f"AI/테크 테마 중복 완화 기준으로 제외 — "
            f"포트폴리오 분산 효과 개선을 위해 교체됨"
            + (f" ({dominant} 태그 과다)" if dominant else "")
        )
    if reason_type == "prefer_displaced":
        return "관심 업종 우선 보장 과정에서 제외 — 선택된 업종 후보로 교체됨"
    if reason_type == "score_low":
        tags = set(candidate.tags)
        if "고PER" in tags and user.risk == "conservative":
            return "보수적 성향에서 고PER 종목은 부담 — 종합 점수 미달로 제외"
        if "변동성높음" in tags and user.period == "long":
            return "장기 투자 성향에서 높은 변동성은 부적합 — 종합 점수 미달로 제외"
        return "종합 점수 미달로 최종 후보에서 제외"
    return "조건 미충족으로 제외"


# ──────────────────────────────────────────────
# 관심 업종 최소 보장 후처리
# ──────────────────────────────────────────────
def _ensure_sector_coverage(
    final_pairs: list[tuple[Candidate, float]],
    eligible: list[tuple[Candidate, float, LightResult]],
    prefer_sectors: list[str],
    final_n: int,
    max_per_sector: int = 2,
    gap_threshold: float = 15,
) -> tuple[list[tuple[Candidate, float]], int]:
    """
    apply_diversity() 결과에서 관심 업종이 빠진 경우 후처리로 보정.

    - 하드 컷 종목은 eligible에 이미 없으므로 자동 제외
    - 추천 개수(final_n)는 유지, max_per_sector 제약도 유지
    - 관심 업종 수 > 추천 개수면 추천 개수만큼만 커버
    - Returns: (보정된 final_pairs, 커버된 관심 업종 수)
    """
    if not prefer_sectors:
        covered = 0
        return final_pairs, covered

    result = list(final_pairs)
    final_tickers = {c.ticker for c, _ in result}

    def _sector_count(pairs: list[tuple[Candidate, float]], sector: str) -> int:
        return sum(1 for c, _ in pairs if matches_sector(c, sector))

    # 현재 final에서 커버된 관심 업종
    covered_sectors: set[str] = set()
    for c, _ in result:
        for s in prefer_sectors:
            if matches_sector(c, s):
                covered_sectors.add(s)

    uncovered = [s for s in prefer_sectors if s not in covered_sectors]

    # 관심 업종 수가 추천 개수보다 많으면 추천 개수만큼만 커버 시도
    max_to_cover = min(len(uncovered), final_n)

    for sector in uncovered[:max_to_cover]:
        # alias 기반 매칭 — eligible 전체에서 해당 업종 최고점 후보 탐색
        sector_candidates = [
            (c, s) for c, s, _ in eligible
            if matches_sector(c, sector) and c.ticker not in final_tickers
        ]
        if not sector_candidates:
            continue

        best_c, best_s = max(sector_candidates, key=lambda x: x[1])

        # max_per_sector 초과 방지
        if _sector_count(result, sector) >= max_per_sector:
            continue

        # 교체 대상 우선순위:
        #  1순위) 비관심 업종 중 같은 섹터가 이미 2개 이상인(중복) 최하위
        #  2순위) 비관심 업종 최하위
        sector_counts: dict[str, int] = {}
        for c, _ in result:
            sector_counts[c.sector] = sector_counts.get(c.sector, 0) + 1

        duplicate_non_prefer = [
            (i, c, s) for i, (c, s) in enumerate(result)
            if sector_counts.get(c.sector, 0) >= 2
            and not any(matches_sector(c, ps) for ps in prefer_sectors)
        ]
        non_prefer = [
            (i, c, s) for i, (c, s) in enumerate(result)
            if not any(matches_sector(c, ps) for ps in prefer_sectors)
        ]
        targets = duplicate_non_prefer if duplicate_non_prefer else non_prefer
        if not targets:
            continue

        target_i, target_c, target_s = min(targets, key=lambda x: x[2])

        # 품질 체크: 누락 업종 최고 후보가 교체 대상보다 gap_threshold 이상 낮으면 교체 안 함
        if best_s < target_s - gap_threshold:
            continue

        result[target_i] = (best_c, best_s)
        final_tickers.discard(target_c.ticker)
        final_tickers.add(best_c.ticker)
        covered_sectors.add(sector)

    covered_count = sum(
        1 for s in prefer_sectors
        if any(matches_sector(c, s) for c, _ in result)
    )
    return result, covered_count


# ──────────────────────────────────────────────
# 테마 독식 방지 — AI 등 공통 태그 과다 반복 억제
# ──────────────────────────────────────────────
# TODO: 후보 풀 확대 시 "SaaS", "스트리밍" 등 추가 검토
_DOMINANT_TAGS: set[str] = {"AI"}

def _apply_tag_diversity(
    final_pairs: list[tuple[Candidate, float]],
    eligible: list[tuple[Candidate, float, LightResult]],
    user: UserInput,
    protected_tickers: set[str] | None = None,
    max_dominant_tag: int = 2,
    gap_threshold: float = 15.0,
) -> tuple[list[tuple[Candidate, float]], set[str]]:
    """
    _DOMINANT_TAGS 태그 종목이 max_dominant_tag 초과 시 최하위 후보를 교체.
    - IT/반도체 관심 업종 선택 시 한도 +1 완화.
    - protected_tickers(관심 업종 보장 종목)는 교체 대상 제외.
    - 대안 종목이 gap_threshold 이상 낮으면 교체하지 않음.
    반환: (교체된 final_pairs, 테마 중복으로 밀려난 ticker 집합)
    """
    _AI_RELATED_SECTORS = {"IT", "반도체", "로봇", "테크"}
    is_ai_preferred = any(ps in _AI_RELATED_SECTORS for ps in user.prefer_sectors)
    effective_max = max_dominant_tag + (1 if is_ai_preferred else 0)
    protected = protected_tickers or set()

    result = list(final_pairs)
    final_tickers = {c.ticker for c, _ in result}
    theme_displaced: set[str] = set()

    for dtag in _DOMINANT_TAGS:
        tag_count = sum(1 for c, _ in result if dtag in c.tags)
        if tag_count <= effective_max:
            continue

        # 보호 종목 제외하고 점수 오름차순 → 최하위부터 교체 시도
        tag_holders = sorted(
            [(i, c, s) for i, (c, s) in enumerate(result)
             if dtag in c.tags and c.ticker not in protected],
            key=lambda x: x[2],
        )
        excess = tag_count - effective_max

        for idx, displaced_c, displaced_s in tag_holders[:excess]:
            alternatives = [
                (c, s) for c, s, _ in eligible
                if c.ticker not in final_tickers
                and dtag not in c.tags
                and s >= displaced_s - gap_threshold
            ]
            if not alternatives:
                continue
            best_c, best_s = max(alternatives, key=lambda x: x[1])
            result[idx] = (best_c, best_s)
            final_tickers.discard(displaced_c.ticker)
            final_tickers.add(best_c.ticker)
            theme_displaced.add(displaced_c.ticker)

    return result, theme_displaced


# ──────────────────────────────────────────────
# 선정 근거 요약 (종목당 2~4줄)
# ──────────────────────────────────────────────
def _make_selection_summary(
    candidate: Candidate,
    user: UserInput,
    res: LightResult,
    score: float,
    buy_type: str,
    is_sector_guaranteed: bool = False,
) -> list[str]:
    lines: list[str] = []

    # 1. 관심 업종 — 매칭 강도 반영
    for ps in user.prefer_sectors:
        strength = _sector_match_strength(candidate, ps)
        if strength == "none":
            continue
        if is_sector_guaranteed:
            lines.append("관심 업종 우선 조건을 반영해 최종 후보로 선정되었습니다.")
        elif strength == "direct":
            lines.append(f"관심 업종({ps})에 해당하는 후보입니다.")
        else:  # tag
            lines.append(
                f"관심 업종({ps})과 직접 일치하지는 않으나, 연관 서비스를 보유한 후보입니다."
            )
        break

    # 2. 데이터 검증
    if res.confidence_score is not None and res.confidence_score >= 70:
        lines.append("재무 데이터 신뢰도가 높아 분석 기반이 탄탄합니다.")
    else:
        lines.append("재무 데이터 검증 기준을 통과해 분석 가능한 후보로 남았습니다.")

    # 3. 점수 해석
    if score >= 80:
        lines.append("종합 점수가 높아 우선 검토 대상으로 분류되었습니다.")
    elif score >= 60:
        if buy_type == "즉시 검토형":
            # 관심 업종이 있고 tag match인 경우에만 "업종 직접 일치 여부" 문구 사용
            is_tag_match = user.prefer_sectors and any(
                _sector_match_strength(candidate, ps) == "tag"
                for ps in user.prefer_sectors
            )
            if is_tag_match:
                lines.append("점수는 양호하며 타이밍 기준도 우호적이나, 업종 직접 일치 여부는 확인이 필요합니다.")
            else:
                lines.append("점수는 양호하며 타이밍 기준도 우호적이나, 단기 변동성과 업종 사이클은 확인이 필요합니다.")
        else:
            lines.append("종합 점수는 양호하나 추가 확인이 필요한 후보입니다.")
    else:
        lines.append(f"종합 점수는 중간 수준({score:.0f}점)으로, 조건 충족을 중심으로 선정된 후보입니다.")

    # 4. buy_type (3줄 이상이어도 항상 포함 — 핵심 정보)
    if len(lines) < 4:
        if buy_type == "즉시 검토형":
            lines.append("점수와 타이밍이 모두 양호해 즉시 검토형으로 분류되었습니다.")
        elif buy_type == "확인 후 매수형":
            lines.append("강한 매수 신호보다는 추가 확인이 필요해 확인 후 매수형으로 분류되었습니다.")
        elif buy_type == "관찰 우선형":
            lines.append("현재는 매수보다 관찰이 우선인 후보로 분류되었습니다.")

    # 5. timing — 최대 1줄, 4줄 초과 방지
    if res.timing_summary and len(lines) < 4:
        if res.timing_score >= 4:
            lines.append(f"기술적 지표상 진입 타이밍이 양호합니다 ({res.timing_summary}).")
        elif res.timing_score <= -4:
            lines.append("기술적 지표상 일부 부정 신호가 있어 진입 시점 확인이 필요합니다.")

    return lines[:4]


# ──────────────────────────────────────────────
# Claude 최종 리포트 생성
# ──────────────────────────────────────────────
async def _claude_recommend_report(
    user: UserInput,
    recommendations: list[dict],
    excluded: list[dict],
    split_plan: list[dict],
    selection_notes: list[str] | None = None,
) -> str:
    rec_text = "\n".join(
        "- {name}({ticker}) [buy_type: {bt}]\n"
        "  선정근거: {summary}\n"
        "  추천이유: {reasons} | 리스크: {risks}\n"
        "  검증사실: {facts}".format(
            name=r["name"], ticker=r["ticker"], bt=r["buy_type"],
            summary=" / ".join(r.get("selection_summary", [])),
            reasons=", ".join(r["reasons"]),
            risks=", ".join(r["risks"]),
            facts="; ".join(
                f["f"] for f in r.get("verified_facts", [])
                if f.get("t") in {"financial", "valuation", "dividend", "timing"}
            ) or "데이터 없음",
        )
        for r in recommendations
    )
    exc_text = "\n".join(
        f"- {e['name']}({e['ticker']}): {e['exclusion_reason']}"
        for e in excluded
    )
    split_text = "\n".join(
        f"- {s['name']}: {s['weight_pct']}% ({s['amount_krw']:,}원)"
        for s in split_plan
    )

    system = (
        "너는 친절하고 보수적인 투자 어시스턴트다. "
        "아래 구조화 데이터는 백엔드 알고리즘이 이미 결정한 결과야. "
        "네 역할은 이 결과를 자연스러운 언어로 설명하는 것이지, 재판단하거나 수치를 바꾸는 게 아니야. "
        "절대로: buy_type 변경 금지 / 비중·금액 수치 변경 금지 / 제외 종목을 추천으로 바꾸거나 그 반대 금지. "
        "verified_facts, reasons, risks에 없는 구체적 사실은 생성하지 마. "
        "입력에 없는 실적 수치, 계약명, 수주액, 날짜, 뉴스는 절대 쓰지 마. "
        "과장하지 말고, 불확실한 것은 불확실하다고 말해라. "
        "한국어로 작성하고, 섹션 구조를 유지해라."
    )

    risk_label = {"conservative": "보수적", "neutral": "중립", "aggressive": "공격적"}.get(user.risk, user.risk)
    period_label = {"short": "단기(3개월 이내)", "medium": "중기(3개월~1년)", "long": "장기(1년 이상)"}.get(user.period, user.period)

    notes_text = ""
    if selection_notes:
        notes_text = "\n[알고리즘 선택 메모 — 이 내용을 리포트에 반영할 것]\n" + "\n".join(
            f"- {n}" for n in selection_notes
        )

    user_msg = f"""[추천 조건]
투자금: {user.amount:,}원 | 성향: {risk_label} | 기간: {period_label} | 시장: {user.market}
관심업종: {', '.join(user.prefer_sectors) or '없음'} | 선호스타일: {', '.join(user.prefer_styles) or '없음'} | 제외: {', '.join(user.exclude_tags) or '없음'}
{notes_text}

[추천 후보 — 알고리즘 확정 결과, buy_type과 비중은 변경 불가]
{rec_text}

[제외 후보 — 변경 불가]
{exc_text}

[분할매수 계획 — 변경 불가]
{split_text}

위 데이터를 그대로 사용해서 아래 형식으로 리포트를 작성해라.
buy_type, 비중(%), 금액은 위 값 그대로 인용하고 다른 수치를 제시하지 마라.

[작성 규칙]
- 추천 종목 설명은 selection_summary를 중심으로 2~4줄 이내로 요약하라. 길게 늘리지 마라.
- 없는 근거를 새로 만들지 마라. selection_summary·추천이유·리스크에 있는 내용만 사용하라.
- 검증사실(verified_facts)에 없는 재무 수치, 실적 개선, 계약명, 뉴스는 절대 생성하지 마라.
- 종합 점수 70 미만 종목을 강한 추천처럼 표현하지 마라.
- 관심 업종 보장으로 선정된 경우(선정근거에 "우선 조건" 포함) "조건 충족 후보"라고 표현하라.
- buy_type의 이유는 한 줄로만 설명하라.

## 추천 요약
(이 조건에서 이 후보들이 선택된 배경을 한 줄로)

## 추천 종목
(각 종목별 — **선정 근거 요약** 블록을 종목명 바로 아래에 bullets로 표시, 알고리즘이 정한 buy_type 그대로 명시, 리스크 1~2줄)

## 제외된 후보
(제외 이유를 납득 가능하게 설명. 구체적으로 어떤 포함 종목과 역할·테마가 겹쳤는지 설명할 것)

## 업종 미편입 / 알고리즘 주의사항
(알고리즘 선택 메모가 있을 경우에만 이 섹션을 작성. 없으면 섹션 전체 생략.
 관심 업종 미편입 사유, 소프트 충돌 안내 등을 자연스럽게 한국어로 설명)

## 분할매수 제안
(알고리즘이 정한 비중과 금액 그대로 안내)"""

    try:
        msg = await _claude_client.messages.create(
            model="claude-sonnet-4-6",
            max_tokens=2000,
            system=system,
            messages=[{"role": "user", "content": user_msg}],
        )
        return msg.content[0].text
    except Exception:
        return ""


# ──────────────────────────────────────────────
# 메인 오케스트레이터
# ──────────────────────────────────────────────
async def recommend_stocks(
    user: UserInput,
    kis_dynamic: list[Candidate] | None = None,
    price_map: dict[str, int] | None = None,
) -> dict:
    """
    사용자 조건 → 추천 결과 dict 반환.

    반환 스키마:
    {
      "summary": str,
      "recommendations": [
        { ticker, name, sector, tier, reasons, risks, buy_type,
          weight_pct, amount_krw, split }
      ],
      "excluded": [
        { ticker, name, sector, exclusion_reason }
      ],
      "split_buy_plan": [...],
      "notes": str,
      "report": str  (Claude 생성 리포트)
    }
    """
    # ── 관심/제외 업종 충돌 사전 감지 ─────────
    selection_notes: list[str] = []

    # US 소액 경고: $50(약 70,000원) 미만이면 대부분의 US 종목을 1주도 살 수 없음
    _US_MIN_KRW = 70_000
    if user.market in ("US", "ALL") and user.amount < _US_MIN_KRW:
        selection_notes.append(
            f"[소액 경고] 투자금 {user.amount:,}원은 해외 주식 1주 구매에 부족할 가능성이 높습니다. "
            f"(기준: 약 {_US_MIN_KRW:,}원 ≈ $50 USD). "
            "가격 조회 실패 종목이 후보에 포함될 수 있으니 실제 주가를 반드시 확인하세요."
        )

    conflicting = [ps for ps in user.prefer_sectors if ps in user.exclude_sectors]
    if conflicting:
        selection_notes.append(
            f"조건 충돌: 관심 업종({', '.join(conflicting)})이 제외 업종에도 포함되어 있습니다. "
            "제외 조건이 우선 적용되어 해당 업종은 추천되지 않습니다."
        )

    # ── Phase 1: 사전 점수화 ─────────────────
    shortlist = build_candidate_shortlist(user, kis_dynamic=kis_dynamic, price_map=price_map, top_n=20)
    if not shortlist:
        return {
            "summary": "조건에 맞는 후보가 없습니다.",
            "recommendations": [],
            "excluded": [],
            "split_buy_plan": [],
            "notes": "조건을 변경하거나 제외 태그를 줄여보세요.",
            "report": "",
        }

    # ── Phase 2: 경량 분석 (병렬) ────────────
    batch_results = await _light_analyze_batch(shortlist)

    # post_score 기준 정렬
    post_sorted = sorted(batch_results, key=lambda x: x[1], reverse=True)

    # 가격 접근성 필터
    def _affordable(c: Candidate, res: LightResult) -> bool:
        if res.current_price is None or res.current_price <= 0:
            # US 종목: 가격 조회 실패 시 보수적 판단 — 70,000원(~$50) 이상이어야 통과
            if c.market == "US":
                return user.amount >= _US_MIN_KRW
            return True  # KR 종목은 가격 미확인 시 기존대로 통과
        return res.current_price <= user.amount

    post_sorted = [(c, s, r) for c, s, r in post_sorted if _affordable(c, r)]

    # 하드 컷: score < 0 또는 severe validation → excluded로 직접 분리
    hard_cut: list[tuple[Candidate, float, LightResult]] = []
    eligible: list[tuple[Candidate, float, LightResult]] = []
    for c, s, r in post_sorted:
        if s < 0 or r.validation_severity == "severe":
            hard_cut.append((c, s, r))
        else:
            eligible.append((c, s, r))

    # ── Phase 2.5: timing_score (eligible 상위 12개 차트 기반) ───
    _TIMING_TOP_K = 12
    timing_map = await _fetch_timing_batch(eligible[:_TIMING_TOP_K])

    # timing_score를 LightResult에 기록 + 총점 합산 후 재정렬
    updated_eligible: list[tuple[Candidate, float, LightResult]] = []
    for c, s, r in eligible:
        if c.ticker in timing_map:
            t_score, t_flags, t_summary = timing_map[c.ticker]
            r.timing_score = t_score
            r.timing_flags = t_flags
            r.timing_summary = t_summary
            total = s + t_score
        else:
            total = s
        updated_eligible.append((c, total, r))

    updated_eligible.sort(key=lambda x: x[1], reverse=True)
    eligible = updated_eligible

    # ── Phase 3: 다양성 제어 → 최종 2~5개 ───
    final_n = _resolve_recommend_count(user.amount, user.recommend_count)
    scored_pairs = [(c, s) for c, s, _ in eligible]
    final_pairs = apply_diversity(scored_pairs, final_n=final_n)

    # ── Phase 3-B: 관심 업종 최소 보장 후처리 ───
    pre_diversity_set = {c.ticker for c, _ in final_pairs}
    final_pairs, sector_coverage = _ensure_sector_coverage(
        final_pairs, eligible, user.prefer_sectors, final_n
    )
    sector_guaranteed_tickers = {c.ticker for c, _ in final_pairs} - pre_diversity_set

    # ── Phase 3-C: 테마 독식 방지 (sector 보장 후 적용) ───
    final_pairs, theme_displaced_tickers = _apply_tag_diversity(
        final_pairs, eligible, user,
        protected_tickers=sector_guaranteed_tickers,
    )

    final_set = {c.ticker for c, _ in final_pairs}

    # ── 제외 조건-추천 결과 소프트 충돌 caution flag ──────────
    # exclude_tags는 hard ban이지만 동일 업종 내 해당 tag 없는 종목은 통과함
    # 예: "경기민감" 제외 → SK하이닉스 제외, 삼성전자(태그 없음)는 통과 → 사용자 혼동 방지
    _CYCLICAL_SECTORS = {"반도체", "2차전지", "화학", "자동차", "조선", "건설", "에너지", "항공", "해운"}
    for excl_tag in user.exclude_tags:
        if excl_tag in _CYCLICAL_SECTORS or excl_tag == "경기민감":
            for c, _ in final_pairs:
                if c.sector in _CYCLICAL_SECTORS and excl_tag not in c.tags:
                    selection_notes.append(
                        f"[소프트 충돌 안내] {c.name}({c.sector})은 '{excl_tag}' 태그가 없어 "
                        f"제외 필터를 통과했으나, {c.sector} 업종은 업황 사이클에 민감합니다. "
                        "경기 하강 국면에서 변동성이 커질 수 있으므로 확인이 필요합니다."
                    )

    # ── 관심 업종 미커버 사유 기록 ─────────────
    _gap_threshold = 15  # _ensure_sector_coverage와 동일 기준
    min_final_score = min((s for _, s in final_pairs), default=0)

    for ps in user.prefer_sectors:
        if any(matches_sector(c, ps) for c, _ in final_pairs):
            continue  # 커버됨

        sector_eligible = [(c, s) for c, s, _ in eligible if matches_sector(c, ps)]
        ps_in_hardcut   = any(matches_sector(c, ps) for c, _, _ in hard_cut)

        if not sector_eligible and not ps_in_hardcut:
            selection_notes.append(
                f"관심 업종({ps}): 현재 후보 데이터베이스에 해당 업종이 없거나 "
                "제외 태그·시장 필터에 의해 사전 제거되었습니다."
            )
        elif not sector_eligible:
            selection_notes.append(
                f"관심 업종({ps}): 후보가 검토되었으나 재무 데이터 검증 이슈(severe)로 "
                "하드컷 처리되어 최종 후보군에서 제외되었습니다."
            )
        else:
            best_c_ps, best_s_ps = max(sector_eligible, key=lambda x: x[1])
            gap = min_final_score - best_s_ps
            if gap > _gap_threshold:
                selection_notes.append(
                    f"관심 업종({ps}) 최고 후보: {best_c_ps.name} (점수 {best_s_ps:.0f}점). "
                    f"최종 편입 컷 {min_final_score:.0f}점 대비 {gap:.0f}점 낮아 "
                    f"품질 기준 미달로 미편입 — 점수 우선 정책 적용."
                )
            else:
                selection_notes.append(
                    f"관심 업종({ps}) 최고 후보: {best_c_ps.name} (점수 {best_s_ps:.0f}점, "
                    f"편입 컷 {min_final_score:.0f}점, 차이 {gap:.0f}점). "
                    "점수 기준은 통과했으나 교체 가능한 비관심 업종 슬롯이 없어 미편입."
                )

    # 제외 후보: 하드 컷 + eligible 상위 10개 중 탈락한 것
    excluded_raw = [
        (c, s, r) for c, s, r in eligible[:10]
        if c.ticker not in final_set
    ][:3]
    excluded_raw += hard_cut[:2]  # 하드 컷 항목 최대 2개 노출

    # result_map: ticker → LightResult
    result_map = {c.ticker: r for c, _, r in batch_results}

    # ── Phase 5: 분할매수 초안 (Phase 4 앞으로 이동 — weight_pct를 facts에 활용) ─
    final_candidates = [c for c, _ in final_pairs]
    split_plan = calc_split_buy(user.amount, final_candidates)
    split_map = {s["ticker"]: s for s in split_plan}

    # ── Phase 4: verified_facts 생성 + 이유 생성 ─────────────────
    recommendations = []
    for c, score in final_pairs:
        res = result_map.get(c.ticker, LightResult(c.ticker, None, None, None, {}))
        sp = split_map.get(c.ticker, {})
        facts = _build_verified_facts(c, res, sp.get("weight_pct"))

        # buy_type 먼저 결정 — _generate_reasons의 timing 문장에 반영
        t_flags = set(res.timing_flags)
        _overheated = {"RSI과매수", "BB과열", "52주고점근접"}
        _weak_trend = {"역배열", "MACD하락", "데드크로스", "장기하락"}
        is_overheated = bool(t_flags & _overheated)
        is_weak = bool(t_flags & _weak_trend) and not t_flags & {"정배열", "MACD상승"}

        if res.validation_severity == "severe" or (res.confidence_score or 0) < 30:
            buy_type = "관찰 우선형"
        elif is_weak and res.timing_score <= -4:
            buy_type = "관찰 우선형"
        elif score >= 70 and not is_overheated:
            buy_type = "즉시 검토형"
        elif score >= 70 and is_overheated:
            buy_type = "확인 후 매수형"
        elif is_weak:
            buy_type = "확인 후 매수형"
        else:
            buy_type = "확인 후 매수형"

        reasons, risks = _generate_reasons(c, user, res, facts, buy_type)
        is_guaranteed = c.ticker in sector_guaranteed_tickers
        selection_summary = _make_selection_summary(c, user, res, score, buy_type, is_guaranteed)

        recommendations.append({
            "ticker": c.ticker,
            "name": c.name,
            "sector": c.sector,
            "tier": c.tier,
            "score": round(score, 1),
            "reasons": reasons,
            "risks": risks,
            "buy_type": buy_type,
            "selection_summary": selection_summary,
            "weight_pct": sp.get("weight_pct"),
            "amount_krw": sp.get("amount_krw"),
            "split": sp.get("split", []),
            "verified_facts": facts,
        })

    excluded = []
    final_sectors = {r["sector"] for r in recommendations}
    min_final_score = min((r["score"] for r in recommendations), default=0)
    # 관심 업종 보장으로 교체된 종목 ticker 집합 (교체 전 apply_diversity 결과와 비교)
    prefer_displaced_tickers: set[str] = set()
    if user.prefer_sectors:  # 관심 업종이 없으면 prefer_displaced 사유 자체가 발생 불가
        for c, s, r in eligible[:10]:
            if c.ticker not in final_set:
                if s >= min_final_score and not any(matches_sector(c, ps) for ps in user.prefer_sectors):
                    prefer_displaced_tickers.add(c.ticker)

    for c, score, res in excluded_raw:
        if res.validation_severity == "severe" or score < 0:
            reason_type = "validation"
        elif c.ticker in theme_displaced_tickers:
            reason_type = "theme_diversity"
        elif c.ticker in prefer_displaced_tickers:
            reason_type = "prefer_displaced"
        elif c.sector in final_sectors and score >= min_final_score - 10:
            reason_type = "sector_overlap"
        elif score >= min_final_score:
            reason_type = "count_limit"
        else:
            reason_type = "score_low"
        excluded.append({
            "ticker": c.ticker,
            "name": c.name,
            "sector": c.sector,
            "score": round(score, 1),
            "exclusion_reason": _exclusion_reason(c, user, res, reason_type, [fc for fc, _ in final_pairs]),
        })

    # ── Phase 5: 분할매수 (Phase 4 이전에 계산됨) ───────────────

    # ── Phase 6: Claude 리포트 ───────────────
    report = await _claude_recommend_report(user, recommendations, excluded, split_plan, selection_notes)

    count_note = ""
    if user.recommend_count is not None and len(recommendations) < user.recommend_count:
        count_note = (
            f" 요청하신 {user.recommend_count}개 중 {len(recommendations)}개만 추천됩니다"
            f" (점수 미달 또는 재무 이슈로 일부 제외)."
        )

    sector_note = ""
    if user.prefer_sectors:
        sector_note = (
            f" 관심 업종 {len(user.prefer_sectors)}개 중 {sector_coverage}개 업종이 최종 추천에 포함됐습니다."
        )

    notes = (
        "이 추천은 정적 후보군과 공개 재무 데이터를 기반으로 한 판단 보조 결과입니다."
        + count_note
        + sector_note
        + " 투자 결정의 최종 책임은 투자자 본인에게 있습니다."
    )

    return {
        "summary": f"{user.amount:,}원 | {user.risk} | {user.period} | {user.market}",
        "recommendations": recommendations,
        "excluded": excluded,
        "split_buy_plan": split_plan,
        "notes": notes,
        "selection_notes": selection_notes,   # 관심 업종 미커버 사유, 충돌 경고 등
        "report": report,
    }
