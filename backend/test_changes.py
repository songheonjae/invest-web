"""
변경사항 단위 테스트 (서버/API 없이 실행 가능)
실행: cd backend && venv/Scripts/activate && python test_changes.py
"""
import sys
sys.path.insert(0, ".")

PASS = "[PASS]"
FAIL = "[FAIL]"


def section(title):
    print(f"\n{'='*50}")
    print(f"  {title}")
    print('='*50)


def check(label, condition, detail=""):
    status = PASS if condition else FAIL
    print(f"  {status} {label}" + (f"  ->  {detail}" if detail else ""))
    return condition


# ──────────────────────────────────────────────────
# 1. format_financials_for_prompt — [확정]/[추정] 꼬리표
# ──────────────────────────────────────────────────
section("1. 실적 꼬리표 ([확정]/[추정])")
from app.services.financials import format_financials_for_prompt, get_quarter_meta

mock_financials = {
    "unit": "억원",
    "annual": [
        {"period": "2023.12", "매출액": "1000", "영업이익": "100", "당기순이익": "80", "영업이익률": "10.0", "ROE": "8.0"},
        {"period": "2024.12", "매출액": "1200", "영업이익": "130", "당기순이익": "100", "영업이익률": "10.8", "ROE": "9.2"},
        {"period": "2025.12(E)", "매출액": "1400", "영업이익": "160", "당기순이익": "120", "영업이익률": "11.4", "ROE": "10.0"},
    ],
    "quarterly": [
        {"period": "2024.09", "매출액": "300", "영업이익": "32", "당기순이익": "25", "영업이익률": "10.7"},
        {"period": "2024.12", "매출액": "320", "영업이익": "35", "당기순이익": "27", "영업이익률": "10.9"},
        {"period": "2025.03(E)", "매출액": "340", "영업이익": "38", "당기순이익": "29", "영업이익률": "11.2"},
    ],
}

result = format_financials_for_prompt(mock_financials)
check("[확정] 태그 존재", "[확정]" in result, result[:80])
check("[추정] 태그 존재", "[추정]" in result)
check("범례 존재", "확정]=공시" in result)
check("단위 표시", "억원" in result)


# ──────────────────────────────────────────────────
# 2. get_quarter_meta — last_reported_quarter / next_quarter 추론
# ──────────────────────────────────────────────────
section("2. get_quarter_meta")

meta = get_quarter_meta(mock_financials)
check("last_reported_quarter = 2024.12", meta.get("last_reported_quarter") == "2024.12", str(meta))
check("next_quarter = 2025.03", meta.get("next_quarter") == "2025.03", str(meta))

# 분기 경계 테스트 (12월 → 다음 해 3월)
meta2 = get_quarter_meta({
    "quarterly": [
        {"period": "2024.06"},
        {"period": "2024.09"},
        {"period": "2024.12"},
    ]
})
check("12월 → 다음해 03월 계산", meta2.get("next_quarter") == "2025.03", str(meta2))

# 전부 추정치면 빈 dict
meta3 = get_quarter_meta({"quarterly": [{"period": "2025.03(E)"}]})
check("전부 추정치 → 빈 dict", meta3 == {}, str(meta3))


# ──────────────────────────────────────────────────
# 3. _detect_sector_type — 업종 감지
# ──────────────────────────────────────────────────
section("3. 업종 감지 (_detect_sector_type)")
from app.services.claude_analysis import _detect_sector_type, _sector_rules

check("은행 → financial", _detect_sector_type("은행", "") == "financial")
check("보험 → financial", _detect_sector_type("보험", "생명보험") == "financial")
check("바이오 → bio", _detect_sector_type("바이오", "") == "bio")
check("제약 → bio", _detect_sector_type("의약품", "제약") == "bio")
check("소프트웨어 → growth", _detect_sector_type("소프트웨어", "") == "growth")
check("게임 → growth", _detect_sector_type("게임", "") == "growth")
check("철강 → general", _detect_sector_type("철강", "") == "general")
check("Technology(해외) → general", _detect_sector_type("Technology", "Semiconductors") == "general")


# ──────────────────────────────────────────────────
# 4. _sector_rules — 업종별 규칙 내용
# ──────────────────────────────────────────────────
section("4. 업종 규칙 (_sector_rules)")

fin_rule = _sector_rules("financial")
bio_rule = _sector_rules("bio")
growth_rule = _sector_rules("growth")
general_rule = _sector_rules("general")

check("금융: 영업이익률 금지 규칙 포함", "영업이익률 규칙 적용 금지" in fin_rule)
check("금융: ROE 언급", "ROE" in fin_rule)
check("바이오: 적자 severe 금지 규칙 포함", "severe" in bio_rule)
check("바이오: 파이프라인 언급", "파이프라인" in bio_rule)
check("성장주: 고PER 금지 규칙 포함", "고PER" in growth_rule)
check("일반: 빈 문자열", general_rule == "")


# ──────────────────────────────────────────────────
# 5. validate_financials — 기존 검증 (회귀 테스트)
# ──────────────────────────────────────────────────
section("5. validate_financials 회귀 테스트")
from app.services.financials import validate_financials

# 정상 케이스
ok_fin = {
    "annual": [{"period": "2024.12", "매출액": "1000", "영업이익": "100", "영업이익률": "10.0"}],
    "quarterly": [],
}
v = validate_financials(ok_fin)
check("정상 데이터 → has_issues False", not v["has_issues"])

# 분기 > 연간 (severe)
bad_fin = {
    "annual": [{"period": "2024.12", "매출액": "500", "영업이익": "50", "영업이익률": "10.0"}],
    "quarterly": [{"period": "2024.09", "매출액": "800", "영업이익": "80", "영업이익률": "10.0"}],
}
v2 = validate_financials(bad_fin)
check("분기>연간 → severe 감지", v2["max_severity"] == "severe", str(v2["issues"]))


# ──────────────────────────────────────────────────
# 결과 요약
# ──────────────────────────────────────────────────
print(f"\n{'='*50}")
print("  완료. 위에서 ❌ 없으면 단위 테스트 통과.")
print('='*50)
