import asyncio
import httpx
from bs4 import BeautifulSoup
from app.services.opendart import get_dart_confirmed_financials


async def get_sector_per_naver(sector_name: str) -> float | None:
    """네이버 업종별 평균 PER 스크래핑. 실패 시 None 반환."""
    if not sector_name:
        return None
    try:
        url = "https://finance.naver.com/sise/sectorList.nhn"
        headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
        async with httpx.AsyncClient(timeout=5) as client:
            res = await client.get(url, headers=headers)
        html = res.content.decode(res.encoding or "euc-kr", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")

        for table in soup.select("table"):
            header_row = table.select_one("tr")
            if not header_row:
                continue
            headers_text = [th.get_text(strip=True) for th in header_row.select("th")]
            if "PER" not in headers_text:
                continue
            per_idx = headers_text.index("PER")

            for row in table.select("tr")[1:]:
                cells = row.select("td")
                if len(cells) <= per_idx:
                    continue
                row_name = cells[0].get_text(strip=True)
                # 업종명 부분 일치
                if any(kw in row_name for kw in sector_name.split() if len(kw) >= 2):
                    try:
                        return float(cells[per_idx].get_text(strip=True).replace(",", ""))
                    except (ValueError, TypeError):
                        pass
            break
        return None
    except Exception:
        return None


async def get_company_overview(ticker: str) -> str:
    url = f"https://finance.naver.com/item/coinfo.naver?code={ticker}&target=company_info"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            res = await client.get(url, headers=headers)
        html = res.content.decode(res.encoding or "utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        noise = {"공모주와 해외 종목은 모바일 페이지로 이동합니다.", "현재 자동완성 기능을 사용하고 계십니다.", "추가할 그룹이 없습니다.그룹을 추가해주세요."}
        lines = []
        for p in soup.find_all("p"):
            text = p.get_text(strip=True)
            if len(text) > 20 and text not in noise:
                lines.append(text)
        return " ".join(lines)[:500] if lines else ""
    except Exception:
        return ""


FIELD_NAMES = ["매출액", "영업이익", "당기순이익", "영업이익률", "순이익률", "ROE", "부채비율"]


async def _get_financials_naver(ticker: str) -> dict:
    url = f"https://finance.naver.com/item/main.naver?code={ticker}"
    headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com"}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            res = await client.get(url, headers=headers)
        html = res.content.decode(res.encoding or "utf-8", errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        section = soup.select_one("div.section.cop_analysis")
        if not section:
            return {}

        rows = section.select("tr")

        # 헤더 행(연도) 파싱
        # Step1: colspan 기반으로 연간/분기 컬럼 수 확정
        annual_col_count, quarter_col_count = 4, 4
        for row in rows[:2]:
            for th in row.select("th[colspan]"):
                text = th.get_text(strip=True)
                try:
                    span = int(th.get("colspan", 1))
                except (ValueError, TypeError):
                    span = 1
                if "연간" in text:
                    annual_col_count = span
                elif "분기" in text:
                    quarter_col_count = span

        # Step2: 날짜 셀 추출 — 점 없는 연간(2024/2025E)도 포함
        annual_years, quarter_years = [], []
        for row in rows[:3]:
            cells = [td.get_text(strip=True) for td in row.select("th, td")]
            cells = [c for c in cells if c]
            # 연간: "2023", "2025(E)" 형태 / 분기: "2024.03", "2024.12(E)" 형태
            date_cells = [
                c for c in cells
                if len(c) >= 4 and c[:4].isdigit()
            ]
            if len(date_cells) >= annual_col_count:
                annual_years = date_cells[:annual_col_count]
                quarter_years = date_cells[annual_col_count:annual_col_count + quarter_col_count]
                break

        # 라벨 기반 데이터 파싱 (th 텍스트로 필드 매칭, 부분일치)
        _sorted_fields = sorted(FIELD_NAMES, key=len, reverse=True)

        def _match_field(text: str):
            text = text.strip()
            if text in FIELD_NAMES:
                return text
            for name in _sorted_fields:
                if name in text:
                    return name
            return None

        label_rows: dict[str, list] = {}
        for row in rows:
            label_el = row.select_one("th")
            if label_el is None:
                tds = row.select("td")
                label_el = tds[0] if tds else None
            if label_el is None:
                continue
            label = _match_field(label_el.get_text(strip=True))
            if label:
                cells = [td.get_text(strip=True) for td in row.select("td")]
                cells = [c for c in cells if c]
                label_rows[label] = cells

        def build_records(years, col_start):
            records = []
            for idx, year in enumerate(years):
                record = {"period": year}
                col_idx = idx + col_start
                for field_name in FIELD_NAMES:
                    row_cells = label_rows.get(field_name, [])
                    val = row_cells[col_idx] if col_idx < len(row_cells) else None
                    record[field_name] = val if val != "" else None
                records.append(record)
            return records

        annual = build_records(annual_years, 0) if annual_years else []
        quarterly = build_records(quarter_years, len(annual_years)) if quarter_years else []

        # 파싱 정합성 검증: 연간 확정값이 분기 확정값보다 작으면 col_start 오프셋 오류
        # annual과 quarterly 둘 다 재보정 시도
        def _confirmed_rev(records):
            for r in records:
                if "(E)" not in r.get("period", "") and r.get("매출액"):
                    try:
                        return float(str(r["매출액"]).replace(",", ""))
                    except (ValueError, TypeError):
                        pass
            return None

        if annual and quarterly:
            a_rev = _confirmed_rev(annual)
            q_rev = _confirmed_rev(quarterly)
            if a_rev and q_rev and a_rev < q_rev:
                # quarterly col_start 재보정
                quarterly_retry = build_records(quarter_years, len(annual_years) + 1)
                q_rev_retry = _confirmed_rev(quarterly_retry)
                if q_rev_retry and q_rev_retry < a_rev:
                    quarterly = quarterly_retry

        return {"annual": annual, "quarterly": quarterly[-4:], "unit": "억원"}

    except Exception:
        return {}


async def get_financials(ticker: str) -> dict:
    """
    확정 실적: OpenDART (연간 3개년 + 최근 4분기 standalone)
    추정치(E): 네이버 파이낸스 fallback
    OpenDART 실패 시 전체 네이버 fallback.
    """
    dart_task  = get_dart_confirmed_financials(ticker)
    naver_task = _get_financials_naver(ticker)
    dart_data, naver_data = await asyncio.gather(dart_task, naver_task, return_exceptions=True)

    if isinstance(dart_data, Exception):
        dart_data = None
    if isinstance(naver_data, Exception):
        naver_data = {}

    if not dart_data:
        return naver_data or {}

    naver_annual    = naver_data.get("annual", []) if isinstance(naver_data, dict) else []
    naver_quarterly = naver_data.get("quarterly", []) if isinstance(naver_data, dict) else []

    dart_a_periods = {r["period"] for r in dart_data.get("annual", [])}
    dart_q_periods = {r["period"] for r in dart_data.get("quarterly", [])}

    # DART가 커버하지 못한 기간은 Naver로 보완 (확정+E 모두)
    naver_a_fill = [r for r in naver_annual    if r.get("period") not in dart_a_periods]
    naver_q_fill = [r for r in naver_quarterly if r.get("period") not in dart_q_periods]

    def _period_key(r: dict) -> tuple:
        """정렬용 키: 숫자 오름차순, E값은 같은 연도 내 나중에"""
        p = r.get("period", "")
        is_e = 1 if "(E)" in p else 0
        numeric = p.replace("(E)", "").replace(".", "").strip()
        try:
            return (int(numeric), is_e)
        except ValueError:
            return (0, is_e)

    merged_annual    = sorted(dart_data["annual"] + naver_a_fill,    key=_period_key)
    merged_quarterly = sorted(dart_data["quarterly"] + naver_q_fill, key=_period_key)

    return {
        "annual":    merged_annual,
        "quarterly": merged_quarterly[-4:],
        "unit":      "억원",
    }


def _classify_estimate_quality(
    financials: dict,
    is_financial: bool = False,
    is_bio: bool = False,
    is_semiconductor: bool = False,
) -> dict:
    """
    E(추정치) 기간별 품질 3단계 분류.
    반환: {period: {"level": "normal"|"suspect"|"error_candidate", "reasons": [...], "tag": str}}
    - error_candidate : 프롬프트·표 전면 제외
    - suspect         : 표에 [추정-주의] 태그, 시나리오/조언만 제한 허용
    - normal          : 표에 [추정] 태그, 시나리오/조언/Forward PER 허용
    """
    import logging
    _log = logging.getLogger(__name__)

    def _f(val):
        try:
            return float(str(val).replace(",", "").replace("%", "").strip()) if val is not None else None
        except (ValueError, TypeError):
            return None

    annual    = financials.get("annual", [])
    quarterly = financials.get("quarterly", [])
    confirmed_a = [r for r in annual    if "(E)" not in r.get("period", "")]
    confirmed_q = [r for r in quarterly if "(E)" not in r.get("period", "")]

    last_q_rev = _f(confirmed_q[-1].get("매출액"))   if confirmed_q else None
    last_a_rev = _f(confirmed_a[-1].get("매출액"))   if confirmed_a else None
    last_a_op  = _f(confirmed_a[-1].get("영업이익")) if confirmed_a else None

    # 반도체 회복 흐름 선행 판단 (E급등 suspect 완화 조건)
    is_recovering = False
    if is_semiconductor and len(confirmed_a) >= 2:
        p_op = _f(confirmed_a[-2].get("영업이익"))
        c_op = _f(confirmed_a[-1].get("영업이익"))
        if p_op is not None and c_op is not None and c_op > p_op:
            is_recovering = True

    quality: dict = {}

    all_e = (
        [(r, "annual")    for r in annual    if "(E)" in r.get("period", "")]
        + [(r, "quarterly") for r in quarterly if "(E)" in r.get("period", "")]
    )

    for r, rtype in all_e:
        period = r.get("period", "")
        e_rev  = _f(r.get("매출액"))
        e_op   = _f(r.get("영업이익"))
        reasons: list[str] = []
        level  = "normal"

        def _up(to: str, msg: str):
            nonlocal level
            reasons.append(msg)
            if to == "error_candidate":
                level = "error_candidate"
            elif to == "suspect" and level == "normal":
                level = "suspect"

        # ── error_candidate ──────────────────────────────
        if e_rev is not None and e_rev < 0:
            _up("error_candidate", f"음수 매출 {e_rev:,.0f}억원")

        if e_rev and e_op and e_rev > 0 and e_op > 0 and e_op > e_rev:
            _up("error_candidate", f"영업이익({e_op:,.0f})이 매출({e_rev:,.0f})을 초과 — 상호모순")

        # ── 업종 무관: 연간 E ≈ 최근 분기 (분기 혼입 확실 신호) ────────
        # 반도체 회복 사이클과 상관없이 annual == quarter는 파싱 아티팩트
        if (rtype == "annual"
                and e_rev is not None and e_rev > 0
                and last_q_rev and last_q_rev > 0):
            _qr = e_rev / last_q_rev
            if 0.8 <= _qr <= 1.3:
                _up("error_candidate",
                    f"연간 E({e_rev:,.0f}) ≈ 최근 확정 분기({last_q_rev:,.0f}) "
                    f"(비율 {_qr:.2f}) — 분기 수치 혼입 확실 (업종 무관 판정)")

        # 분기 혼입: 연간 E < 최근 확정 분기 * 1.5 (금융·바이오 제외)
        if (rtype == "annual" and not is_financial and not is_bio
                and e_rev is not None and e_rev > 0
                and last_q_rev and last_q_rev > 0
                and e_rev < last_q_rev * 1.5):
            _up("error_candidate",
                f"연간 E({e_rev:,.0f}) < 최근 확정 분기({last_q_rev:,.0f}) × 1.5 — 분기 수치 혼입 의심")

        # 금융: 직전 연간 5% 미만
        if (is_financial and rtype == "annual"
                and e_rev is not None and e_rev > 0
                and last_a_rev and last_a_rev > 0
                and e_rev < last_a_rev * 0.05):
            _up("error_candidate",
                f"연간 E({e_rev:,.0f}) < 직전 확정 연간({last_a_rev:,.0f}) × 5% — 파싱 오류 의심")

        # ── suspect (error_candidate 아닌 경우만) ────────
        if level != "error_candidate" and rtype == "annual":
            # 일반: 30% 미만 급감 → error_candidate
            if (not is_financial and not is_bio and not is_semiconductor
                    and e_rev is not None and e_rev > 0
                    and last_a_rev and last_a_rev > 0
                    and e_rev < last_a_rev * 0.3):
                _up("error_candidate",
                    f"연간 E({e_rev:,.0f}) < 직전 확정 연간({last_a_rev:,.0f}) × 30% — 분기 수치 혼입 가능성")

            # 일반: 30~50% 구간 → suspect
            elif (not is_financial and not is_bio and not is_semiconductor
                    and e_rev is not None and e_rev > 0
                    and last_a_rev and last_a_rev > 0
                    and e_rev < last_a_rev * 0.5):
                _up("suspect",
                    f"연간 E({e_rev:,.0f}) < 직전 확정 연간({last_a_rev:,.0f}) × 50% — 급감 이상치")

            # 금융: 30% 미만 → suspect (5% 미만은 이미 error_candidate)
            if (is_financial and e_rev is not None and e_rev > 0
                    and last_a_rev and last_a_rev > 0
                    and e_rev < last_a_rev * 0.3):
                _up("suspect",
                    f"연간 E({e_rev:,.0f}) < 직전 확정 연간({last_a_rev:,.0f}) × 30%")

            # 매출 급등
            if e_rev is not None and last_a_rev and abs(last_a_rev) > 0:
                chg = (e_rev - last_a_rev) / abs(last_a_rev) * 100
                if is_semiconductor:
                    if chg > 400 and not is_recovering:
                        _up("suspect", f"매출 E {chg:.0f}% 급등 — 반도체 사이클 회복 흐름 미확인")
                    elif chg > 400:
                        reasons.append(f"매출 E {chg:.0f}% 급등 — 사이클 회복 반영 가능성(확정 개선 흐름 확인됨)")
                elif not is_bio and not is_financial:
                    if chg > 400:
                        _up("suspect", f"매출 E {chg:.0f}% 급등 — 파싱 오류 또는 이상치 가능성")
                    elif chg > 200:
                        _up("suspect", f"매출 E {chg:.0f}% 급등 — 증권사 편차 클 수 있음")

            # 영업이익 급등 (일반 업종)
            if (not is_bio and not is_financial and not is_semiconductor
                    and e_op is not None and last_a_op and last_a_op != 0):
                op_chg = (e_op - last_a_op) / abs(last_a_op) * 100
                if op_chg > 400:
                    _up("suspect", f"영업이익 E {op_chg:.0f}% 급등")

        tag_map = {
            "normal":          "[추정]",
            "suspect":         "[추정-주의]",
            "error_candidate": "[추정-오류후보]",
        }
        quality[period] = {"level": level, "reasons": reasons, "tag": tag_map[level]}
        _log.info(
            "[estimate_quality] %s → %s%s",
            period, level,
            f" | {'; '.join(reasons)}" if reasons else "",
        )

    return quality


def validate_financials(financials: dict, sector: str = "", industry: str = "") -> dict:
    """
    issues 리스트 반환: [{"severity": "severe"|"medium"|"light", "message": str}]
    severity 기준:
      severe  — 파싱 오류 확실 (단위 혼동, 분기/연간 혼입)
      medium  — 산식 불일치 10%p 초과 (일반 제조업 전용)
      light   — E값 급등·소폭 불일치 (참고 경고)
    금융주: 매출액/영업이익률 기반 규칙 비활성화, 순이익·ROE·PBR 중심 검증
    """
    import logging
    _log = logging.getLogger(__name__)

    issues = []
    estimate_periods = []
    disabled_rules = []

    _combined = (sector + " " + industry).lower()
    is_financial    = any(k in _combined for k in ["bank", "financ", "insurance", "은행", "보험", "증권", "금융", "financial"])
    is_bio          = any(k in _combined for k in ["bio", "pharma", "drug", "바이오", "제약", "의약", "의료기기", "헬스케어", "biotechnology", "pharmaceutical", "healthcare"])
    is_semiconductor= any(k in _combined for k in ["반도체", "디스플레이", "semiconductor"])

    if is_financial:
        _log.info(
            "[validate_financials] 금융주 판정: sector=%s industry=%s → "
            "비활성화 규칙: 영업이익률검증, 연율화괴리, 매출연율화비교, 영업이익률구간LIGHT",
            sector, industry
        )
    if is_bio:
        _log.info(
            "[validate_financials] 바이오/제약 판정: sector=%s industry=%s → "
            "비활성화 규칙: E값급등경고, 연율화괴리, 영업이익률검증, 매출급감severe, 매출연율화비교",
            sector, industry
        )
    if is_semiconductor:
        _log.info(
            "[validate_financials] 반도체/디스플레이 판정: sector=%s industry=%s → "
            "완화 규칙: E값급등(회복흐름확인), 연율화괴리임계3.0배, 영업이익률급등검증제외 | "
            "비활성화: 매출급감severe(30%%), 매출연율화비교severe",
            sector, industry
        )

    annual = financials.get("annual", [])
    quarterly = financials.get("quarterly", [])

    for r in annual + quarterly:
        if "(E)" in r.get("period", ""):
            estimate_periods.append(r["period"])

    def to_float(val):
        if val is None:
            return None
        try:
            return float(str(val).replace(",", "").replace("%", "").strip())
        except (ValueError, TypeError):
            return None

    def add(severity, message):
        issues.append({"severity": severity, "message": message})

    # ── SEVERE: 분기 매출 > 연간 매출 (단위 혼동) ────────────────
    for q in quarterly:
        if "(E)" in q.get("period", ""):
            continue
        q_rev = to_float(q.get("매출액"))
        q_year = q.get("period", "")[:4]
        for a in annual:
            if a.get("period", "").startswith(q_year) and "(E)" not in a.get("period", ""):
                a_rev = to_float(a.get("매출액"))
                if q_rev and a_rev and q_rev > a_rev:
                    add("severe",
                        f"[{q['period']}] 분기 매출({q_rev:,.0f})이 연간({a['period']}, {a_rev:,.0f})보다 큼 — 단위 혼동 의심")
                break

    # ── SEVERE: 영업이익률 ±200% 초과 (금융·바이오 제외) ──────────
    if not is_financial and not is_bio:
        for r in annual + quarterly:
            period = r.get("period", "?")
            margin = to_float(r.get("영업이익률"))
            if margin is not None and (margin > 200 or margin < -200):
                add("severe", f"[{period}] 영업이익률 극단값 {margin}% — 파싱 오류 가능성")

    # ── MEDIUM: 영업이익률 역산 불일치 10%p 초과 (금융·바이오 제외) ─
    if not is_financial and not is_bio:
        for r in annual + quarterly:
            period = r.get("period", "?")
            rev = to_float(r.get("매출액"))
            op = to_float(r.get("영업이익"))
            margin = to_float(r.get("영업이익률"))
            if rev and op is not None and margin is not None and abs(rev) > 0:
                calc = (op / rev) * 100
                diff = abs(calc - margin)
                if diff > 10:
                    add("medium", f"[{period}] 영업이익률 역산 불일치 {diff:.1f}%p (표시 {margin}% / 역산 {calc:.1f}%)")
                elif diff > 5:
                    add("light", f"[{period}] 영업이익률 소폭 불일치 {diff:.1f}%p")

    # ── MEDIUM/LIGHT: E값 급등 ────────────────────────────────────
    # 반도체: 사이클 회복으로 400%+ 급등이 실제로 일어남 → medium→light 완화
    for i in range(1, len(annual)):
        curr, prev = annual[i], annual[i - 1]
        if "(E)" not in curr.get("period", ""):
            continue
        period = curr["period"]
        for field in ["매출액", "영업이익"]:
            cv, pv = to_float(curr.get(field)), to_float(prev.get(field))
            if cv is not None and pv and abs(pv) > 0:
                change = (cv - pv) / abs(pv) * 100
                if is_semiconductor:
                    if change > 400:
                        # 최근 확정 실적이 저점에서 회복 중인지 확인
                        confirmed_a_check = [r for r in annual if "(E)" not in r.get("period", "")]
                        is_recovering = False
                        if len(confirmed_a_check) >= 2:
                            prev_op_c = to_float(confirmed_a_check[-2].get("영업이익"))
                            curr_op_c = to_float(confirmed_a_check[-1].get("영업이익"))
                            if prev_op_c is not None and curr_op_c is not None and curr_op_c > prev_op_c:
                                is_recovering = True
                        if is_recovering:
                            add("light",
                                f"[{period}] {field} 추정치 전년 대비 {change:.0f}% 급등 — "
                                f"반도체 사이클 회복 반영 가능성 (확정 실적 개선 흐름 확인됨, 파싱 오류 가능성 낮음)")
                        else:
                            add("medium",
                                f"[{period}] {field} 추정치 전년 대비 {change:.0f}% 급등 — "
                                f"반도체 사이클 회복 가능성 있으나 확정 실적 개선 미확인, 추가 확인 필요")
                    elif change > 200:
                        add("light",
                            f"[{period}] {field} 추정치 전년 대비 {change:.0f}% 급등 — "
                            f"반도체 업황 회복 반영 가능성, 증권사별 편차 클 수 있음")
                elif is_bio:
                    pass  # 바이오는 적자→흑자 전환 시 무한대 급등 가능, E값 급등 경고 생략
                else:
                    if change > 400:
                        add("medium", f"[{period}] {field} 추정치 전년 대비 {change:.0f}% 급등 — 파싱 오류 또는 컨센서스 이상치 가능성")
                    elif change > 200:
                        add("light", f"[{period}] {field} 추정치 전년 대비 {change:.0f}% 급등 — 증권사별 편차 클 수 있음")

    # ── MEDIUM: 연간 E 추정치 vs 분기 연율화 괴리 (반도체·바이오·금융 완화) ─
    # 금융주: 이자율 환경 변동으로 분기→연간 연율화가 비선형, 비교 무의미
    confirmed_q = [r for r in quarterly if "(E)" not in r.get("period", "")]
    if is_financial:
        disabled_rules.append("연간E vs 분기연율화 괴리(금융주 이자율 환경 비선형)")
    if confirmed_q and not is_bio and not is_financial:
        last_q_op = to_float(confirmed_q[-1].get("영업이익"))
        if last_q_op and last_q_op > 0:
            implied_annual = last_q_op * 4
            # 반도체는 사이클 특성상 분기 연율화 대비 급등 빈번 → 임계값 완화
            threshold = 3.0 if is_semiconductor else 1.8
            for r in annual:
                if "(E)" not in r.get("period", ""):
                    continue
                a_op = to_float(r.get("영업이익"))
                if a_op and a_op > implied_annual * threshold:
                    add("medium",
                        f"[{r['period']}] 연간 영업이익 추정치({a_op:,.0f})가 "
                        f"분기 연율화({implied_annual:,.0f})의 {a_op/implied_annual:.1f}배 — "
                        f"파싱 오류 또는 이상치 가능성")

    # ── MEDIUM: E 영업이익률 급등 (금융·바이오·반도체 제외) ──────────
    confirmed_a = [r for r in annual if "(E)" not in r.get("period", "")]
    if confirmed_a and not is_financial and not is_bio and not is_semiconductor:
        last_confirmed_margin = to_float(confirmed_a[-1].get("영업이익률"))
        if last_confirmed_margin is not None:
            for r in annual:
                if "(E)" not in r.get("period", ""):
                    continue
                e_margin = to_float(r.get("영업이익률"))
                if e_margin is not None and (e_margin - last_confirmed_margin) > 25:
                    add("medium",
                        f"[{r['period']}] 영업이익률 추정치 {e_margin}% — "
                        f"전년 확정({last_confirmed_margin}%) 대비 +{e_margin - last_confirmed_margin:.1f}%p 급등")

    # ── SEVERE: 연간 E ≈ 최근 확정 분기 (업종 무관 — 분기 수치 혼입 확실) ──────
    # KEC(092220) 사례: 연간 E 538억 ≈ 분기 537억, 실제 연간 2,296억
    # is_semiconductor 등 업종 예외 없이 항상 적용
    if confirmed_q and confirmed_a:
        _last_q_rev_u = to_float(confirmed_q[-1].get("매출액"))
        if _last_q_rev_u and _last_q_rev_u > 0:
            for r in annual:
                if "(E)" not in r.get("period", ""):
                    continue
                _e_rev_u = to_float(r.get("매출액"))
                if _e_rev_u and _e_rev_u > 0:
                    _qr_u = _e_rev_u / _last_q_rev_u
                    if 0.8 <= _qr_u <= 1.3:
                        add("severe",
                            f"[{r['period']}] 연간 추정 매출({_e_rev_u:,.0f})이 최근 확정 분기({_last_q_rev_u:,.0f})와 "
                            f"거의 동일(비율 {_qr_u:.2f}) — 분기 수치 혼입 확실 (업종 무관 판정)")

    # ── SEVERE: 연간 E 매출이 TTM(직전 4분기 합산)의 45% 미만 (금융·바이오·반도체 제외) ──
    if confirmed_q and confirmed_a and not is_financial and not is_bio and not is_semiconductor:
        _ttm_revs = [to_float(r.get("매출액")) for r in confirmed_q[-4:]]
        _ttm_rev = sum(v for v in _ttm_revs if v is not None) if any(v is not None for v in _ttm_revs) else None
        if _ttm_rev and _ttm_rev > 0:
            for r in annual:
                if "(E)" not in r.get("period", ""):
                    continue
                _e_rev_t = to_float(r.get("매출액"))
                if _e_rev_t and _e_rev_t > 0 and _e_rev_t < _ttm_rev * 0.45:
                    add("severe",
                        f"[{r['period']}] 연간 추정 매출({_e_rev_t:,.0f})이 TTM 합산({_ttm_rev:,.0f})의 45% 미만 "
                        f"— 분기 수치 혼입 또는 심각한 파싱 오류 의심")

    # ── SEVERE: 연간 E 매출이 최근 확정 분기 매출보다 작음 (분기/연간 혼동) ──
    # 금융주: 이자수익 구조 상이 / 바이오: 소규모 매출이 분기와 유사할 수 있음
    if is_financial:
        disabled_rules.append("연간E매출 < 분기매출*1.5 severe(금융주 매출 구조 상이)")
    if is_bio:
        disabled_rules.append("연간E매출 < 분기매출*1.5 severe(바이오 소규모 매출 정상 패턴)")
    if is_semiconductor:
        disabled_rules.append("연간E매출 < 분기매출*1.5 severe(반도체 다운사이클→업턴 E값 급등 정상)")

    if confirmed_q and confirmed_a and not is_financial and not is_bio and not is_semiconductor:
        last_q_rev = to_float(confirmed_q[-1].get("매출액"))
        if last_q_rev and last_q_rev > 0:
            for r in annual:
                if "(E)" not in r.get("period", ""):
                    continue
                e_rev = to_float(r.get("매출액"))
                if e_rev and e_rev < last_q_rev * 1.5:
                    add("severe",
                        f"[{r['period']}] 연간 추정 매출({e_rev:,.0f})이 최근 분기 매출({last_q_rev:,.0f})보다 "
                        f"지나치게 작음 — 분기/연간 혼동 의심")

    # ── SEVERE: 연간 E값이 직전 연간 대비 30% 이하 (급감 = 분기 혼입 의심) ──
    # 바이오: 기술이전 후 매출 급감 정상 / 금융: 영업수익 개념 변동성으로 제외
    if is_bio:
        disabled_rules.append("연간E매출 < 직전연간*30% severe(바이오 기술이전 후 매출 급감 정상)")
    if is_financial:
        disabled_rules.append("연간E매출 < 직전연간*30% severe(금융 영업수익 개념 변동성 큼)")
    if is_semiconductor:
        disabled_rules.append("연간E매출 < 직전연간*30% severe(반도체 다운사이클 매출 급감 사이클 특성)")
    if confirmed_a and not is_bio and not is_financial and not is_semiconductor:
        last_a_rev = to_float(confirmed_a[-1].get("매출액"))
        if last_a_rev and last_a_rev > 0:
            for r in annual:
                if "(E)" not in r.get("period", ""):
                    continue
                e_rev = to_float(r.get("매출액"))
                if e_rev and e_rev < last_a_rev * 0.3:
                    add("severe",
                        f"[{r['period']}] 연간 추정 매출({e_rev:,.0f})이 직전 연간({last_a_rev:,.0f})의 30% 이하 — "
                        f"분기 수치 혼입 또는 파싱 오류 의심")

    # ── LIGHT: 영업이익률 90~200% 구간 (금융·바이오 제외) ───────────
    if is_financial:
        disabled_rules.append("영업이익률 90~200% LIGHT(금융주 이익률 구조 상이)")
    if not is_financial and not is_bio:
        for r in annual + quarterly:
            period = r.get("period", "?")
            margin = to_float(r.get("영업이익률"))
            if margin is not None and (90 < margin <= 200 or -200 <= margin < -100):
                add("light", f"[{period}] 영업이익률 이상 수준: {margin}%")

    # ── MEDIUM: 음수 E 매출 (업종 무관, 항상 파싱 오류 의심) ────────
    # 음수 매출은 어떤 업종도 정상이 아님 → medium (severe 아님: 단독으로 분석 불가 판정 금지)
    for r in annual:
        if "(E)" not in r.get("period", ""):
            continue
        e_rev = to_float(r.get("매출액"))
        if e_rev is not None and e_rev < 0:
            add("medium",
                f"[{r['period']}] 추정 매출액이 음수({e_rev:,.0f}) — "
                f"파싱 오류 또는 헤징손실 혼입 의심. 해당 기간 매출 수치 사용 금지")

    # ── LIGHT: 금융주 핵심 지표 누락 경고 ────────────────────────
    if is_financial:
        all_records = annual + quarterly
        has_net_income = any(to_float(r.get("당기순이익")) is not None for r in all_records)
        has_roe = any(to_float(r.get("ROE")) is not None for r in all_records)
        if not has_net_income:
            add("light", "[금융주] 당기순이익 데이터 없음 — ROE 산정 불가, 핵심 지표 누락")
        if not has_roe:
            add("light", "[금융주] ROE 데이터 없음 — 수익성 판단 제한적")

    # ── MEDIUM/LIGHT: 바이오 적자 확대 속도 경고 ─────────────────
    # 적자 자체는 정상이나, 적자 확대 속도와 매출 정체 동시는 캐시번 위험 신호
    if is_bio and len(confirmed_a) >= 2:
        ops = [to_float(r.get("영업이익")) for r in confirmed_a[-2:]]
        revs = [to_float(r.get("매출액")) for r in confirmed_a[-2:]]
        prev_op, curr_op = ops[0], ops[1]
        prev_rev, curr_rev = revs[0], revs[1]

        if prev_op is not None and curr_op is not None and prev_op < 0 and curr_op < 0:
            loss_increase = (curr_op - prev_op) / abs(prev_op) * 100  # 음수: 손실 확대
            if loss_increase < -50:
                # 매출도 감소 중이면 위험도 상승
                if prev_rev and curr_rev and curr_rev < prev_rev:
                    add("medium",
                        f"[{confirmed_a[-1]['period']}] [바이오] 영업손실 전년 대비 "
                        f"{abs(loss_increase):.0f}% 확대 + 매출 감소 동시 — 현금 소진 가속 가능성")
                else:
                    add("light",
                        f"[{confirmed_a[-1]['period']}] [바이오] 영업손실 전년 대비 "
                        f"{abs(loss_increase):.0f}% 확대 — 임상비용 증가 또는 사업 확장 확인 필요")

    # ── LIGHT: 바이오 분기 데이터 기반 추가 경고 ─────────────────
    if is_bio and len(confirmed_q) >= 2:
        q_ops = [to_float(r.get("영업이익")) for r in confirmed_q[-2:]]
        q_revs = [to_float(r.get("매출액")) for r in confirmed_q[-2:]]
        prev_qop, curr_qop = q_ops[0], q_ops[1]
        prev_qrev, curr_qrev = q_revs[0], q_revs[1]

        if (prev_qop is not None and curr_qop is not None
                and prev_qop < 0 and curr_qop < 0
                and abs(curr_qop) > abs(prev_qop) * 2):
            add("light",
                f"[{confirmed_q[-1]['period']}] [바이오] 분기 영업손실 직전 분기 대비 2배 이상 확대 — "
                f"연구개발비 급증 또는 비용 구조 변화 확인 필요")

    # ── LIGHT: 반도체 업황 보완 필요 경고 ─────────────────────────
    # 재고/수요처/CAPEX는 DART·네이버에서 수집 안 됨 → 판단 한계 명시
    if is_semiconductor:
        add("light",
            "[반도체/디스플레이] 재고 수준·수요처(모바일/서버/PC) 비중·CAPEX 방향성 데이터 없음 — "
            "업황 사이클 판단 보완 필요. 공식 IR·실적발표 자료 직접 확인 권장")

    severities = [i["severity"] for i in issues]
    max_severity = (
        "severe" if "severe" in severities
        else "medium" if "medium" in severities
        else "light" if "light" in severities
        else "ok"
    )

    severity_counts = {s: sum(1 for i in issues if i["severity"] == s) for s in ("severe", "medium", "light")}
    _log.info(
        "[validate_financials] 완료 | is_financial=%s is_bio=%s is_semiconductor=%s | max_severity=%s | "
        "severe=%d medium=%d light=%d | 비활성화=%s",
        is_financial, is_bio, is_semiconductor, max_severity,
        severity_counts["severe"], severity_counts["medium"], severity_counts["light"],
        disabled_rules or "없음",
    )

    # ── E값 품질 3단계 분류 ───────────────────────────────────────
    # financials dict에 직접 기록 → format_financials_for_prompt가 별도 인자 없이 읽을 수 있음
    quality = _classify_estimate_quality(
        financials,
        is_financial=is_financial,
        is_bio=is_bio,
        is_semiconductor=is_semiconductor,
    )
    financials["estimate_quality"] = quality  # 호출부 변경 없이 전달

    return {
        "issues": issues,
        "warnings": [i["message"] for i in issues],  # 하위 호환
        "estimate_periods": estimate_periods,
        "max_severity": max_severity,
        "has_issues": max_severity != "ok",
        "is_financial": is_financial,
        "is_bio": is_bio,
        "is_semiconductor": is_semiconductor,
        "disabled_rules": disabled_rules,
        "estimate_quality": quality,
    }


def get_quarter_meta(financials: dict) -> dict:
    """마지막 확정 분기와 다음 분기를 financials 데이터에서 추론."""
    quarterly = financials.get("quarterly", [])
    confirmed = [r for r in quarterly if "(E)" not in r.get("period", "")]
    if not confirmed:
        return {}
    last = confirmed[-1].get("period", "")  # e.g. "2024.12"
    try:
        year, month = int(last[:4]), int(last[5:7])
        month += 3
        if month > 12:
            month -= 12
            year += 1
        next_q = f"{year}.{month:02d}"
    except (ValueError, IndexError):
        next_q = ""
    return {"last_reported_quarter": last, "next_quarter": next_q}


def format_financials_for_prompt(financials: dict,
                                  is_financial: bool = False,
                                  is_bio: bool = False,
                                  is_semiconductor: bool = False) -> str:
    if not financials:
        return "실적 데이터 없음"

    unit = financials.get("unit", "억원")
    lines = ["※ 숫자 유형: [확정]=공시 확정치 | [추정]=증권사 컨센서스 추정치 | [추정-주의]=신뢰도 낮음 | N/A=데이터 없음"]

    annual = financials.get("annual", [])
    quarterly = financials.get("quarterly", [])
    confirmed_a_list = [r for r in annual if "(E)" not in r.get("period", "")]

    # ── E값 품질 분류 읽기 (validate_financials에서 기록됨) ───────────
    quality: dict = financials.get("estimate_quality", {})
    bad_e_periods       = {p for p, q in quality.items() if q["level"] == "error_candidate"}
    suspicious_e_periods = {p for p, q in quality.items() if q["level"] == "suspect"}

    # ── 연간 실적 표 ────────────────────────────────────────────────
    if annual:
        filtered_annual = [y for y in annual if y.get("period") not in bad_e_periods]

        if is_financial:
            # 원천 계정명이 있으면 동적으로 표기 (DART 데이터 기준)
            src_acct = next(
                (y.get("_source_account_rev") for y in confirmed_a_list if y.get("_source_account_rev")),
                None
            )
            rev_label = src_acct if src_acct else "영업수익"
            lines.append(
                f"\n【연간 실적 ({unit})】"
                f"※ 금융주: 매출액={rev_label}(이자+보험료+평가손익 gross 합산), 영업이익률 해석 주의"
            )
        elif is_semiconductor:
            lines.append(
                f"\n【연간 실적 ({unit})】"
                f"※ 반도체/디스플레이: 업황 사이클로 실적 급변 가능. E값 급등은 회복 기대 반영일 수 있음"
            )
        else:
            lines.append(f"\n【연간 실적 ({unit})】")

        def _period_label(y: dict) -> str:
            p = y.get("period", "")
            if "(E)" not in p:
                return f"{p} [확정]"
            tag = quality.get(p, {}).get("tag", "[추정]")
            return f"{p} {tag}"

        header = "항목".ljust(12) + " | " + " | ".join(_period_label(y) for y in filtered_annual)
        lines.append(header)

        def _cell(y: dict, field: str) -> str:
            val = y.get(field)
            return str(val) if val is not None else "N/A"

        if is_financial:
            # 금융주: ROE·순이익 우선, 매출액·영업이익률은 참고용으로 후순위
            for field in ["당기순이익", "ROE", "영업이익"]:
                row = field.ljust(12) + " | " + " | ".join(_cell(y, field) for y in filtered_annual)
                lines.append(row)
            # 매출액은 원천 계정명으로 표기 (DART 기준)
            src_acct = next(
                (y.get("_source_account_rev") for y in filtered_annual if y.get("_source_account_rev")),
                None
            )
            rev_col_label = f"{src_acct}(gross)" if src_acct else "영업수익(gross)"
            row = rev_col_label.ljust(16) + " | " + " | ".join(_cell(y, "매출액") for y in filtered_annual)
            lines.append(row)
            row = "영업이익률(비표준)".ljust(16) + " | " + " | ".join(_cell(y, "영업이익률") for y in filtered_annual)
            lines.append(row)
        else:
            for field in ["매출액", "영업이익", "당기순이익", "영업이익률", "ROE"]:
                row = field.ljust(12) + " | " + " | ".join(_cell(y, field) for y in filtered_annual)
                lines.append(row)

    # ── 분기 실적 표 ────────────────────────────────────────────────
    if quarterly:
        if is_financial:
            lines.append(f"\n【최근 분기 실적 ({unit})】※ 금융주: 영업이익률 해석 주의")
        else:
            lines.append(f"\n【최근 분기 실적 ({unit})】")

        def _q_period_label(y: dict) -> str:
            p = y.get("period", "")
            if "(E)" not in p:
                return f"{p} [확정]"
            return f"{p} {quality.get(p, {}).get('tag', '[추정]')}"

        header = "항목".ljust(12) + " | " + " | ".join(_q_period_label(y) for y in quarterly)
        lines.append(header)

        if is_financial:
            for field in ["당기순이익", "영업이익"]:
                row = field.ljust(12) + " | " + " | ".join(str(y.get(field) or "N/A") for y in quarterly)
                lines.append(row)
            q_src = next(
                (y.get("_source_account_rev") for y in quarterly if y.get("_source_account_rev")),
                None
            )
            q_rev_label = f"{q_src}(gross)" if q_src else "영업수익(gross)"
            row = q_rev_label.ljust(16) + " | " + " | ".join(str(y.get("매출액") or "N/A") for y in quarterly)
            lines.append(row)
            row = "영업이익률(비표준)".ljust(16) + " | " + " | ".join(str(y.get("영업이익률") or "N/A") for y in quarterly)
            lines.append(row)
        else:
            for field in ["매출액", "영업이익", "당기순이익", "영업이익률"]:
                row = field.ljust(12) + " | " + " | ".join(str(y.get(field) or "N/A") for y in quarterly)
                lines.append(row)

    return "\n".join(lines)
