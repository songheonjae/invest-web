"""
스코어링 엔진 — 사용자 조건 기반 후보 점수화 + 다양성 제어

점수 범위 기준:
  pre_score  : 대략 -30 ~ +60 (태그·Tier·기간 합산)
  post_score : pre + 분석 결과 추가 -50 ~ +70
"""
from dataclasses import dataclass, field
from .candidate_pool import Candidate, filter_pool, get_pool, KR_TIER1_SET, matches_sector

# ──────────────────────────────────────────────
# 사용자 입력 스키마
# ──────────────────────────────────────────────
@dataclass
class UserInput:
    amount: int                         # 투자금액 (원)
    risk: str                           # "conservative" | "neutral" | "aggressive"
    period: str                         # "short" | "medium" | "long"
    market: str                         # "KR" | "US" | "ALL"
    recommend_count: int | None = None  # None=자동, 1~5=수동
    prefer_sectors: list[str] = field(default_factory=list)  # ["반도체", "금융", ...]
    prefer_styles: list[str] = field(default_factory=list)   # ["성장주", "배당", ...]
    exclude_tags: list[str] = field(default_factory=list)    # ["변동성높음", "고PER", ...]
    exclude_sectors: list[str] = field(default_factory=list) # ["바이오", "게임", ...]
    holdings: list[str] = field(default_factory=list)        # 보유 종목 ticker

# ──────────────────────────────────────────────
# 점수 테이블
# ──────────────────────────────────────────────

# Tier 가중치: pre_score 기여 ≈ 최대 ±15
_TIER_SCORE: dict[str, dict[int, float]] = {
    "conservative": {1: 15, 2:  3, 3: -8},
    "neutral":      {1:  5, 2:  3, 3:  0},  # 대형주 독식 방지: 1/2 차이 확대, 전체 하향
    "aggressive":   {1: -3, 2:  5, 3: 10},
}

# 태그별 기본 가중치: risk profile별
# 한 종목에 태그가 보통 2~4개 → 총 기여 ≈ -20 ~ +30
_TAG_SCORE: dict[str, dict[str, float]] = {
    "conservative": {
        "배당":       12,
        "방어주":     10,
        "저PER":       8,
        "내수":        5,
        "성장주":     -3,
        "고PER":     -10,
        "변동성높음": -18,
        "경기민감":   -5,
        "턴어라운드": -5,
        "바이오":     -5,
        "블록체인":  -12,
        "암호화폐":  -15,
    },
    "neutral": {
        "배당":        5,
        "방어주":      3,
        "성장주":      5,
        "고PER":      -3,
        "변동성높음":  -8,
        "턴어라운드":  3,
        "AI":          1,  # 4→1: 조건부 추가 보너스는 pre_score에서 처리
    },
    "aggressive": {
        "성장주":     12,
        "AI":          3,  # 10→3: 조건부 추가 보너스는 pre_score에서 처리
        "턴어라운드":  8,
        "수출":        5,
        "고PER":       3,
        "배당":       -3,
        "방어주":     -8,
        "변동성높음":  5,
        "경기민감":    5,
    },
}

# 투자기간별 태그 가중치 (기여 ≈ ±10)
_PERIOD_SCORE: dict[str, dict[str, float]] = {
    "short": {
        "수급강한":   10,
        "경기민감":    5,
        "변동성높음":  3,
        "배당":       -3,
        "방어주":     -3,
    },
    "medium": {},
    "long": {
        "배당":        8,
        "성장주":      5,
        "저PER":       5,
        "방어주":      3,
        "변동성높음":  -5,
    },
}

# 관심 업종 매칭 보너스 — sector 필드 or tags 양쪽 체크, Tier1 다중태그 보너스를 넘도록 충분히 큼
_PREFER_SECTOR_BONUS = 22

# 선호 스타일 태그 매칭 보너스 (배당, 성장주 등)
_PREFER_STYLE_BONUS = 10

# 보유 종목 업종 중복 패널티
_SECTOR_OVERLAP_PENALTY = -15


# ──────────────────────────────────────────────
# 금액 기준 주가 접근성 보정
# ──────────────────────────────────────────────
def _price_accessibility_bonus(amount: int, current_price: int | None) -> float:
    if current_price is None or current_price <= 0:
        return 0.0
    if current_price > amount:
        return -15.0     # 1주도 살 수 없음
    if current_price > amount * 0.5:
        return -7.0      # 1~2주만 가능
    return 0.0


