"use client";
import React, { useState } from "react";
import {
  fetchRecommendations,
  RecommendRequest,
  RecommendResponse,
  RecommendationItem,
} from "@/lib/api";

// ── 태그 그룹 정의 ─────────────────────────────────────────────
type TagField = "prefer_sectors" | "prefer_styles" | "exclude_sectors" | "exclude_tags";

const TAG_GROUPS: {
  id: string;
  label: string;
  desc: string;
  field: TagField;
  activeClass: string;
  initial: number;
  all: string[];
}[] = [
  {
    id: "prefer_sectors",
    label: "관심 업종/사업",
    desc: "어떤 산업 종목을 보고 싶나요?",
    field: "prefer_sectors",
    activeClass: "bg-blue-600 text-white",
    initial: 8,
    all: [
      "반도체", "바이오", "2차전지", "방산", "금융", "통신", "자동차", "조선",
      "에너지", "건설", "화학", "철강", "로봇", "의료기기", "게임", "IT", "엔터", "뷰티", "항공", "해운",
    ],
  },
  {
    id: "prefer_styles",
    label: "선호 투자 스타일",
    desc: "어떤 성격의 종목을 원하나요?",
    field: "prefer_styles",
    activeClass: "bg-emerald-600 text-white",
    initial: 7,
    all: ["성장주", "배당", "저PER", "방어주", "턴어라운드", "수출", "내수"],
  },
  {
    id: "exclude_sectors",
    label: "제외 업종",
    desc: "아예 보고 싶지 않은 업종이 있나요?",
    field: "exclude_sectors",
    activeClass: "bg-orange-700 text-white",
    initial: 8,
    all: [
      "반도체", "바이오", "2차전지", "방산", "금융", "통신", "자동차", "조선",
      "에너지", "건설", "화학", "철강", "로봇", "의료기기", "게임", "IT", "엔터", "뷰티", "항공", "해운",
    ],
  },
  {
    id: "exclude_tags",
    label: "제외 조건",
    desc: "빼고 싶은 종목 성격이 있나요?",
    field: "exclude_tags",
    activeClass: "bg-red-700 text-white",
    initial: 6,
    all: ["변동성높음", "고PER", "경기민감", "블록체인", "암호화폐", "적자기업", "거래대금 적음", "실적 데이터 부족"],
  },
];

// ── 태그 그룹 블록 ─────────────────────────────────────────────
function TagGroupBlock({
  group,
  selected,
  expanded,
  onToggleTag,
  onToggleExpand,
}: {
  group: (typeof TAG_GROUPS)[number];
  selected: string[];
  expanded: boolean;
  onToggleTag: (tag: string, field: TagField) => void;
  onToggleExpand: () => void;
}) {
  const visible = expanded ? group.all : group.all.slice(0, group.initial);
  const hasMore = group.all.length > group.initial;

  return (
    <div className="space-y-2">
      <div>
        <p className="text-sm font-medium text-white">{group.label}</p>
        <p className="text-xs text-gray-500">{group.desc}</p>
      </div>
      <div className="flex flex-wrap gap-2">
        {visible.map((tag) => (
          <button
            key={tag}
            type="button"
            onClick={() => onToggleTag(tag, group.field)}
            className={`px-3 py-1 rounded-full text-sm transition ${
              selected.includes(tag)
                ? group.activeClass
                : "bg-gray-800 text-gray-400 hover:bg-gray-700"
            }`}
          >
            {tag}
          </button>
        ))}
        {hasMore && (
          <button
            type="button"
            onClick={onToggleExpand}
            className="px-3 py-1 rounded-full text-sm text-gray-500 hover:text-gray-300 transition border border-gray-700"
          >
            {expanded ? "접기" : `+${group.all.length - group.initial}개 더보기`}
          </button>
        )}
      </div>
    </div>
  );
}

// ── 추천 결과 카드 ──────────────────────────────────────────────
const BUY_TYPE_STYLE: Record<RecommendationItem["buy_type"], string> = {
  "즉시 검토형": "bg-emerald-900 text-emerald-300",
  "확인 후 매수형": "bg-yellow-900 text-yellow-300",
  "관찰 우선형": "bg-gray-700 text-gray-400",
};

