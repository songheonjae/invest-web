"""
추천기 테스트셋 — 스코어링 엔진 + 파이프라인 검증
Claude API 호출 없이 사전 점수 + 다양성 로직만 검증
"""
import asyncio
from app.services.recommender.scorer import UserInput, build_candidate_shortlist, apply_diversity

SCENARIOS = [
    {
        "label": "소액/공격/단기/성장주",
        "input": UserInput(amount=50_000, risk="aggressive", period="short", market="KR",
                           prefer_styles=["성장주"]),
    },
    {
        "label": "300만/보수/장기/배당",
        "input": UserInput(amount=3_000_000, risk="conservative", period="long", market="KR",
                           prefer_styles=["배당"], exclude_tags=["변동성높음"], exclude_sectors=["바이오", "2차전지"]),
    },
    {
        "label": "1000만/중립/중기/바이오제외",
        "input": UserInput(amount=10_000_000, risk="neutral", period="medium", market="KR",
                           exclude_sectors=["바이오"]),
    },
    {
        "label": "500만/공격/중기/방산관심",
        "input": UserInput(amount=5_000_000, risk="aggressive", period="medium", market="KR",
                           prefer_sectors=["방산"]),
    },
    {
        "label": "100만/보수/장기/배당+저PER",
        "input": UserInput(amount=1_000_000, risk="conservative", period="long", market="KR",
                           prefer_styles=["배당", "저PER"], exclude_tags=["변동성높음"]),
    },
    {
        "label": "200만/중립/중기/해외성장주",
        "input": UserInput(amount=2_000_000, risk="neutral", period="medium", market="US",
                           prefer_styles=["성장주"]),
    },
    {
        "label": "500만/공격/단기/해외반도체",
        "input": UserInput(amount=5_000_000, risk="aggressive", period="short", market="US",
                           prefer_sectors=["반도체"]),
    },
    {
        "label": "ALL시장/중립/중기/배당",
        "input": UserInput(amount=5_000_000, risk="neutral", period="medium", market="ALL",
                           prefer_styles=["배당"]),
    },
    {
        "label": "보유종목 있을때 중복 회피",
        "input": UserInput(amount=3_000_000, risk="neutral", period="medium", market="KR",
                           holdings=["005930", "000660"],   # 삼성전자, SK하이닉스
                           exclude_sectors=["바이오"]),
    },
    {
        "label": "전부 제외 극단 케이스",
        "input": UserInput(amount=1_000_000, risk="conservative", period="long", market="KR",
                           exclude_sectors=["바이오", "2차전지", "게임"],
                           exclude_tags=["변동성높음", "고PER", "경기민감", "블록체인"]),
    },
    {
        "label": "수동 추천 개수 2개",
        "input": UserInput(amount=5_000_000, risk="neutral", period="medium", market="KR",
                           recommend_count=2, prefer_sectors=["반도체", "방산"]),
    },
]


def run_test(scenario: dict):
    user = scenario["input"]
    shortlist = build_candidate_shortlist(user, top_n=20)
    from app.services.recommender.pipeline import _resolve_recommend_count
    n = _resolve_recommend_count(user.amount, user.recommend_count)
    final = apply_diversity(shortlist, final_n=n)

    tiers = [c.tier for c, _ in final]
    sectors = [c.sector for c, _ in final]
    names = [c.name for c, _ in final]
    scores = [round(s, 1) for _, s in final]

    sector_ok = len(sectors) == len(set(sectors)) or len(sectors) <= 1
    tier_variety = len(set(tiers))

    status = "✅" if len(final) > 0 else "❌ 후보 없음"

    print(f"\n[{scenario['label']}]")
    print(f"  후보수: {len(shortlist)} → 최종: {len(final)}개  {status}")
    print(f"  선정: {list(zip(names, scores))}")
    print(f"  Tier구성: {tiers} (다양성: {tier_variety}종)")
    print(f"  섹터중복: {'없음' if sector_ok else '있음'} {sectors}")


if __name__ == "__main__":
    print("=" * 60)
    print("추천기 스코어링 테스트 (API 호출 없음)")
    print("=" * 60)
    for s in SCENARIOS:
        run_test(s)