# ──────────────────────────────────────────────
# 단일 후보 사전 점수 계산
# ──────────────────────────────────────────────
def pre_score(
    candidate: Candidate,
    user: UserInput,
    holding_sectors: set[str],
    current_price: int | None = None,
) -> float:
    """
    API 호출 없이 정적 속성만으로 계산하는 사전 점수.
    목표 범위: -30 ~ +60
    """
    score = 0.0
    tags = set(candidate.tags)

    score += _TIER_SCORE[user.risk].get(candidate.tier, 0)

    for tag, w in _TAG_SCORE[user.risk].items():
        if tag in tags:
            score += w

    for tag, w in _PERIOD_SCORE[user.period].items():
        if tag in tags:
            score += w

    # AI 조건부 추가 보너스: IT/반도체/로봇 관심 업종 또는 성장주 스타일일 때만 강화
    _AI_PREFERRED_SECTORS = {"IT", "반도체", "로봇", "테크"}
    if "AI" in tags:
        is_ai_context = (
            any(ps in _AI_PREFERRED_SECTORS for ps in user.prefer_sectors)
            or "성장주" in user.prefer_styles
        )
        if is_ai_context:
            _ai_ctx_bonus: dict[str, float] = {"neutral": 4, "aggressive": 8, "conservative": 0}
            score += _ai_ctx_bonus.get(user.risk, 0)

    # alias 기반 관심 업종 매칭 (sector 직접 일치 + tags alias 확장)
    if any(matches_sector(candidate, ps) for ps in user.prefer_sectors):
        score += _PREFER_SECTOR_BONUS

    for style in user.prefer_styles:
        if style in tags:
            score += _PREFER_STYLE_BONUS

    if candidate.sector in holding_sectors:
        score += _SECTOR_OVERLAP_PENALTY

    score += _price_accessibility_bonus(user.amount, current_price)

    return score


# ──────────────────────────────────────────────
# 분석 결과 반영 후 최종 점수
# ──────────────────────────────────────────────
def post_score(
    pre: float,
    confidence_score: int | None,
    attractiveness_score: int | None,
    validation_severity: str | None,
) -> float:
    """
    analyze_stock 결과를 반영한 최종 점수.
    추가 범위: -50 ~ +70 (pre에 합산)
    """
    score = pre

    # 신뢰도 (0~80) → 최대 +25
    if confidence_score is not None:
        score += (confidence_score / 80) * 25

    # 매력도 (0~100) → 최대 +45
    if attractiveness_score is not None:
        score += (attractiveness_score / 100) * 45

    # 검증 이슈 패널티
    if validation_severity == "severe":
        score -= 50
    elif validation_severity == "medium":
        score -= 20
    elif validation_severity == "light":
        score -= 5

    return score


# ──────────────────────────────────────────────
# 후보군 필터 + 사전 점수화
# ──────────────────────────────────────────────
def build_candidate_shortlist(
    user: UserInput,
    kis_dynamic: list[Candidate] | None = None,
    price_map: dict[str, int] | None = None,
    top_n: int = 20,
) -> list[tuple[Candidate, float]]:
    """
    정적 풀 + KIS 동적 보강 → 필터 → 사전 점수화 → 상위 top_n 반환.
    """
    pools: list[Candidate] = []

    if user.market in ("KR", "ALL"):
        pools += get_pool("KR")
    if user.market in ("US", "ALL"):
        pools += get_pool("US")

    # KIS 동적 보강: KR 후보 전제, 항상 KR_TIER1_SET 기준으로 중복 제거
    if kis_dynamic:
        existing_tickers = {p.ticker for p in pools}
        new_dynamic = [
            c for c in kis_dynamic
            if c.ticker not in KR_TIER1_SET and c.ticker not in existing_tickers
        ]
        pools += new_dynamic

    filtered = filter_pool(
        pools,
        exclude_tags=user.exclude_tags,
        exclude_sectors=user.exclude_sectors,
        exclude_tickers=user.holdings,
    )

    pool_map = {c.ticker: c for c in pools}
    holding_sectors: set[str] = {
        pool_map[t].sector for t in user.holdings if t in pool_map
    }

    pm = price_map or {}
    scored = [
        (c, pre_score(c, user, holding_sectors, pm.get(c.ticker)))
        for c in filtered
    ]
    scored.sort(key=lambda x: x[1], reverse=True)

    # ALL 시장: KR/US 비율 균형 (각 최소 top_n//2개씩 확보)
    if user.market == "ALL":
        half = top_n // 2
        kr = [(c, s) for c, s in scored if c.market == "KR"][:half]
        us = [(c, s) for c, s in scored if c.market == "US"][:half]
        base = kr + us
        base.sort(key=lambda x: x[1], reverse=True)
        return _guarantee_prefer_sectors(base, scored, user)

    return _guarantee_prefer_sectors(scored[:top_n], scored, user)


def _guarantee_prefer_sectors(
    base: list[tuple[Candidate, float]],
    full_scored: list[tuple[Candidate, float]],
    user: "UserInput",
) -> list[tuple[Candidate, float]]:
    """
    shortlist에 관심 업종 후보가 없으면 해당 업종 최고점 후보 1개씩 추가.
    동일 ticker 중복 방지, scoring 순서 유지(append 방식).
    """
    if not user.prefer_sectors:
        return base

    included_tickers = {c.ticker for c, _ in base}
    extras: list[tuple[Candidate, float]] = []
    for ps in user.prefer_sectors:
        # 이미 해당 업종이 base에 있으면 스킵
        if any(matches_sector(c, ps) for c, _ in base):
            continue
        # 해당 업종 후보 중 최고점 1개 추가
        for c, s in full_scored:
            if matches_sector(c, ps) and c.ticker not in included_tickers:
                extras.append((c, s))
                included_tickers.add(c.ticker)
                break

    return base + extras