function RecommendCard({ item, onAddToPaper }: { item: RecommendationItem; onAddToPaper?: (ticker: string, name: string, market: string) => void }) {
  const [showSplit, setShowSplit] = useState(false);
  const tierColor =
    item.tier === 1 ? "text-yellow-400" : item.tier === 2 ? "text-blue-400" : "text-gray-500";

  return (
    <div className="bg-gray-900 border border-gray-800 rounded-2xl p-5 space-y-3">
      <div className="flex justify-between items-start">
        <div>
          <div className="flex items-center gap-2">
            <span className="font-bold text-white text-lg">{item.name}</span>
            <span className="text-gray-500 text-sm">{item.ticker}</span>
          </div>
          <div className="flex gap-2 mt-1">
            <span className="text-xs bg-gray-800 text-gray-400 px-2 py-0.5 rounded">{item.sector}</span>
            <span className={`text-xs font-medium ${tierColor}`}>Tier {item.tier}</span>
          </div>
        </div>
        <div className="text-right space-y-1.5">
          <div>
            <span className={`text-xs px-2 py-1 rounded-full font-medium ${BUY_TYPE_STYLE[item.buy_type]}`}>
              {item.buy_type}
            </span>
          </div>
          <div className="text-sm text-gray-400">
            점수 <span className="text-white font-semibold">{item.score}</span>
          </div>
          {onAddToPaper && (
            <button
              onClick={() => onAddToPaper(item.ticker, item.name, item.ticker.match(/^\d{6}$/) ? "KR" : "US")}
              className="text-xs bg-indigo-800 hover:bg-indigo-700 text-indigo-200 px-2 py-1 rounded transition whitespace-nowrap"
            >
              📥 모의투자
            </button>
          )}
        </div>
      </div>

      {item.weight_pct != null && item.amount_krw != null && (
        <div className="flex gap-4 text-sm text-gray-400">
          <span>비중 <span className="text-white font-medium">{item.weight_pct}%</span></span>
          <span>배분 <span className="text-white font-medium">{item.amount_krw.toLocaleString()}원</span></span>
        </div>
      )}

      {item.reasons.length > 0 && (
        <div>
          <p className="text-xs text-emerald-400 mb-1">추천 이유</p>
          <ul className="space-y-0.5">
            {item.reasons.map((r, i) => (
              <li key={i} className="text-sm text-gray-300 flex gap-1.5">
                <span className="text-emerald-500 shrink-0">•</span>{r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {item.risks.length > 0 && (
        <div>
          <p className="text-xs text-red-400 mb-1">리스크</p>
          <ul className="space-y-0.5">
            {item.risks.map((r, i) => (
              <li key={i} className="text-sm text-gray-400 flex gap-1.5">
                <span className="text-red-500 shrink-0">•</span>{r}
              </li>
            ))}
          </ul>
        </div>
      )}

      {item.split.length > 0 && (
        <div>
          <button
            onClick={() => setShowSplit((v) => !v)}
            className="text-xs text-purple-400 hover:text-purple-300 transition"
          >
            {showSplit ? "▲ 분할매수 숨기기" : "▼ 분할매수 계획 보기"}
          </button>
          {showSplit && (
            <div className="mt-2 space-y-1">
              {item.split.map((s, i) => (
                <div key={i} className="flex justify-between text-xs bg-gray-800 rounded px-3 py-1.5">
                  <span className="text-gray-300 font-medium">{s.차수}</span>
                  <span className="text-gray-300">{s.금액.toLocaleString()}원</span>
                  <span className="text-gray-500">{s.조건}</span>
                </div>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

// ── 메인 컴포넌트 ──────────────────────────────────────────────
const DEFAULT_FORM: RecommendRequest = {
  amount: 1000000,
  risk: "neutral",
  period: "medium",
  market: "ALL",
  recommend_count: null,
  prefer_sectors: [],
  prefer_styles: [],
  exclude_sectors: [],
  exclude_tags: [],
  holdings: [],
};

interface RecommendPanelProps {
  onAddToPaper?: (ticker: string, name: string, market: string) => void;
}

export default function RecommendPanel({ onAddToPaper }: RecommendPanelProps = {}) {
  const [form, setForm] = useState<RecommendRequest>(DEFAULT_FORM);
  const [amountStr, setAmountStr] = useState("1000000");
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const [result, setResult] = useState<RecommendResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [showExcluded, setShowExcluded] = useState(false);
  const [showReport, setShowReport] = useState(false);

  function toggleTag(tag: string, field: TagField) {
    setForm((prev) => {
      const current = prev[field];
      const updated = current.includes(tag)
        ? current.filter((t) => t !== tag)
        : [...current, tag];
      return { ...prev, [field]: updated };
    });
  }

  function toggleGroupExpand(id: string) {
    setExpandedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setLoading(true);
    setError("");
    setResult(null);
    setShowExcluded(false);
    setShowReport(false);
    try {
      const res = await fetchRecommendations(form);
      setResult(res);
    } catch {
      setError("추천 요청 중 오류가 발생했습니다.");
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="max-w-2xl mx-auto space-y-6">
      <form onSubmit={submit} className="bg-gray-900 rounded-2xl p-6 border border-gray-800 space-y-6">
        <h2 className="text-base font-semibold text-white">투자 조건 설정</h2>

        {/* 투자금액 */}
        <div>
          <label className="text-sm text-gray-400 block mb-1">투자금액</label>
          <div className="relative">
            <input
              type="number"
              value={amountStr}
              onChange={(e) => {
                setAmountStr(e.target.value);
                const n = parseInt(e.target.value, 10);
                if (!isNaN(n) && n > 0) setForm((prev) => ({ ...prev, amount: n }));
              }}
              min={1}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-4 py-2 outline-none focus:border-blue-500 text-sm pr-8"
              placeholder="예: 1000000"
            />
            <span className="absolute right-3 top-1/2 -translate-y-1/2 text-gray-500 text-sm pointer-events-none">
              원
            </span>
          </div>
          {form.amount >= 10000 && (
            <p className="text-xs text-gray-500 mt-1">{form.amount.toLocaleString()}원</p>
          )}
        </div>

        {/* 성향 / 기간 / 시장 / 추천 개수 */}
        <div className="grid grid-cols-2 gap-3">
          <div>
            <label className="text-sm text-gray-400 block mb-1">투자 성향</label>
            <select
              value={form.risk}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, risk: e.target.value as RecommendRequest["risk"] }))
              }
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm"
            >
              <option value="conservative">보수적</option>
              <option value="neutral">중립</option>
              <option value="aggressive">공격적</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400 block mb-1">투자 기간</label>
            <select
              value={form.period}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, period: e.target.value as RecommendRequest["period"] }))
              }
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm"
            >
              <option value="short">단기 (3개월↓)</option>
              <option value="medium">중기 (3개월~1년)</option>
              <option value="long">장기 (1년↑)</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400 block mb-1">시장</label>
            <select
              value={form.market}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, market: e.target.value as RecommendRequest["market"] }))
              }
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm"
            >
              <option value="KR">국내 (KR)</option>
              <option value="US">해외 (US)</option>
              <option value="ALL">전체 (KR+US)</option>
            </select>
          </div>
          <div>
            <label className="text-sm text-gray-400 block mb-1">추천 종목 개수</label>
            <select
              value={form.recommend_count ?? "auto"}
              onChange={(e) => {
                const v = e.target.value;
                setForm((prev) => ({
                  ...prev,
                  recommend_count: v === "auto" ? null : parseInt(v, 10),
                }));
              }}
              className="w-full bg-gray-800 border border-gray-600 rounded-lg px-3 py-2 text-sm"
            >
              <option value="auto">자동</option>
              <option value="1">1개</option>
              <option value="2">2개</option>
              <option value="3">3개</option>
              <option value="4">4개</option>
              <option value="5">5개</option>
            </select>
          </div>
        </div>

        <div className="border-t border-gray-800" />

        {/* 태그 4그룹 */}
        {TAG_GROUPS.map((group) => (
          <TagGroupBlock
            key={group.id}
            group={group}
            selected={form[group.field]}
            expanded={expandedGroups.has(group.id)}
            onToggleTag={toggleTag}
            onToggleExpand={() => toggleGroupExpand(group.id)}
          />
        ))}

        {/* 선택 요약 */}
        {(form.prefer_sectors.length > 0 || form.prefer_styles.length > 0 || form.exclude_sectors.length > 0 || form.exclude_tags.length > 0) && (
          <div className="bg-gray-800 rounded-xl px-4 py-3 text-xs text-gray-400 space-y-1">
            {form.prefer_sectors.length > 0 && (
              <p>관심 업종: <span className="text-white">{form.prefer_sectors.join(", ")}</span></p>
            )}
            {form.prefer_styles.length > 0 && (
              <p>선호 스타일: <span className="text-blue-300">{form.prefer_styles.join(", ")}</span></p>
            )}
            {form.exclude_sectors.length > 0 && (
              <p>제외 업종: <span className="text-orange-400">{form.exclude_sectors.join(", ")}</span></p>
            )}
            {form.exclude_tags.length > 0 && (
              <p>제외 조건: <span className="text-red-400">{form.exclude_tags.join(", ")}</span></p>
            )}
          </div>
        )}

        <button
          type="submit"
          disabled={loading}
          className="w-full bg-gradient-to-r from-purple-600 to-blue-600 hover:opacity-90 disabled:opacity-50 py-3 rounded-xl font-semibold text-lg transition"
        >
          {loading ? "🤖 후보 분석 중... (최대 30초)" : "🔍 AI 투자 후보 추천받기"}
        </button>
      </form>

      {error && <p className="text-center text-red-400">{error}</p>}

      {result && (
        <div className="space-y-4">
          <div className="bg-gray-900 rounded-xl px-4 py-3 border border-gray-800 text-sm text-gray-400">
            <span className="text-white font-medium">조건: </span>
            {result.summary}
          </div>

          {result.recommendations.length === 0 ? (
            <div className="text-center text-gray-500 py-12">
              <p className="text-lg mb-1">조건에 맞는 후보가 없습니다.</p>
              <p className="text-sm">{result.notes}</p>
            </div>
          ) : (
            <>
              <h2 className="font-bold text-white">추천 종목 ({result.recommendations.length})</h2>
              {result.recommendations.map((item) => (
                <RecommendCard key={item.ticker} item={item} onAddToPaper={onAddToPaper} />
              ))}

              {result.excluded.length > 0 && (
                <div>
                  <button
                    onClick={() => setShowExcluded((v) => !v)}
                    className="text-sm text-gray-500 hover:text-gray-300 transition mb-2"
                  >
                    {showExcluded ? "▲" : "▼"} 제외된 후보 ({result.excluded.length})
                  </button>
                  {showExcluded && (
                    <div className="space-y-2">
                      {result.excluded.map((item) => (
                        <div
                          key={item.ticker}
                          className="bg-gray-900 border border-gray-800 rounded-xl px-4 py-3 flex justify-between items-start text-sm"
                        >
                          <div>
                            <span className="text-gray-400 font-medium">{item.name}</span>
                            <span className="text-gray-600 ml-2 text-xs">{item.ticker}</span>
                            <p className="text-gray-600 text-xs mt-0.5">{item.exclusion_reason}</p>
                          </div>
                          <span className="text-gray-600 text-xs shrink-0 ml-3">점수 {item.score}</span>
                        </div>
                      ))}
                    </div>
                  )}
                </div>
              )}

              {result.report && (
                <div className="bg-gray-900 rounded-2xl border border-purple-800">
                  <button
                    onClick={() => setShowReport((v) => !v)}
                    className="w-full flex justify-between items-center px-5 py-4 font-semibold text-purple-300"
                  >
                    <span>AI 추천 리포트</span>
                    <span className="text-gray-400 text-sm">
                      {showReport ? "▲ 접기" : "▼ 펼치기"}
                    </span>
                  </button>
                  {showReport && (
                    <div className="px-5 pb-5 text-sm text-gray-200 whitespace-pre-wrap leading-relaxed border-t border-gray-800 pt-4">
                      {result.report}
                    </div>
                  )}
                </div>
              )}

              <p className="text-xs text-gray-600 text-center pb-4">{result.notes}</p>
            </>
          )}
        </div>
      )}
    </div>
  );
}