# ──────────────────────────────────────────────
# 다양성 제어 — 최종 추천 선정
# ──────────────────────────────────────────────
def apply_diversity(
    scored: list[tuple[Candidate, float]],
    max_per_sector: int = 2,
    ensure_tier_mix: bool = True,
    final_n: int = 5,
) -> list[tuple[Candidate, float]]:
    """
    점수 순 리스트에서 다양성 제약 적용 후 최종 추천 선정.

    동작 순서:
      1. 섹터 중복 제한(max_per_sector) 적용하며 순서대로 채움
      2. ensure_tier_mix=True면 아직 없는 Tier의 최고점 후보를 탐색해 1개씩 교체
         (단, gap_threshold 이상 낮은 후보는 생략, 섹터 제한도 유지)
      3. 부족하면 overflow에서 섹터 제한 재확인 후 보충
    """
    gap_threshold = 30

    sector_count: dict[str, int] = {}
    result: list[tuple[Candidate, float]] = []
    overflow: list[tuple[Candidate, float]] = []

    for c, s in scored:
        if sector_count.get(c.sector, 0) >= max_per_sector:
            overflow.append((c, s))
            continue
        sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
        result.append((c, s))
        if len(result) >= final_n:
            break

    # Tier mix 보정: 결과에 없는 Tier가 있으면 교체 시도
    if ensure_tier_mix and result:
        tiers_in_result = {c.tier for c, _ in result}
        result_tickers = {c.ticker for c, _ in result}
        remaining = [(c, s) for c, s in (overflow + scored) if c.ticker not in result_tickers]

        for missing_tier in ({1, 2, 3} - tiers_in_result):
            min_score_in_result = min(s for _, s in result)
            # 섹터 제한도 통과하는 후보만 교체 대상
            candidates_for_tier = [
                (c, s) for c, s in remaining
                if c.tier == missing_tier
                and (min_score_in_result - s) <= gap_threshold
                and sector_count.get(c.sector, 0) < max_per_sector
            ]
            if not candidates_for_tier:
                continue
            best = max(candidates_for_tier, key=lambda x: x[1])
            # 가장 낮은 점수 항목 교체 (sector_count 갱신)
            idx = min(range(len(result)), key=lambda i: result[i][1])
            old_c = result[idx][0]
            sector_count[old_c.sector] = max(0, sector_count[old_c.sector] - 1)
            sector_count[best[0].sector] = sector_count.get(best[0].sector, 0) + 1
            result[idx] = best
            result_tickers = {c.ticker for c, _ in result}
            remaining = [(c, s) for c, s in remaining if c.ticker not in result_tickers]
            tiers_in_result = {c.tier for c, _ in result}

    # 부족하면 overflow에서 채움 (섹터 제한 재확인)
    if len(result) < final_n:
        used = {c.ticker for c, _ in result}
        for c, s in overflow:
            if len(result) >= final_n:
                break
            if c.ticker not in used and sector_count.get(c.sector, 0) < max_per_sector:
                sector_count[c.sector] = sector_count.get(c.sector, 0) + 1
                result.append((c, s))

    return result[:final_n]


# ──────────────────────────────────────────────
# 분할매수 초안 계산
# ──────────────────────────────────────────────
def calc_split_buy(
    amount: int,
    candidates: list[Candidate],
    weights: list[float] | None = None,
) -> list[dict]:
    n = len(candidates)
    if n == 0:
        return []

    if weights is None:
        weights = [1 / n] * n

    total_w = sum(weights)
    weights = [w / total_w for w in weights]

    result = []
    for c, w in zip(candidates, weights):
        alloc = int(amount * w)
        result.append({
            "ticker": c.ticker,
            "name": c.name,
            "sector": c.sector,
            "tier": c.tier,
            "weight_pct": round(w * 100, 1),
            "amount_krw": alloc,
            "split": _split_schedule(alloc),
        })
    return result


def _split_schedule(alloc: int) -> list[dict]:
    """금액 규모에 따라 분할 횟수 자동 결정."""
    if alloc <= 0:
        return []

    if alloc < 100_000:          # 10만원 미만 → 1회
        return [{"차수": "1차", "금액": alloc, "조건": "일괄 진입"}]

    if alloc < 500_000:          # 50만원 미만 → 2회
        half = alloc // 2
        return [
            {"차수": "1차", "금액": half,        "조건": "현재 가격 진입"},
            {"차수": "2차", "금액": alloc - half, "조건": "-5% 추가 하락 또는 추세 확인 후"},
        ]

    # 50만원 이상 → 3회
    each = alloc // 3
    return [
        {"차수": "1차", "금액": each,             "조건": "현재 가격 진입"},
        {"차수": "2차", "금액": each,             "조건": "-5% 추가 하락 시"},
        {"차수": "3차", "금액": alloc - each * 2, "조건": "추세 확인 후"},
    ]
